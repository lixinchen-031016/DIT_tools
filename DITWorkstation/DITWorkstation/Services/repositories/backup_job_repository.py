"""Backup-job persistence and legacy row deserialization."""
import json
from datetime import datetime

from DITWorkstation.Utils import logger

from .base_repository import BaseRepository


class BackupJobRepository(BaseRepository):
    """Owns backup job rows; file-to-asset association remains in AssetRepository."""

    def save(self, job, project_id: str | None = None) -> bool:
        if project_id:
            with self._connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone()
            if exists is None:
                logger.warning(f"备份作业关联项目不存在，改为未关联: {project_id}")
                project_id = None
        targets_json = json.dumps([
            {
                "path": target.path,
                "name": target.name,
                "status": target.status.value,
                "total_files": target.total_files,
                "completed_files": target.completed_files,
                "total_bytes": target.total_bytes,
                "copied_bytes": target.copied_bytes,
                "verified": target.verified,
                "error_message": target.error_message,
                "failed_files": list(target.failed_files),
                "pending_files": list(target.pending_files),
            }
            for target in job.targets
        ], ensure_ascii=False)
        failed_files_json = json.dumps([
            {"path": target.path, "files": list(target.failed_files)}
            for target in job.targets if target.failed_files
        ], ensure_ascii=False)
        snapshots_json = json.dumps(
            list(getattr(job, "_files_cache", []) or []), ensure_ascii=False, default=str,
        )
        try:
            with self._transaction() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO backup_jobs
                       (job_id, project_id, source_path, algorithm, total_files, total_bytes,
                        status, targets_json, failed_files_json, snapshot_json, created_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job.job_id, project_id, job.source_path, job.algorithm.value,
                     job.total_files, job.total_bytes, job.status.value, targets_json,
                     failed_files_json, snapshots_json, job.created_at.isoformat(),
                     job.completed_at.isoformat() if job.completed_at else None),
                )
                logger.info(f"持久化备份作业: {job.job_id} (project={project_id})")
            return True
        except Exception as exc:
            logger.error(f"持久化备份作业失败 {job.job_id}: {exc}")
            return False

    def get(self, job_id: str) -> dict | None:
        for raw in self.list_all():
            if raw["job_id"] == job_id:
                return raw
        return None

    def list_all(self, project_id: str | None = None) -> list[dict]:
        with self._connection() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM backup_jobs WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM backup_jobs ORDER BY created_at DESC").fetchall()
        results = []
        for row in rows:
            try:
                targets = json.loads(row["targets_json"]) if row["targets_json"] else []
            except (json.JSONDecodeError, TypeError):
                targets = []
            for target in targets:
                target.setdefault("failed_files", [])
                target.setdefault("pending_files", [])
            completed_at = None
            if row["completed_at"]:
                try:
                    completed_at = datetime.fromisoformat(row["completed_at"])
                except ValueError:
                    pass
            try:
                failed = json.loads(row["failed_files_json"]) if row["failed_files_json"] else []
            except (json.JSONDecodeError, TypeError):
                failed = []
            try:
                snapshots = json.loads(row["snapshot_json"]) if row["snapshot_json"] else []
            except (json.JSONDecodeError, TypeError):
                snapshots = []
            results.append({
                "job_id": row["job_id"], "project_id": row["project_id"],
                "source_path": row["source_path"], "algorithm": row["algorithm"],
                "total_files": row["total_files"], "total_bytes": row["total_bytes"],
                "status": row["status"], "targets": targets,
                "created_at": self._database._parse_datetime(row["created_at"]),
                "completed_at": completed_at,
                "failed_files_by_target": {item["path"]: item.get("files", []) for item in failed},
                "file_snapshots": snapshots,
            })
        return results
