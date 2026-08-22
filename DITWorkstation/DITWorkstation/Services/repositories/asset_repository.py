"""Read-side media asset persistence operations."""

import sqlite3
from datetime import datetime
from pathlib import Path

from DITWorkstation.Models import MediaAsset, OperationResult, OperationStatus
from DITWorkstation.Utils import logger, normalize_path

from .base_repository import BaseRepository
from .field_registry import MEDIA_ASSET_FIELDS, build_update_clause


class AssetRepository(BaseRepository):
    """Owns basic media-asset CRUD while recovery flows remain in the facade."""

    _INSERT_SQL = """INSERT INTO media_assets
       (asset_id, project_id, file_path, file_name, file_size, file_type,
        asset_type, checksum_algorithm, checksum_value, scene, shot, take, date_imported,
        date_taken, camera_make, camera_model, backup_locations, log_id,
        is_working_copy, original_path, width, height, duration_seconds,
        lens_model, focal_length, video_metadata, rating, tags, notes)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    @staticmethod
    def _asset_values(asset: MediaAsset) -> tuple:
        return (
            asset.asset_id,
            asset.project_id,
            asset.file_path,
            asset.file_name,
            asset.file_size,
            asset.file_type,
            asset.asset_type,
            asset.checksum_algorithm,
            asset.checksum_value,
            asset.scene,
            asset.shot,
            asset.take,
            asset.date_imported.isoformat(),
            asset.date_taken.isoformat() if asset.date_taken else None,
            asset.camera_make,
            asset.camera_model,
            "|".join(asset.backup_locations),
            asset.log_id,
            1 if asset.is_working_copy else 0,
            asset.original_path,
            asset.width,
            asset.height,
            asset.duration_seconds,
            asset.lens_model,
            asset.focal_length,
            asset.video_metadata,
            asset.rating,
            asset.tags,
            asset.notes,
        )

    @staticmethod
    def _normalize_datetime_bound(
        value: str | None, *, upper: bool = False
    ) -> str | None:
        """Normalize UI date bounds to the ISO format stored in SQLite."""
        if not value:
            return value
        normalized = str(value).replace(" ", "T", 1)
        if upper and len(normalized) == 10:
            normalized += "T23:59:59"
        return normalized

    def sync_tags(self, conn, asset_id: str, tags: str) -> None:
        """Synchronize the denormalized comma-separated tags with the relation table."""
        conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
        for tag in self._database._split_tags(tags):
            conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                (asset_id, tag),
            )

    @staticmethod
    def _insert_asset(conn, asset: MediaAsset) -> None:
        conn.execute(AssetRepository._INSERT_SQL, AssetRepository._asset_values(asset))

    def create(self, asset: MediaAsset) -> MediaAsset:
        with self._transaction() as conn:
            self._insert_asset(conn, asset)
            self.sync_tags(conn, asset.asset_id, asset.tags)
            self._database._sync_asset_fts(
                conn,
                asset.asset_id,
                file_name=asset.file_name,
                scene=asset.scene,
                shot=asset.shot,
                notes=asset.notes,
                tags=asset.tags,
            )
            logger.info(f"添加素材资产: {asset.asset_id} - {asset.file_name}")
        return asset

    def create_batch(self, assets: list[MediaAsset]) -> int:
        if not assets:
            return 0
        try:
            with self._transaction() as conn:
                conn.executemany(
                    self._INSERT_SQL, [self._asset_values(asset) for asset in assets]
                )
                tag_rows = [
                    (asset.asset_id, tag)
                    for asset in assets
                    for tag in self._database._split_tags(asset.tags)
                ]
                if tag_rows:
                    conn.executemany(
                        "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                        tag_rows,
                    )
                for asset in assets:
                    self._database._sync_asset_fts(
                        conn,
                        asset.asset_id,
                        file_name=asset.file_name,
                        scene=asset.scene,
                        shot=asset.shot,
                        notes=asset.notes,
                        tags=asset.tags,
                    )
                logger.info(f"批量添加素材资产: {len(assets)} 个")
                return len(assets)
        except Exception as exc:
            logger.error(f"批量添加素材资产失败: {exc}")
            return 0

    def update_result(self, asset_id: str, **kwargs) -> OperationResult:
        sql, params = build_update_clause(
            MEDIA_ASSET_FIELDS,
            "media_assets",
            "asset_id",
            asset_id,
            touch_updated_at=False,
            **kwargs,
        )
        if not sql:
            return OperationResult(OperationStatus.INVALID, "没有可更新的素材字段")
        try:
            with self._transaction() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = ?", (asset_id,)
                ).fetchone()
                if exists is None:
                    return OperationResult(
                        OperationStatus.NOT_FOUND, f"素材不存在: {asset_id}"
                    )
                conn.execute(sql, params)
                if "tags" in kwargs:
                    self.sync_tags(conn, asset_id, kwargs.get("tags") or "")
                self._database._refresh_asset_fts(conn, asset_id)
                self._database._record_operation_in_transaction(
                    conn,
                    "更新素材",
                    project_id=None,
                    object_type="asset",
                    object_id=asset_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=1)
        except sqlite3.Error as exc:
            logger.error(f"更新素材资产失败 {asset_id}: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def delete_legacy(self, asset_id: str) -> bool:
        """Permanently delete a single asset for legacy internal callers."""
        try:
            with self._transaction() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = ?", (asset_id,)
                ).fetchone()
                if exists is None:
                    logger.warning(f"素材资产不存在，未删除: {asset_id}")
                    return False
                self.delete_rows(conn, [asset_id])
                logger.info(f"删除素材资产: {asset_id}")
                return True
        except sqlite3.Error as exc:
            logger.error(f"删除素材资产失败 {asset_id}: {exc}")
            return False

    def delete_many_legacy(self, asset_ids: list[str]) -> int:
        """Permanently delete a batch of assets for legacy internal callers."""
        unique_ids = list(dict.fromkeys(asset_id for asset_id in asset_ids if asset_id))
        if not unique_ids:
            return 0
        deleted = 0
        try:
            with self._transaction() as conn:
                for chunk in self._chunks(unique_ids):
                    placeholders = ", ".join("?" for _ in chunk)
                    if self._database._fts_available:
                        conn.execute(
                            f"DELETE FROM media_assets_fts WHERE asset_id IN ({placeholders})",
                            chunk,
                        )
                    conn.execute(
                        f"DELETE FROM asset_tags WHERE asset_id IN ({placeholders})",
                        chunk,
                    )
                    cursor = conn.execute(
                        f"DELETE FROM media_assets WHERE asset_id IN ({placeholders})",
                        chunk,
                    )
                    deleted += max(cursor.rowcount, 0)
        except sqlite3.Error as exc:
            logger.error(f"批量删除素材资产失败: {exc}")
            return 0
        logger.info(f"批量删除素材资产: 成功 {deleted}/{len(asset_ids)}")
        return deleted

    def snapshot(self, conn, row: sqlite3.Row) -> dict:
        tags = [
            item["tag"]
            for item in conn.execute(
                "SELECT tag FROM asset_tags WHERE asset_id = ? ORDER BY tag",
                (row["asset_id"],),
            )
        ]
        return {"asset": dict(row), "tags": tags}

    def delete_rows(self, conn, asset_ids: list[str]) -> None:
        for chunk in self._chunks(asset_ids):
            placeholders = ", ".join("?" for _ in chunk)
            if self._database._fts_available:
                conn.execute(
                    f"DELETE FROM media_assets_fts WHERE asset_id IN ({placeholders})",
                    chunk,
                )
            conn.execute(
                f"DELETE FROM asset_tags WHERE asset_id IN ({placeholders})", chunk
            )
            conn.execute(
                f"DELETE FROM media_assets WHERE asset_id IN ({placeholders})", chunk
            )

    def delete_result(
        self, asset_ids: list[str], retention_days: int = 30
    ) -> OperationResult:
        """Soft-delete assets, preserving recovery snapshots in the caller's transaction domain."""
        unique_ids = list(dict.fromkeys(asset_id for asset_id in asset_ids if asset_id))
        if not unique_ids:
            return OperationResult(OperationStatus.INVALID, "未提供素材 ID")
        try:
            with self._transaction() as conn:
                rows = []
                for chunk in self._chunks(unique_ids):
                    placeholders = ", ".join("?" for _ in chunk)
                    rows.extend(
                        conn.execute(
                            f"SELECT * FROM media_assets WHERE asset_id IN ({placeholders})",
                            chunk,
                        ).fetchall()
                    )
                if not rows:
                    return OperationResult(OperationStatus.NOT_FOUND, "素材不存在")
                recovery_ids = []
                for row in rows:
                    recovery_ids.append(
                        self._database._store_recycle_snapshot(
                            conn,
                            "asset",
                            row["asset_id"],
                            row["project_id"],
                            self.snapshot(conn, row),
                            retention_days,
                        )
                    )
                self.delete_rows(conn, [row["asset_id"] for row in rows])
                for row, recovery_id in zip(rows, recovery_ids):
                    self._database._record_operation_in_transaction(
                        conn,
                        "删除素材",
                        "素材记录已移入回收站",
                        row["project_id"],
                        object_type="asset",
                        object_id=row["asset_id"],
                        recovery_id=recovery_id,
                    )
            return OperationResult(
                OperationStatus.SUCCESS,
                affected_count=len(rows),
                recovery_id=recovery_ids[0],
                value=recovery_ids,
            )
        except sqlite3.Error as exc:
            logger.error(f"软删除素材失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def relink_result(
        self,
        project_id: str,
        updates: list[tuple[str, str, str, int]],
    ) -> OperationResult:
        """Atomically apply validated relink paths and record the associated audit entries."""
        if not updates:
            return OperationResult(OperationStatus.INVALID, "没有重新链接更新")
        try:
            with self._transaction() as conn:
                if (
                    conn.execute(
                        "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
                    ).fetchone()
                    is None
                ):
                    return OperationResult(
                        OperationStatus.NOT_FOUND, f"项目不存在: {project_id}"
                    )
                seen_paths = set()
                for asset_id, new_path, _name, _size in updates:
                    if new_path in seen_paths:
                        return OperationResult(
                            OperationStatus.CONFLICT,
                            f"多个素材选择了同一路径: {new_path}",
                        )
                    seen_paths.add(new_path)
                    row = conn.execute(
                        "SELECT asset_id FROM media_assets WHERE asset_id = ? AND project_id = ?",
                        (asset_id, project_id),
                    ).fetchone()
                    if row is None:
                        return OperationResult(
                            OperationStatus.NOT_FOUND,
                            f"素材不存在或不属于项目: {asset_id}",
                        )
                    conflict = conn.execute(
                        "SELECT asset_id FROM media_assets WHERE project_id = ? AND file_path = ? AND asset_id != ?",
                        (project_id, new_path, asset_id),
                    ).fetchone()
                    if conflict is not None:
                        return OperationResult(
                            OperationStatus.CONFLICT, f"路径已关联其他素材: {new_path}"
                        )
                for asset_id, new_path, name, size in updates:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ?, file_name = ?, file_size = ? WHERE asset_id = ?",
                        (new_path, name, size, asset_id),
                    )
                    self._database._refresh_asset_fts(conn, asset_id)
                    self._database._record_operation_in_transaction(
                        conn,
                        "重新链接素材",
                        new_path,
                        project_id,
                        object_type="asset",
                        object_id=asset_id,
                    )
            return OperationResult(OperationStatus.SUCCESS, affected_count=len(updates))
        except sqlite3.Error as exc:
            logger.error(f"重新链接素材失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def list_all(
        self,
        project_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[MediaAsset]:
        query = (
            "SELECT * FROM media_assets WHERE project_id = ? "
            "ORDER BY date_imported DESC, asset_id DESC"
        )
        params: list[str | int] = [project_id]
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None and offset > 0:
                query += " OFFSET ?"
                params.append(offset)
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_asset(row) for row in rows]

    def iter_project(self, project_id: str, batch_size: int = 500):
        """Yield project assets in stable order without materializing the full result set."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM media_assets WHERE project_id = ? "
                "ORDER BY date_imported DESC, asset_id DESC",
                (project_id,),
            )
            while True:
                rows = cursor.fetchmany(max(1, batch_size))
                if not rows:
                    return
                for row in rows:
                    yield self._row_to_asset(row)

    def count_project(self, project_id: str) -> int:
        with self._connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ?", (project_id,)
            ).fetchone()[0]

    def capture_timeline(
        self,
        project_id: str | None = None,
        *,
        granularity: str = "day",
        limit: int = 366,
    ) -> list[dict]:
        """按拍摄日期聚合素材，返回轻量时间线记录而非完整素材对象。"""
        if granularity not in {"day", "week"}:
            raise ValueError("时间线粒度必须是 day 或 week")
        limit = max(1, min(int(limit), 2000))
        period_sql = (
            "substr(date_taken, 1, 10)"
            if granularity == "day"
            else "strftime('%Y-W%W', date_taken)"
        )
        where = "WHERE date_taken IS NOT NULL AND date_taken != ''"
        params: list = []
        if project_id:
            where += " AND project_id = ?"
            params.append(project_id)
        query = (
            f"SELECT {period_sql} AS period, COUNT(*) AS asset_count, "
            "COALESCE(SUM(file_size), 0) AS total_size, "
            "MIN(date_taken) AS first_taken, MAX(date_taken) AS last_taken "
            f"FROM media_assets {where} GROUP BY {period_sql} ORDER BY period LIMIT ?"
        )
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_page(
        self,
        project_id: str,
        page_size: int = 500,
        cursor: tuple[str, str] | None = None,
    ) -> tuple[list[MediaAsset], tuple[str, str] | None]:
        """Return a keyset page ordered by ``date_imported, asset_id`` descending."""
        page_size = max(1, page_size)
        query = "SELECT * FROM media_assets WHERE project_id = ?"
        params: list[str | int] = [project_id]
        if cursor:
            query += " AND (date_imported < ? OR (date_imported = ? AND asset_id < ?))"
            params.extend([cursor[0], cursor[0], cursor[1]])
        query += " ORDER BY date_imported DESC, asset_id DESC LIMIT ?"
        params.append(page_size + 1)
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        assets = [self._row_to_asset(row) for row in rows]
        next_cursor = None
        if has_next and rows:
            next_cursor = (rows[-1]["date_imported"], rows[-1]["asset_id"])
        return assets, next_cursor

    def get(self, asset_id: str) -> MediaAsset | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return self._row_to_asset(row) if row else None

    def missing_file_ids(self, project_id: str) -> list[str]:
        return [
            asset.asset_id
            for asset in self.iter_project(project_id)
            if not asset.file_path or not Path(asset.file_path).exists()
        ]

    def exists_by_path(self, project_id: str, file_path: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND file_path = ?",
                (project_id, normalize_path(file_path)),
            ).fetchone()
        return row[0] > 0

    def existing_paths(self, project_id: str, file_paths: list[str]) -> set[str]:
        if not file_paths:
            return set()
        keys = {normalize_path(file_path) for file_path in file_paths}
        existing = set()
        with self._connection() as conn:
            for chunk in self._chunks(sorted(keys)):
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    "SELECT file_path FROM media_assets "
                    f"WHERE project_id = ? AND file_path IN ({placeholders})",
                    (project_id, *chunk),
                ).fetchall()
                existing.update(row["file_path"] for row in rows)
        return existing

    def log_id_by_path(self, file_path: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT log_id FROM media_assets WHERE file_path = ?",
                (normalize_path(file_path),),
            ).fetchone()
        return row["log_id"] if row else None

    def exists_by_checksum(self, project_id: str, checksum_value: str) -> bool:
        if not checksum_value:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND checksum_value = ?",
                (project_id, checksum_value),
            ).fetchone()
        return row[0] > 0

    def find_duplicates(self) -> list[dict]:
        with self._connection() as conn:
            groups = conn.execute(
                """SELECT checksum_value, checksum_algorithm, COUNT(*) AS c
                   FROM media_assets WHERE checksum_value != ''
                   GROUP BY checksum_value, checksum_algorithm HAVING COUNT(*) > 1
                   ORDER BY COUNT(*) DESC"""
            ).fetchall()
            results = []
            for group in groups:
                rows = conn.execute(
                    """SELECT * FROM media_assets
                       WHERE checksum_value = ? AND checksum_algorithm = ?
                       ORDER BY project_id, date_imported""",
                    (group["checksum_value"], group["checksum_algorithm"]),
                ).fetchall()
                results.append(
                    {
                        "checksum_value": group["checksum_value"],
                        "algorithm": group["checksum_algorithm"],
                        "count": group["c"],
                        "assets": [self._row_to_asset(row) for row in rows],
                    }
                )
        return results

    def update_path(
        self, asset_id: str, new_path: str, new_name: str | None = None
    ) -> bool:
        try:
            with self._transaction() as conn:
                if new_name:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ?, file_name = ? WHERE asset_id = ?",
                        (new_path, new_name, asset_id),
                    )
                else:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ? WHERE asset_id = ?",
                        (new_path, asset_id),
                    )
                self._database._refresh_asset_fts(conn, asset_id)
                logger.info(f"更新素材路径: {asset_id}")
            return True
        except sqlite3.Error as exc:
            logger.error(f"更新素材路径失败 {asset_id}: {exc}")
            return False

    def update_path_by_old_path(
        self,
        old_path: str,
        new_path: str,
        new_name: str | None = None,
    ) -> bool:
        old_key, new_key = normalize_path(old_path), normalize_path(new_path)
        try:
            with self._transaction() as conn:
                row = conn.execute(
                    "SELECT asset_id FROM media_assets WHERE file_path = ?", (old_key,)
                ).fetchone()
                if not row:
                    return False
                if new_name:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ?, file_name = ? WHERE asset_id = ?",
                        (new_key, new_name, row["asset_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ? WHERE asset_id = ?",
                        (new_key, row["asset_id"]),
                    )
                self._database._refresh_asset_fts(conn, row["asset_id"])
                logger.info(f"按旧路径同步重命名: {old_key} -> {new_key}")
            return True
        except sqlite3.Error as exc:
            logger.error(f"按旧路径同步重命名失败 {old_key}: {exc}")
            return False

    def _filter_clause(
        self,
        project_id: str | None = None,
        scene: str | None = None,
        shot: str | None = None,
        file_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        keyword: str | None = None,
        log_id: str | None = None,
        rating: int | None = None,
        tag: str | None = None,
        taken_from: str | None = None,
        taken_to: str | None = None,
    ) -> tuple[str, list]:
        query, params = " WHERE 1=1", []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if scene:
            query += " AND scene LIKE ?"
            params.append(f"%{scene}%")
        if shot:
            query += " AND shot LIKE ?"
            params.append(f"%{shot}%")
        if file_type:
            query += " AND file_type = ?"
            params.append(file_type)
        if date_from:
            query += " AND date_imported >= ?"
            params.append(date_from)
        if date_to:
            query += " AND date_imported <= ?"
            params.append(date_to)
        if taken_from:
            query += " AND date_taken >= ?"
            params.append(self._normalize_datetime_bound(taken_from))
        if taken_to:
            query += " AND date_taken <= ?"
            params.append(self._normalize_datetime_bound(taken_to, upper=True))
        fts_keyword = self._database._fts_query(keyword or "")
        if keyword and not (self._database._fts_available and fts_keyword):
            query += (
                " AND (file_name LIKE ? OR scene LIKE ? OR shot LIKE ? OR notes LIKE ?)"
            )
            params.extend([f"%{keyword}%"] * 4)
        if log_id:
            query += " AND log_id = ?"
            params.append(log_id)
        if rating is not None and rating > 0:
            query += " AND rating >= ?"
            params.append(rating)
        if tag:
            query += (
                " AND EXISTS (SELECT 1 FROM asset_tags WHERE "
                "asset_id = media_assets.asset_id AND tag LIKE ? COLLATE NOCASE)"
            )
            params.append(f"%{tag}%")
        return query, params

    def _search_sql(self, **filters) -> tuple[str, list]:
        keyword = filters.get("keyword") or ""
        fts_keyword = self._database._fts_query(keyword)
        where_sql, params = self._filter_clause(**filters)
        if keyword and self._database._fts_available and fts_keyword:
            where_sql += (
                " AND asset_id IN (SELECT asset_id FROM media_assets_fts "
                "WHERE media_assets_fts MATCH ?)"
            )
            params.append(fts_keyword)
        return where_sql, params

    def iter_search(
        self,
        *,
        batch_size: int = 500,
        cursor: tuple[str, str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        **filters,
    ):
        where_sql, params = self._search_sql(**filters)
        if cursor:
            where_sql += (
                " AND (date_imported < ? OR (date_imported = ? AND asset_id < ?))"
            )
            params.extend([cursor[0], cursor[0], cursor[1]])
        query = f"SELECT * FROM media_assets{where_sql} ORDER BY date_imported DESC, asset_id DESC"
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None and offset > 0:
                query += " OFFSET ?"
                params.append(offset)
        with self._connection() as conn:
            sql_cursor = conn.execute(query, params)
            while True:
                rows = sql_cursor.fetchmany(max(1, batch_size))
                if not rows:
                    return
                yield from (self._row_to_asset(row) for row in rows)

    def get_search_page(
        self,
        *,
        page_size: int = 500,
        cursor: tuple[str, str] | None = None,
        **filters,
    ) -> tuple[list[MediaAsset], tuple[str, str] | None]:
        page_size = max(1, page_size)
        rows = list(
            self.iter_search(
                **filters,
                cursor=cursor,
                limit=page_size + 1,
                batch_size=page_size + 1,
            )
        )
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = (
            (rows[-1].date_imported.isoformat(), rows[-1].asset_id)
            if has_next and rows
            else None
        )
        return rows, next_cursor

    def search(self, **filters) -> list[MediaAsset]:
        return list(self.iter_search(**filters))

    def count(self, **filters) -> int:
        where_sql, params = self._search_sql(**filters)
        with self._connection() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM media_assets{where_sql}", params
            ).fetchone()[0]

    def all_tags(self) -> list[str]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT tag, COUNT(*) AS cnt FROM asset_tags GROUP BY tag ORDER BY cnt DESC, tag ASC"
            ).fetchall()
        return [row["tag"] for row in rows]

    def by_log_id(self, log_id: str) -> list[MediaAsset]:
        return self.search(log_id=log_id)

    def update_log_id(self, asset_id: str, log_id: str | None) -> bool:
        try:
            with self._transaction() as conn:
                if log_id is None:
                    conn.execute(
                        "UPDATE media_assets SET log_id = NULL, scene = '', shot = '' WHERE asset_id = ?",
                        (asset_id,),
                    )
                else:
                    conn.execute(
                        "UPDATE media_assets SET log_id = ? WHERE asset_id = ?",
                        (log_id, asset_id),
                    )
                logger.info(f"更新素材日志关联: {asset_id} -> {log_id or 'None'}")
            return True
        except sqlite3.Error as exc:
            logger.error(f"更新素材日志关联失败 {asset_id}: {exc}")
            return False

    def add_backup_locations(
        self,
        file_paths: list[str],
        target_path: str,
        project_id: str | None = None,
    ) -> int:
        if not file_paths or not target_path:
            return 0
        try:
            keys = list(dict.fromkeys(normalize_path(path) for path in file_paths))
            with self._transaction() as conn:
                rows_by_path = {}
                for chunk in self._chunks(keys):
                    placeholders = ",".join("?" * len(chunk))
                    query = (
                        "SELECT asset_id, file_path, backup_locations FROM media_assets "
                        + f"WHERE file_path IN ({placeholders})"
                    )
                    params = (*chunk, project_id) if project_id else chunk
                    if project_id:
                        query += " AND project_id = ?"
                    for row in conn.execute(query, params).fetchall():
                        rows_by_path.setdefault(row["file_path"], row)
                updated = 0
                for path in keys:
                    row = rows_by_path.get(path)
                    if not row:
                        continue
                    locations = [
                        item
                        for item in (row["backup_locations"] or "").split("|")
                        if item
                    ]
                    if target_path in locations:
                        continue
                    conn.execute(
                        "UPDATE media_assets SET backup_locations = ? WHERE asset_id = ?",
                        ("|".join([*locations, target_path]), row["asset_id"]),
                    )
                    updated += 1
            if updated:
                logger.info(
                    f"批量回写 backup_locations: {updated} 个 asset += {target_path}"
                )
            return updated
        except sqlite3.Error as exc:
            logger.error(f"批量回写 backup_locations 失败: {exc}")
            return 0

    @staticmethod
    def _chunks(asset_ids: list[str], size: int = 500):
        for offset in range(0, len(asset_ids), size):
            yield asset_ids[offset : offset + size]

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> MediaAsset:
        return MediaAsset(
            asset_id=row["asset_id"],
            project_id=row["project_id"],
            file_path=row["file_path"],
            file_name=row["file_name"],
            file_size=row["file_size"],
            file_type=row["file_type"],
            asset_type=row["asset_type"] if "asset_type" in row.keys() else "other",
            checksum_algorithm=row["checksum_algorithm"],
            checksum_value=row["checksum_value"],
            scene=row["scene"],
            shot=row["shot"],
            take=row["take"],
            date_imported=datetime.fromisoformat(row["date_imported"]),
            date_taken=datetime.fromisoformat(row["date_taken"])
            if row["date_taken"]
            else None,
            camera_make=row["camera_make"],
            camera_model=row["camera_model"],
            backup_locations=row["backup_locations"].split("|")
            if row["backup_locations"]
            else [],
            log_id=row["log_id"],
            is_working_copy=bool(row["is_working_copy"])
            if "is_working_copy" in row.keys()
            else False,
            original_path=row["original_path"] if "original_path" in row.keys() else "",
            width=row["width"] if "width" in row.keys() else 0,
            height=row["height"] if "height" in row.keys() else 0,
            duration_seconds=row["duration_seconds"]
            if "duration_seconds" in row.keys()
            else 0.0,
            lens_model=row["lens_model"] if "lens_model" in row.keys() else "",
            focal_length=row["focal_length"] if "focal_length" in row.keys() else "",
            video_metadata=row["video_metadata"]
            if "video_metadata" in row.keys()
            else "",
            rating=row["rating"] if "rating" in row.keys() else 0,
            tags=row["tags"] if "tags" in row.keys() else "",
            notes=row["notes"] if "notes" in row.keys() else "",
        )
