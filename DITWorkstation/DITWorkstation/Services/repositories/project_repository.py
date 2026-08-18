"""Project persistence operations, including recoverable deletion."""
import sqlite3
import uuid
from datetime import datetime

from DITWorkstation.Models import OperationResult, OperationStatus, Project
from DITWorkstation.Utils import logger

from .base_repository import BaseRepository
from .field_registry import PROJECT_FIELDS, build_update_clause


class ProjectRepository(BaseRepository):
    """Owns project CRUD while delegating shared recovery helpers to the facade."""

    def create(
        self,
        name: str,
        description: str = "",
        base_path: str = "",
        workspace_id: str | None = None,
    ) -> Project:
        if workspace_id is None or workspace_id == "default":
            workspace_id = self._database.get_or_create_default_workspace().workspace_id
        project = Project(
            project_id=str(uuid.uuid4())[:8],
            name=name,
            description=description,
            base_path=base_path,
            workspace_id=workspace_id,
        )
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO projects "
                "(project_id, name, description, base_path, workspace_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project.project_id,
                    project.name,
                    project.description,
                    project.base_path,
                    project.workspace_id,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )
            logger.info(f"创建项目: {project.project_id} - {project.name} (workspace={workspace_id})")
        return project

    def list_all(self, workspace_id: str | None = None) -> list[Project]:
        with self._connection() as conn:
            if workspace_id:
                rows = conn.execute(
                    "SELECT * FROM projects WHERE workspace_id = ? ORDER BY created_at DESC",
                    (workspace_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [self._row_to_project(row) for row in rows]

    def get(self, project_id: str) -> Project | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        return self._row_to_project(row) if row else None

    def update_result(self, project_id: str, **kwargs) -> OperationResult:
        sql, params = build_update_clause(
            PROJECT_FIELDS, "projects", "project_id", project_id, **kwargs
        )
        if not sql:
            return OperationResult(OperationStatus.INVALID, "没有可更新的项目字段")
        try:
            with self._transaction() as conn:
                if conn.execute(
                    "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone() is None:
                    return OperationResult(OperationStatus.NOT_FOUND, f"项目不存在: {project_id}")
                conn.execute(sql, params)
                self._database._record_operation_in_transaction(
                    conn, "更新项目", project_id=project_id,
                    object_type="project", object_id=project_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=1)
        except sqlite3.Error as exc:
            logger.error(f"更新项目失败 {project_id}: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def delete_result(self, project_id: str, retention_days: int = 30) -> OperationResult:
        """Soft-delete a project and all associated metadata in one transaction."""
        try:
            with self._transaction() as conn:
                project = conn.execute(
                    "SELECT * FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone()
                if project is None:
                    return OperationResult(OperationStatus.NOT_FOUND, f"项目不存在: {project_id}")
                asset_rows = conn.execute(
                    "SELECT * FROM media_assets WHERE project_id = ?", (project_id,)
                ).fetchall()
                snapshot = {
                    "project": dict(project),
                    "assets": [self._database._asset_snapshot(conn, row) for row in asset_rows],
                    "shooting_logs": [dict(row) for row in conn.execute(
                        "SELECT * FROM shooting_logs WHERE project_id = ?", (project_id,)
                    )],
                    "backup_job_ids": [row["job_id"] for row in conn.execute(
                        "SELECT job_id FROM backup_jobs WHERE project_id = ?", (project_id,)
                    )],
                }
                recovery_id = self._database._store_recycle_snapshot(
                    conn, "project", project_id, project_id, snapshot, retention_days,
                )
                conn.execute("UPDATE backup_jobs SET project_id = NULL WHERE project_id = ?", (project_id,))
                self._database._delete_asset_rows(conn, [row["asset_id"] for row in asset_rows])
                conn.execute("DELETE FROM shooting_logs WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
                self._database._record_operation_in_transaction(
                    conn, "删除项目", "项目记录已移入回收站", project_id,
                    object_type="project", object_id=project_id, recovery_id=recovery_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=1, recovery_id=recovery_id)
        except sqlite3.Error as exc:
            logger.error(f"软删除项目失败 {project_id}: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def delete_legacy(self, project_id: str) -> None:
        """Compatibility path for historical permanent project deletion."""
        with self._transaction() as conn:
            conn.execute("UPDATE backup_jobs SET project_id = NULL WHERE project_id = ?", (project_id,))
            conn.execute(
                "DELETE FROM asset_tags WHERE asset_id IN "
                "(SELECT asset_id FROM media_assets WHERE project_id = ?)",
                (project_id,),
            )
            conn.execute("DELETE FROM media_assets WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM shooting_logs WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            logger.info(f"删除项目: {project_id}")

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> Project:
        workspace_id = row["workspace_id"] if "workspace_id" in row.keys() else None
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"],
            base_path=row["base_path"],
            workspace_id=workspace_id,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
