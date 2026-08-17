"""Read-side media asset persistence operations."""
from datetime import datetime
import sqlite3
from typing import Optional

from DITWorkstation.Models import MediaAsset, OperationResult, OperationStatus
from DITWorkstation.Utils import logger

from .base_repository import BaseRepository
from .field_registry import MEDIA_ASSET_FIELDS, build_update_clause


class AssetRepository(BaseRepository):
    """Owns basic media-asset CRUD while recovery flows remain in the facade."""

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
        conn.execute(
            """INSERT INTO media_assets
               (asset_id, project_id, file_path, file_name, file_size, file_type,
                asset_type, checksum_algorithm, checksum_value, scene, shot, take, date_imported,
                date_taken, camera_make, camera_model, backup_locations, log_id,
                is_working_copy, original_path, width, height, duration_seconds,
                lens_model, focal_length, video_metadata, rating, tags, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset.asset_id, asset.project_id, asset.file_path, asset.file_name,
             asset.file_size, asset.file_type, asset.asset_type,
             asset.checksum_algorithm, asset.checksum_value, asset.scene, asset.shot,
             asset.take, asset.date_imported.isoformat(),
             asset.date_taken.isoformat() if asset.date_taken else None,
             asset.camera_make, asset.camera_model, "|".join(asset.backup_locations),
             asset.log_id, 1 if asset.is_working_copy else 0, asset.original_path,
             asset.width, asset.height, asset.duration_seconds, asset.lens_model,
             asset.focal_length, asset.video_metadata, asset.rating, asset.tags, asset.notes),
        )

    def create(self, asset: MediaAsset) -> MediaAsset:
        with self._transaction() as conn:
            self._insert_asset(conn, asset)
            self.sync_tags(conn, asset.asset_id, asset.tags)
            self._database._sync_asset_fts(
                conn, asset.asset_id, file_name=asset.file_name, scene=asset.scene,
                shot=asset.shot, notes=asset.notes, tags=asset.tags,
            )
            logger.info(f"添加素材资产: {asset.asset_id} - {asset.file_name}")
        return asset

    def create_batch(self, assets: list[MediaAsset]) -> int:
        if not assets:
            return 0
        try:
            with self._transaction() as conn:
                for asset in assets:
                    self._insert_asset(conn, asset)
                    self.sync_tags(conn, asset.asset_id, asset.tags)
                    self._database._sync_asset_fts(
                        conn, asset.asset_id, file_name=asset.file_name, scene=asset.scene,
                        shot=asset.shot, notes=asset.notes, tags=asset.tags,
                    )
                logger.info(f"批量添加素材资产: {len(assets)} 个")
                return len(assets)
        except Exception as exc:
            logger.error(f"批量添加素材资产失败: {exc}")
            return 0

    def update_result(self, asset_id: str, **kwargs) -> OperationResult:
        sql, params = build_update_clause(
            MEDIA_ASSET_FIELDS, "media_assets", "asset_id", asset_id,
            touch_updated_at=False, **kwargs,
        )
        if not sql:
            return OperationResult(OperationStatus.INVALID, "没有可更新的素材字段")
        try:
            with self._transaction() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = ?", (asset_id,)
                ).fetchone()
                if exists is None:
                    return OperationResult(OperationStatus.NOT_FOUND, f"素材不存在: {asset_id}")
                conn.execute(sql, params)
                if "tags" in kwargs:
                    self.sync_tags(conn, asset_id, kwargs.get("tags") or "")
                self._database._refresh_asset_fts(conn, asset_id)
                self._database._record_operation_in_transaction(
                    conn, "更新素材", project_id=None, object_type="asset", object_id=asset_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=1)
        except sqlite3.Error as exc:
            logger.error(f"更新素材资产失败 {asset_id}: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def list_all(
        self, project_id: str, limit: Optional[int] = None, offset: Optional[int] = None,
    ) -> list[MediaAsset]:
        query = (
            "SELECT * FROM media_assets WHERE project_id = ? "
            "ORDER BY date_imported DESC, asset_id DESC"
        )
        params = [project_id]
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
                "ORDER BY date_imported DESC, asset_id DESC", (project_id,)
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

    def get_page(
        self, project_id: str, page_size: int = 500,
        cursor: Optional[tuple[str, str]] = None,
    ) -> tuple[list[MediaAsset], Optional[tuple[str, str]]]:
        """Return a keyset page ordered by ``date_imported, asset_id`` descending."""
        page_size = max(1, page_size)
        query = "SELECT * FROM media_assets WHERE project_id = ?"
        params = [project_id]
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

    def get(self, asset_id: str) -> Optional[MediaAsset]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return self._row_to_asset(row) if row else None

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
            date_taken=datetime.fromisoformat(row["date_taken"]) if row["date_taken"] else None,
            camera_make=row["camera_make"],
            camera_model=row["camera_model"],
            backup_locations=row["backup_locations"].split("|") if row["backup_locations"] else [],
            log_id=row["log_id"],
            is_working_copy=bool(row["is_working_copy"]) if "is_working_copy" in row.keys() else False,
            original_path=row["original_path"] if "original_path" in row.keys() else "",
            width=row["width"] if "width" in row.keys() else 0,
            height=row["height"] if "height" in row.keys() else 0,
            duration_seconds=row["duration_seconds"] if "duration_seconds" in row.keys() else 0.0,
            lens_model=row["lens_model"] if "lens_model" in row.keys() else "",
            focal_length=row["focal_length"] if "focal_length" in row.keys() else "",
            video_metadata=row["video_metadata"] if "video_metadata" in row.keys() else "",
            rating=row["rating"] if "rating" in row.keys() else 0,
            tags=row["tags"] if "tags" in row.keys() else "",
            notes=row["notes"] if "notes" in row.keys() else "",
        )
