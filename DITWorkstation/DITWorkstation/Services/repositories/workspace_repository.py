"""Workspace persistence operations."""
import sqlite3
import uuid
from datetime import datetime

from DITWorkstation.Models import Workspace
from DITWorkstation.Utils import logger, now_local

from .base_repository import BaseRepository
from .field_registry import WORKSPACE_FIELDS, build_update_clause


class WorkspaceRepository(BaseRepository):
    """Owns workspace CRUD while sharing the database service infrastructure."""

    def create(self, name: str, path: str = "", description: str = "") -> Workspace:
        workspace = Workspace(
            workspace_id=str(uuid.uuid4())[:8],
            name=name,
            path=path,
            description=description,
        )
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO workspaces (workspace_id, name, path, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    workspace.workspace_id,
                    workspace.name,
                    workspace.path,
                    workspace.description,
                    workspace.created_at.isoformat(),
                    workspace.updated_at.isoformat(),
                ),
            )
            logger.info(f"创建工作区: {workspace.workspace_id} - {workspace.name} (path={workspace.path})")
        return workspace

    def list_all(self) -> list[Workspace]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM workspaces ORDER BY created_at DESC").fetchall()
        return [self._row_to_workspace(row) for row in rows]

    def get(self, workspace_id: str) -> Workspace | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
        return self._row_to_workspace(row) if row else None

    def get_or_create_default(self) -> Workspace:
        now = now_local().isoformat()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO workspaces "
                "(workspace_id, name, path, description, created_at, updated_at) "
                "VALUES ('default', ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id) DO NOTHING",
                ("默认工作区", "", "默认工作区", now, now),
            )
        workspace = self.get("default")
        if workspace is None:
            raise RuntimeError("创建默认工作区后仍无法读取")
        return workspace

    def update(self, workspace_id: str, **kwargs) -> bool:
        sql, params = build_update_clause(
            WORKSPACE_FIELDS, "workspaces", "workspace_id", workspace_id, **kwargs
        )
        if not sql:
            return False
        try:
            with self._transaction() as conn:
                cursor = conn.execute(sql, params)
                if cursor.rowcount != 1:
                    logger.warning(f"工作区不存在，未更新: {workspace_id}")
                    return False
                logger.info(f"更新工作区: {workspace_id}")
                return True
        except sqlite3.Error as exc:
            logger.error(f"更新工作区失败 {workspace_id}: {exc}")
            return False

    def delete(self, workspace_id: str, reassign_to: str | None = None) -> bool:
        if workspace_id == "default":
            logger.warning("拒绝删除默认工作区")
            return False
        try:
            with self._transaction() as conn:
                default_exists = conn.execute(
                    "SELECT 1 FROM workspaces WHERE workspace_id = 'default'"
                ).fetchone()
                if default_exists is None:
                    now = now_local().isoformat()
                    conn.execute(
                        "INSERT INTO workspaces "
                        "(workspace_id, name, path, description, created_at, updated_at) "
                        "VALUES ('default', ?, '', ?, ?, ?)",
                        ("默认工作区", "默认工作区", now, now),
                    )
                if reassign_to:
                    conn.execute(
                        "UPDATE projects SET workspace_id = ? WHERE workspace_id = ?",
                        (reassign_to, workspace_id),
                    )
                else:
                    conn.execute(
                        "UPDATE projects SET workspace_id = 'default' WHERE workspace_id = ?",
                        (workspace_id,),
                    )
                conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,))
                logger.info(f"删除工作区: {workspace_id}（项目已归入 default）")
                return True
        except sqlite3.Error as exc:
            logger.error(f"删除工作区失败 {workspace_id}: {exc}")
            return False

    @staticmethod
    def _row_to_workspace(row: sqlite3.Row) -> Workspace:
        return Workspace(
            workspace_id=row["workspace_id"],
            name=row["name"],
            path=row["path"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
