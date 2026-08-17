"""Shooting-log and audit-log persistence operations."""
from datetime import datetime
import sqlite3
import uuid
from typing import Optional

from DITWorkstation.Models import OperationStatus, ShootingLog
from DITWorkstation.Utils import logger

from .base_repository import BaseRepository


class LogRepository(BaseRepository):
    """Owns shooting logs and operation-audit log queries."""

    def create_shooting(self, log: ShootingLog) -> ShootingLog:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO shooting_logs "
                "(log_id, project_id, scene, shot, take, description, camera, lens, iso, "
                "aperture, shutter_speed, notes, file_paths, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    log.log_id, log.project_id, log.scene, log.shot, log.take,
                    log.description, log.camera, log.lens, log.iso, log.aperture,
                    log.shutter_speed, log.notes, "|".join(log.file_paths),
                    log.created_at.isoformat(),
                ),
            )
            logger.info(f"创建拍摄日志: {log.log_id} - {log.scene}/{log.shot}/{log.take}")
        return log

    def list_shooting(self, project_id: str) -> list[ShootingLog]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM shooting_logs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [self._row_to_shooting(row) for row in rows]

    def get_shooting(self, log_id: str) -> Optional[ShootingLog]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM shooting_logs WHERE log_id = ?", (log_id,)).fetchone()
        return self._row_to_shooting(row) if row else None

    def update_shooting(self, log: ShootingLog) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE shooting_logs SET scene=?, shot=?, take=?, description=?, camera=?, "
                "lens=?, iso=?, aperture=?, shutter_speed=?, notes=?, file_paths=? WHERE log_id=?",
                (
                    log.scene, log.shot, log.take, log.description, log.camera,
                    log.lens, log.iso, log.aperture, log.shutter_speed, log.notes,
                    "|".join(log.file_paths), log.log_id,
                ),
            )
            logger.info(f"更新拍摄日志: {log.log_id}")

    def delete_shooting(self, log_id: str) -> None:
        try:
            with self._transaction() as conn:
                conn.execute(
                    "UPDATE media_assets SET log_id = NULL, scene = '', shot = '' WHERE log_id = ?",
                    (log_id,),
                )
                conn.execute("DELETE FROM shooting_logs WHERE log_id = ?", (log_id,))
                logger.info(f"删除拍摄日志: {log_id}（已级联清空关联素材的 scene/shot）")
        except Exception as exc:
            logger.error(f"删除拍摄日志失败 {log_id}: {exc}")
            raise

    def create_shooting_with_assets(
        self, log: ShootingLog, asset_ids: list[str], sync_scene_shot: bool = False
    ) -> ShootingLog:
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO shooting_logs "
                    "(log_id, project_id, scene, shot, take, description, camera, lens, iso, "
                    "aperture, shutter_speed, notes, file_paths, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        log.log_id, log.project_id, log.scene, log.shot, log.take,
                        log.description, log.camera, log.lens, log.iso, log.aperture,
                        log.shutter_speed, log.notes, "|".join(log.file_paths),
                        log.created_at.isoformat(),
                    ),
                )
                for asset_id in asset_ids:
                    if sync_scene_shot:
                        conn.execute(
                            "UPDATE media_assets SET log_id = ?, scene = ?, shot = ? WHERE asset_id = ?",
                            (log.log_id, log.scene, log.shot, asset_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE media_assets SET log_id = ? WHERE asset_id = ?",
                            (log.log_id, asset_id),
                        )
                logger.info(f"创建日志并关联素材: {log.log_id} - {len(asset_ids)} 个素材")
        except Exception as exc:
            logger.error(f"create_log_with_assets 失败: {exc}")
            raise
        return log

    @staticmethod
    def record_in_transaction(
        conn, event: str, detail: str = "", project_id: Optional[str] = None,
        status: str = OperationStatus.SUCCESS.value, object_type: str = "",
        object_id: str = "", recovery_id: str = "",
    ) -> str:
        log_id = str(uuid.uuid4())[:8]
        conn.execute(
            "INSERT INTO operation_logs "
            "(log_id, project_id, event, detail, result_status, object_type, object_id, recovery_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                log_id, project_id, event, detail, status, object_type, object_id,
                recovery_id, datetime.now().isoformat(),
            ),
        )
        return log_id

    def record(
        self, event: str, detail: str = "", project_id: Optional[str] = None,
        status: str = OperationStatus.SUCCESS.value, object_type: str = "",
        object_id: str = "", recovery_id: str = "",
    ) -> bool:
        try:
            with self._transaction() as conn:
                self.record_in_transaction(
                    conn, event, detail, project_id, status, object_type, object_id, recovery_id,
                )
            return True
        except sqlite3.Error as exc:
            logger.error(f"记录操作日志失败: {exc}")
            return False

    def list_recent(
        self, limit: int = 10, project_id: Optional[str] = None, event: str = "",
        status: str = "", object_type: str = "", date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 5000))
        clauses, values = [], []
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        if event:
            clauses.append("event LIKE ?")
            values.append(f"%{event}%")
        if status:
            clauses.append("result_status = ?")
            values.append(status)
        if object_type:
            clauses.append("object_type = ?")
            values.append(object_type)
        if date_from:
            clauses.append("created_at >= ?")
            values.append(date_from.isoformat())
        if date_to:
            clauses.append("created_at <= ?")
            values.append(date_to.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM operation_logs{where} ORDER BY created_at DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        results = []
        for row in rows:
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except ValueError:
                created_at = None
            results.append({
                "log_id": row["log_id"],
                "project_id": row["project_id"],
                "event": row["event"],
                "detail": row["detail"],
                "status": row["result_status"] if "result_status" in row.keys() else "success",
                "object_type": row["object_type"] if "object_type" in row.keys() else "",
                "object_id": row["object_id"] if "object_id" in row.keys() else "",
                "recovery_id": row["recovery_id"] if "recovery_id" in row.keys() else "",
                "created_at": created_at,
            })
        return results

    @staticmethod
    def _row_to_shooting(row: sqlite3.Row) -> ShootingLog:
        return ShootingLog(
            log_id=row["log_id"],
            project_id=row["project_id"],
            scene=row["scene"],
            shot=row["shot"],
            take=row["take"],
            description=row["description"],
            camera=row["camera"],
            lens=row["lens"],
            iso=row["iso"],
            aperture=row["aperture"],
            shutter_speed=row["shutter_speed"],
            notes=row["notes"],
            file_paths=row["file_paths"].split("|") if row["file_paths"] else [],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
