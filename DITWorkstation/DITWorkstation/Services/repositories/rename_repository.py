"""Persistent rename-history operations used by safe rollback."""
import json
import sqlite3
import uuid

from DITWorkstation.Models import OperationResult, OperationStatus
from DITWorkstation.Utils import now_local

from .base_repository import BaseRepository


class RenameRepository(BaseRepository):
    """Owns rename history while delegating shared audit writes to the facade."""

    def create(self, mappings: list[tuple[str, str]], project_id: str | None = None) -> OperationResult:
        if not mappings:
            return OperationResult(OperationStatus.INVALID, "没有可记录的重命名映射")
        rename_id = str(uuid.uuid4())
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO rename_history (rename_id, project_id, mappings_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (rename_id, project_id, json.dumps(mappings, ensure_ascii=False), now_local().isoformat()),
                )
                self._database._record_operation_in_transaction(
                    conn, "文件重命名", f"成功 {len(mappings)} 个", project_id,
                    object_type="rename", object_id=rename_id, recovery_id=rename_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=len(mappings), recovery_id=rename_id)
        except sqlite3.Error as exc:
            return OperationResult(OperationStatus.ERROR, str(exc))

    def get(self, rename_id: str) -> OperationResult:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM rename_history WHERE rename_id = ?", (rename_id,)).fetchone()
        if row is None:
            return OperationResult(OperationStatus.NOT_FOUND, f"重命名记录不存在: {rename_id}")
        if row["reverted_at"]:
            return OperationResult(OperationStatus.CONFLICT, "该重命名已经回退")
        try:
            mappings = [tuple(item) for item in json.loads(row["mappings_json"])]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return OperationResult(OperationStatus.ERROR, f"重命名记录损坏: {exc}")
        return OperationResult(OperationStatus.SUCCESS, value={
            "rename_id": row["rename_id"], "project_id": row["project_id"], "mappings": mappings,
        })

    def mark_reverted(self, rename_id: str) -> OperationResult:
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "UPDATE rename_history SET reverted_at = ? WHERE rename_id = ? AND reverted_at IS NULL",
                    (now_local().isoformat(), rename_id),
                )
                if cursor.rowcount != 1:
                    return OperationResult(OperationStatus.CONFLICT, "重命名记录已回退或不存在")
                self._database._record_operation_in_transaction(
                    conn, "回退文件重命名", project_id=None,
                    object_type="rename", object_id=rename_id, recovery_id=rename_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=1, recovery_id=rename_id)
        except sqlite3.Error as exc:
            return OperationResult(OperationStatus.ERROR, str(exc))
