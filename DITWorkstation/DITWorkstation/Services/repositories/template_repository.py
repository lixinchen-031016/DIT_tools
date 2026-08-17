"""Project and backup template persistence operations."""
from datetime import datetime
import json
import sqlite3
import uuid
from typing import Optional

from DITWorkstation.Models import BackupTemplate, ChecksumAlgorithm, ProjectTemplate
from DITWorkstation.Utils import logger

from .base_repository import BaseRepository
from .field_registry import (
    BACKUP_TEMPLATE_FIELDS,
    PROJECT_TEMPLATE_FIELDS,
    build_update_clause,
)


class TemplateRepository(BaseRepository):
    """Owns project-template and backup-template CRUD."""

    def create_project(
        self, name: str, description: str = "", base_path: str = "", notes: str = ""
    ) -> ProjectTemplate:
        template = ProjectTemplate(
            template_id=str(uuid.uuid4())[:8],
            name=name,
            description=description,
            base_path=base_path,
            notes=notes,
        )
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO project_templates "
                "(template_id, name, description, base_path, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    template.template_id,
                    template.name,
                    template.description,
                    template.base_path,
                    template.notes,
                    template.created_at.isoformat(),
                    template.updated_at.isoformat(),
                ),
            )
            logger.info(f"创建项目模板: {template.template_id} - {template.name}")
        return template

    def list_projects(self) -> list[ProjectTemplate]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM project_templates ORDER BY created_at DESC").fetchall()
        return [self._row_to_project_template(row) for row in rows]

    def get_project(self, template_id: str) -> Optional[ProjectTemplate]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM project_templates WHERE template_id = ?", (template_id,)
            ).fetchone()
        return self._row_to_project_template(row) if row else None

    def update_project(self, template_id: str, **kwargs) -> bool:
        sql, params = build_update_clause(
            PROJECT_TEMPLATE_FIELDS, "project_templates", "template_id", template_id, **kwargs
        )
        if not sql:
            return False
        try:
            with self._transaction() as conn:
                conn.execute(sql, params)
                logger.info(f"更新项目模板: {template_id}")
                return True
        except sqlite3.Error as exc:
            logger.error(f"更新项目模板失败 {template_id}: {exc}")
            return False

    def delete_project(self, template_id: str) -> bool:
        try:
            with self._transaction() as conn:
                conn.execute("DELETE FROM project_templates WHERE template_id = ?", (template_id,))
                logger.info(f"删除项目模板: {template_id}")
                return True
        except sqlite3.Error as exc:
            logger.error(f"删除项目模板失败 {template_id}: {exc}")
            return False

    def create_backup(
        self,
        name: str,
        target_paths: list[str],
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        verify_after_copy: bool = True,
        description: str = "",
    ) -> BackupTemplate:
        template = BackupTemplate(
            template_id=str(uuid.uuid4())[:8],
            name=name,
            target_paths=list(target_paths),
            algorithm=algorithm,
            verify_after_copy=verify_after_copy,
            description=description,
        )
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO backup_templates "
                "(template_id, name, target_paths, algorithm, verify_after_copy, "
                "description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    template.template_id,
                    template.name,
                    json.dumps(template.target_paths, ensure_ascii=False),
                    template.algorithm.value,
                    int(template.verify_after_copy),
                    template.description,
                    template.created_at.isoformat(),
                    template.updated_at.isoformat(),
                ),
            )
        return template

    def list_backups(self) -> list[BackupTemplate]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM backup_templates ORDER BY created_at DESC").fetchall()
        return [self._row_to_backup_template(row) for row in rows]

    def get_backup(self, template_id: str) -> Optional[BackupTemplate]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM backup_templates WHERE template_id = ?", (template_id,)
            ).fetchone()
        return self._row_to_backup_template(row) if row else None

    def update_backup(self, template_id: str, **kwargs) -> bool:
        sql, params = build_update_clause(
            BACKUP_TEMPLATE_FIELDS, "backup_templates", "template_id", template_id, **kwargs
        )
        if not sql:
            return False
        with self._transaction() as conn:
            return conn.execute(sql, params).rowcount > 0

    def delete_backup(self, template_id: str) -> bool:
        with self._transaction() as conn:
            return conn.execute(
                "DELETE FROM backup_templates WHERE template_id = ?", (template_id,)
            ).rowcount > 0

    @staticmethod
    def _row_to_project_template(row: sqlite3.Row) -> ProjectTemplate:
        return ProjectTemplate(
            template_id=row["template_id"],
            name=row["name"],
            description=row["description"],
            base_path=row["base_path"],
            notes=row["notes"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_backup_template(row: sqlite3.Row) -> BackupTemplate:
        try:
            paths = json.loads(row["target_paths"] or "[]")
        except (TypeError, ValueError):
            paths = []
        try:
            algorithm = ChecksumAlgorithm(row["algorithm"] or ChecksumAlgorithm.XXHASH64.value)
        except ValueError:
            algorithm = ChecksumAlgorithm.XXHASH64
        return BackupTemplate(
            template_id=row["template_id"],
            name=row["name"],
            target_paths=paths,
            algorithm=algorithm,
            verify_after_copy=bool(row["verify_after_copy"]),
            description=row["description"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
