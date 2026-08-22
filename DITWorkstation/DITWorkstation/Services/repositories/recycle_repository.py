"""Recycle-bin snapshot persistence and restoration."""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta

from DITWorkstation.Models import OperationResult, OperationStatus
from DITWorkstation.Utils import logger, now_local

from .base_repository import BaseRepository


class RecycleRepository(BaseRepository):
    """Owns recycle-bin rows while reusing facade audit and FTS helpers."""

    def store_snapshot(
        self,
        conn,
        entity_type: str,
        entity_id: str,
        project_id: str | None,
        snapshot: dict,
        retention_days: int = 30,
    ) -> str:
        recycle_id = str(uuid.uuid4())
        now = now_local()
        conn.execute(
            "INSERT INTO recycle_bin "
            "(recycle_id, entity_type, entity_id, project_id, snapshot_json, deleted_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                recycle_id,
                entity_type,
                entity_id,
                project_id,
                json.dumps(snapshot, ensure_ascii=False),
                now.isoformat(),
                (now + timedelta(days=retention_days)).isoformat(),
            ),
        )
        return recycle_id

    def list_items(self, project_id: str | None = None) -> list[dict]:
        with self._connection() as conn:
            query = "SELECT recycle_id, entity_type, entity_id, project_id, deleted_at, expires_at FROM recycle_bin"
            params = []
            if project_id:
                query += " WHERE project_id = ?"
                params.append(project_id)
            query += " ORDER BY deleted_at DESC"
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    @staticmethod
    def insert_snapshot_row(conn, table: str, values: dict) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        selected = {key: value for key, value in values.items() if key in columns}
        names = list(selected)
        placeholders = ", ".join("?" for _ in names)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
            [selected[name] for name in names],
        )

    def restore_asset_snapshot(self, conn, snapshot: dict) -> None:
        asset = snapshot["asset"]
        self.insert_snapshot_row(conn, "media_assets", asset)
        for tag in snapshot.get("tags", []):
            conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                (asset["asset_id"], tag),
            )
        self._database._refresh_asset_fts(conn, asset["asset_id"])

    def restore_item(self, recycle_id: str) -> OperationResult:
        try:
            with self._transaction() as conn:
                item = conn.execute(
                    "SELECT * FROM recycle_bin WHERE recycle_id = ?", (recycle_id,)
                ).fetchone()
                if item is None:
                    return OperationResult(
                        OperationStatus.NOT_FOUND, f"回收站项不存在: {recycle_id}"
                    )
                snapshot = json.loads(item["snapshot_json"])
                if item["entity_type"] == "asset":
                    asset = snapshot["asset"]
                    if conn.execute(
                        "SELECT 1 FROM media_assets WHERE asset_id = ?",
                        (asset["asset_id"],),
                    ).fetchone():
                        return OperationResult(
                            OperationStatus.CONFLICT, "素材 ID 已被重新使用"
                        )
                    if (
                        conn.execute(
                            "SELECT 1 FROM projects WHERE project_id = ?",
                            (asset["project_id"],),
                        ).fetchone()
                        is None
                    ):
                        return OperationResult(
                            OperationStatus.CONFLICT, "素材所属项目不存在，无法恢复"
                        )
                    self.restore_asset_snapshot(conn, snapshot)
                    project_id, object_id = asset["project_id"], asset["asset_id"]
                elif item["entity_type"] == "project":
                    project = snapshot["project"]
                    if conn.execute(
                        "SELECT 1 FROM projects WHERE project_id = ?",
                        (project["project_id"],),
                    ).fetchone():
                        return OperationResult(
                            OperationStatus.CONFLICT, "项目 ID 已被重新使用"
                        )
                    asset_ids = [
                        asset["asset"]["asset_id"]
                        for asset in snapshot.get("assets", [])
                    ]
                    if asset_ids:
                        placeholders = ", ".join("?" for _ in asset_ids)
                        if conn.execute(
                            f"SELECT 1 FROM media_assets WHERE asset_id IN ({placeholders}) LIMIT 1",
                            asset_ids,
                        ).fetchone():
                            return OperationResult(
                                OperationStatus.CONFLICT, "项目素材 ID 已被重新使用"
                            )
                    workspace_id = project.get("workspace_id")
                    if (
                        workspace_id
                        and conn.execute(
                            "SELECT 1 FROM workspaces WHERE workspace_id = ?",
                            (workspace_id,),
                        ).fetchone()
                        is None
                    ):
                        project["workspace_id"] = "default"
                        now = now_local().isoformat()
                        conn.execute(
                            "INSERT OR IGNORE INTO workspaces "
                            "(workspace_id, name, path, description, created_at, updated_at) "
                            "VALUES ('default', ?, '', ?, ?, ?)",
                            ("默认工作区", "默认工作区", now, now),
                        )
                    self.insert_snapshot_row(conn, "projects", project)
                    for log in snapshot.get("shooting_logs", []):
                        self.insert_snapshot_row(conn, "shooting_logs", log)
                    for asset_snapshot in snapshot.get("assets", []):
                        self.restore_asset_snapshot(conn, asset_snapshot)
                    for job_id in snapshot.get("backup_job_ids", []):
                        conn.execute(
                            "UPDATE backup_jobs SET project_id = ? WHERE job_id = ? AND project_id IS NULL",
                            (project["project_id"], job_id),
                        )
                    project_id = object_id = project["project_id"]
                else:
                    return OperationResult(
                        OperationStatus.INVALID, "不支持的回收站实体类型"
                    )
                conn.execute(
                    "DELETE FROM recycle_bin WHERE recycle_id = ?", (recycle_id,)
                )
                self._database._record_operation_in_transaction(
                    conn,
                    "恢复回收站项目",
                    "已恢复数据库记录",
                    project_id,
                    object_type=item["entity_type"],
                    object_id=object_id,
                    recovery_id=recycle_id,
                )
            return OperationResult(
                OperationStatus.SUCCESS, affected_count=1, recovery_id=recycle_id
            )
        except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
            logger.error(f"恢复回收站项失败 {recycle_id}: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def restore_all(self) -> OperationResult:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT recycle_id, entity_type FROM recycle_bin "
                "ORDER BY CASE entity_type WHEN 'project' THEN 0 ELSE 1 END, deleted_at ASC"
            ).fetchall()
        if not rows:
            return OperationResult(OperationStatus.INVALID, "回收站为空")
        failed, restored = [], 0
        for row in rows:
            result = self.restore_item(row["recycle_id"])
            if result:
                restored += result.affected_count
            else:
                failed.append(
                    {"recycle_id": row["recycle_id"], "message": result.message}
                )
        message = "" if not failed else f"{len(failed)} 项因冲突或数据错误未恢复"
        return OperationResult(
            OperationStatus.SUCCESS,
            message,
            value={"failed": failed},
            affected_count=restored,
        )

    def empty(self) -> OperationResult:
        try:
            with self._transaction() as conn:
                cursor = conn.execute("DELETE FROM recycle_bin")
            return OperationResult(
                OperationStatus.SUCCESS, affected_count=max(cursor.rowcount, 0)
            )
        except sqlite3.Error as exc:
            logger.error(f"清空回收站失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def cleanup_expired(self, now: datetime | None = None) -> OperationResult:
        now = now or now_local()
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "DELETE FROM recycle_bin WHERE expires_at <= ?", (now.isoformat(),)
                )
            return OperationResult(
                OperationStatus.SUCCESS, affected_count=max(cursor.rowcount, 0)
            )
        except sqlite3.Error as exc:
            logger.error(f"清理回收站失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))
