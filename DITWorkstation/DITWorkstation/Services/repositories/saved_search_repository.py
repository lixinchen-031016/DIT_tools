"""保存搜索 / 智能集合持久化操作。

保存搜索 = 命名 + 检索条件 JSON + 可选的范围（工作区/项目）；
智能集合 = is_smart=1 的保存搜索（检索时自动套用条件）。

"""

import json
import sqlite3
import uuid
from typing import ClassVar

from DITWorkstation.Utils import logger, now_local_iso

from .base_repository import BaseRepository


class SavedSearchRepository(BaseRepository):
    """Owns saved_search CRUD."""

    # 与 Views 检索表单字段保持一致（未知字段静默忽略）
    FILTER_FIELDS: ClassVar[set[str]] = {
        "project_id",
        "scene",
        "shot",
        "file_type",
        "date_from",
        "date_to",
        "keyword",
        "log_id",
        "rating",
        "tag",
        "taken_from",
        "taken_to",
    }

    def create(
        self,
        name: str,
        filters: dict,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        is_smart: bool = False,
    ) -> dict:
        search_id = str(uuid.uuid4())[:12]
        now = now_local_iso()
        clean_filters = {
            k: v
            for k, v in filters.items()
            if k in self.FILTER_FIELDS and v not in (None, "", [])
        }
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO saved_searches "
                "(search_id, name, filters_json, workspace_id, project_id, is_smart, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    search_id,
                    name,
                    json.dumps(clean_filters, ensure_ascii=False),
                    workspace_id,
                    project_id,
                    int(bool(is_smart)),
                    now,
                    now,
                ),
            )
        logger.info(f"创建保存搜索: {search_id} - {name}")
        created = self.get(search_id)
        if created is None:
            raise RuntimeError(f"保存搜索创建后无法读取: {search_id}")
        return created

    def list(
        self,
        limit: int = 50,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM saved_searches"
        clauses, params = [], []
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get(self, search_id: str) -> dict | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM saved_searches WHERE search_id = ?", (search_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def update(self, search_id: str, **kwargs) -> bool:
        updates, params = [], []
        if "name" in kwargs:
            updates.append("name = ?")
            params.append(str(kwargs["name"]))
        if "filters" in kwargs:
            updates.append("filters_json = ?")
            clean = {
                k: v
                for k, v in kwargs["filters"].items()
                if k in self.FILTER_FIELDS and v not in (None, "", [])
            }
            params.append(json.dumps(clean, ensure_ascii=False))
        if "is_smart" in kwargs:
            updates.append("is_smart = ?")
            params.append(int(bool(kwargs["is_smart"])))
        if "workspace_id" in kwargs:
            updates.append("workspace_id = ?")
            params.append(kwargs["workspace_id"] or None)
        if "project_id" in kwargs:
            updates.append("project_id = ?")
            params.append(kwargs["project_id"] or None)
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.append(now_local_iso())
        params.append(search_id)
        try:
            with self._transaction() as conn:
                conn.execute(
                    f"UPDATE saved_searches SET {', '.join(updates)} WHERE search_id = ?",
                    params,
                )
                return True
        except sqlite3.Error as exc:
            logger.error(f"更新保存搜索失败 {search_id}: {exc}")
            return False

    def delete(self, search_id: str) -> bool:
        try:
            with self._transaction() as conn:
                conn.execute(
                    "DELETE FROM saved_searches WHERE search_id = ?", (search_id,)
                )
                logger.info(f"删除保存搜索: {search_id}")
                return True
        except sqlite3.Error as exc:
            logger.error(f"删除保存搜索失败 {search_id}: {exc}")
            return False

    @staticmethod
    def _row_to_dict(row) -> dict:
        try:
            filters = json.loads(row["filters_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            filters = {}
        return {
            "search_id": row["search_id"],
            "name": row["name"],
            "filters": filters,
            "workspace_id": row["workspace_id"],
            "project_id": row["project_id"],
            "is_smart": bool(row["is_smart"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
