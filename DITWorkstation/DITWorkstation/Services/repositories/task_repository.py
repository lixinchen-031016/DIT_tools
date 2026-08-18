"""Background task history and storage-health snapshot persistence."""
from datetime import datetime
import json
import sqlite3
import uuid
from typing import Optional

from DITWorkstation.Utils import logger

from .base_repository import BaseRepository


class TaskRepository(BaseRepository):
    """Owns operational records consumed by task and project-health views."""

    def create(
        self, task_name: str, project_id: Optional[str] = None, *, state: str = "queued",
        parameters: Optional[dict] = None, recovery_info: Optional[dict] = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO task_history "
                "(task_id, task_name, project_id, state, parameters_json, recovery_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, task_name, project_id, state,
                 json.dumps(parameters or {}, ensure_ascii=False, default=str),
                 json.dumps(recovery_info or {}, ensure_ascii=False, default=str), now),
            )
        return task_id

    def update(
        self, task_id: str, state: str, *, output: Optional[dict] = None,
        error_summary: str = "", recovery_info: Optional[dict] = None,
    ) -> bool:
        terminal = {"completed", "failed", "cancelled", "recoverable"}
        try:
            with self._transaction() as conn:
                current = conn.execute(
                    "SELECT recovery_json FROM task_history WHERE task_id = ?", (task_id,)
                ).fetchone()
                if current is None:
                    return False
                merged_recovery = json.loads(current["recovery_json"] or "{}")
                if recovery_info:
                    merged_recovery.update(recovery_info)
                conn.execute(
                    "UPDATE task_history SET state = ?, output_json = ?, error_summary = ?, "
                    "recovery_json = ?, started_at = COALESCE(started_at, ?), "
                    "completed_at = CASE WHEN ? THEN ? ELSE completed_at END WHERE task_id = ?",
                    (state, json.dumps(output or {}, ensure_ascii=False, default=str), error_summary,
                     json.dumps(merged_recovery, ensure_ascii=False, default=str), datetime.now().isoformat(),
                     int(state in terminal), datetime.now().isoformat(), task_id),
                )
            return True
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"更新任务历史失败 {task_id}: {exc}")
            return False

    def list_all(self, project_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        with self._connection() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM task_history WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("parameters_json", "recovery_json", "output_json"):
                try:
                    item[key[:-5]] = json.loads(item.get(key) or "{}")
                except (TypeError, json.JSONDecodeError):
                    item[key[:-5]] = {}
            result.append(item)
        return result

    def prune_per_project(self, limit: int = 200) -> int:
        """每个项目仅保留最近 limit 条任务，返回删除条数。"""
        limit = max(1, int(limit))
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM task_history WHERE task_id IN ("
                "SELECT task_id FROM ("
                "SELECT task_id, ROW_NUMBER() OVER ("
                "PARTITION BY project_id ORDER BY created_at DESC, rowid DESC"
                ") AS row_number FROM task_history"
                ") WHERE row_number > ?"
                ")",
                (limit,),
            )
            return cursor.rowcount

    def record_storage_health(
        self, project_id: Optional[str], target_path: str, total_bytes: int, free_bytes: int,
    ) -> bool:
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO storage_health_snapshots "
                    "(snapshot_id, project_id, target_path, total_bytes, free_bytes, captured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), project_id, target_path, int(total_bytes), int(free_bytes),
                     datetime.now().isoformat()),
                )
            return True
        except sqlite3.Error as exc:
            logger.warning(f"记录存储健康快照失败: {exc}")
            return False

    def list_storage_health(self, project_id: Optional[str], limit: int = 60) -> list[dict]:
        limit = max(1, min(int(limit), 5000))
        with self._connection() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM storage_health_snapshots WHERE project_id = ? "
                    "ORDER BY captured_at DESC LIMIT ?", (project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM storage_health_snapshots ORDER BY captured_at DESC LIMIT ?", (limit,)
                ).fetchall()
        result = [dict(row) for row in reversed(rows)]
        for item in result:
            item["captured_at"] = self._database._parse_datetime(item.get("captured_at"))
        return result
