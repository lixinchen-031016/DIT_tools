"""拍摄日志与项目管理服务 - SQLite持久化"""
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Any

from DITWorkstation.App import config
from DITWorkstation.Models import Project, ShootingLog, MediaAsset
from DITWorkstation.Utils import logger


class DatabaseService:
    """数据库服务 - SQLite持久化"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._connection_lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-20000")
        return conn

    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    base_path TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shooting_logs (
                    log_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    scene TEXT NOT NULL,
                    shot TEXT NOT NULL,
                    take TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    camera TEXT DEFAULT '',
                    lens TEXT DEFAULT '',
                    iso INTEGER DEFAULT 0,
                    aperture TEXT DEFAULT '',
                    shutter_speed TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    file_paths TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS media_assets (
                    asset_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    file_type TEXT DEFAULT '',
                    asset_type TEXT DEFAULT 'other',
                    checksum_algorithm TEXT DEFAULT 'xxhash64',
                    checksum_value TEXT DEFAULT '',
                    scene TEXT DEFAULT '',
                    shot TEXT DEFAULT '',
                    take TEXT DEFAULT '',
                    date_imported TEXT NOT NULL,
                    date_taken TEXT,
                    camera_make TEXT DEFAULT '',
                    camera_model TEXT DEFAULT '',
                    backup_locations TEXT DEFAULT '',
                    log_id TEXT,
                    is_working_copy INTEGER DEFAULT 0,
                    original_path TEXT DEFAULT '',
                    width INTEGER DEFAULT 0,
                    height INTEGER DEFAULT 0,
                    duration_seconds REAL DEFAULT 0.0,
                    lens_model TEXT DEFAULT '',
                    focal_length TEXT DEFAULT '',
                    video_metadata TEXT DEFAULT '',
                    FOREIGN KEY (project_id) REFERENCES projects(project_id),
                    FOREIGN KEY (log_id) REFERENCES shooting_logs(log_id)
                );

                CREATE INDEX IF NOT EXISTS idx_assets_project ON media_assets(project_id);
                CREATE INDEX IF NOT EXISTS idx_assets_scene ON media_assets(scene);
                CREATE INDEX IF NOT EXISTS idx_assets_date ON media_assets(date_imported);
                CREATE INDEX IF NOT EXISTS idx_logs_project ON shooting_logs(project_id);
                CREATE INDEX IF NOT EXISTS idx_assets_file_name ON media_assets(file_name);
                CREATE INDEX IF NOT EXISTS idx_assets_path ON media_assets(file_path);
                CREATE INDEX IF NOT EXISTS idx_assets_checksum ON media_assets(checksum_value);
                CREATE INDEX IF NOT EXISTS idx_assets_log_id ON media_assets(log_id);
            """)
            conn.commit()
        finally:
            conn.close()
        self._migrate_db()

    def _migrate_db(self):
        """数据库迁移：为旧表补齐新增字段"""
        migrations = {
            "media_assets": [
                ("asset_type", "TEXT DEFAULT 'other'"),
                ("is_working_copy", "INTEGER DEFAULT 0"),
                ("original_path", "TEXT DEFAULT ''"),
                ("width", "INTEGER DEFAULT 0"),
                ("height", "INTEGER DEFAULT 0"),
                ("duration_seconds", "REAL DEFAULT 0.0"),
                ("lens_model", "TEXT DEFAULT ''"),
                ("focal_length", "TEXT DEFAULT ''"),
                ("video_metadata", "TEXT DEFAULT ''"),
            ],
        }
        conn = self._get_conn()
        try:
            for table, columns in migrations.items():
                existing = {row["name"] for row in
                            conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for col_name, col_def in columns:
                    if col_name not in existing:
                        conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                        )
                        logger.info(f"数据库迁移: {table} 新增列 {col_name}")
            conn.commit()
        finally:
            conn.close()

    def execute_transaction(self, operations: list[tuple[str, tuple]]) -> bool:
        """
        执行事务操作

        Args:
            operations: [(SQL语句, 参数元组)] 列表

        Returns:
            是否成功
        """
        conn = self._get_conn()
        try:
            conn.execute("BEGIN TRANSACTION")
            for sql, params in operations:
                conn.execute(sql, params)
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"事务执行失败: {e}")
            return False
        finally:
            conn.close()

    # ===== 项目管理 =====

    def create_project(self, name: str, description: str = "", base_path: str = "") -> Project:
        """创建项目"""
        project = Project(
            project_id=str(uuid.uuid4())[:8],
            name=name,
            description=description,
            base_path=base_path
        )
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO projects (project_id, name, description, base_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (project.project_id, project.name, project.description, project.base_path,
                 project.created_at.isoformat(), project.updated_at.isoformat())
            )
            conn.commit()
            logger.info(f"创建项目: {project.project_id} - {project.name}")
        finally:
            conn.close()
        return project

    def get_projects(self) -> List[Project]:
        """获取所有项目"""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
            return [self._row_to_project(r) for r in rows]
        finally:
            conn.close()

    def get_project(self, project_id: str) -> Optional[Project]:
        """获取单个项目"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            return self._row_to_project(row) if row else None
        finally:
            conn.close()

    def update_project(self, project_id: str, **kwargs) -> bool:
        """更新项目"""
        if not kwargs:
            return False

        fields = []
        params = []
        for key, value in kwargs.items():
            if key in ['name', 'description', 'base_path']:
                fields.append(f"{key} = ?")
                params.append(value)
        fields.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(project_id)

        conn = self._get_conn()
        try:
            conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE project_id = ?", params)
            conn.commit()
            logger.info(f"更新项目: {project_id}")
            return True
        except Exception as e:
            logger.error(f"更新项目失败 {project_id}: {e}")
            return False
        finally:
            conn.close()

    def delete_project(self, project_id: str):
        """删除项目"""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM media_assets WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM shooting_logs WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            conn.commit()
            logger.info(f"删除项目: {project_id}")
        finally:
            conn.close()

    # ===== 拍摄日志 =====

    def create_shooting_log(self, log: ShootingLog) -> ShootingLog:
        """创建拍摄日志"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO shooting_logs 
                   (log_id, project_id, scene, shot, take, description, camera, lens, iso, aperture, shutter_speed, notes, file_paths, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (log.log_id, log.project_id, log.scene, log.shot, log.take,
                 log.description, log.camera, log.lens, log.iso, log.aperture,
                 log.shutter_speed, log.notes, "|".join(log.file_paths),
                 log.created_at.isoformat())
            )
            conn.commit()
            logger.info(f"创建拍摄日志: {log.log_id} - {log.scene}/{log.shot}/{log.take}")
        finally:
            conn.close()
        return log

    def get_shooting_logs(self, project_id: str) -> List[ShootingLog]:
        """获取项目的拍摄日志"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM shooting_logs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,)
            ).fetchall()
            return [self._row_to_log(r) for r in rows]
        finally:
            conn.close()

    def get_shooting_log(self, log_id: str) -> Optional[ShootingLog]:
        """获取单个拍摄日志"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM shooting_logs WHERE log_id = ?", (log_id,)).fetchone()
            return self._row_to_log(row) if row else None
        finally:
            conn.close()

    def update_shooting_log(self, log: ShootingLog):
        """更新拍摄日志"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE shooting_logs SET scene=?, shot=?, take=?, description=?, camera=?, 
                   lens=?, iso=?, aperture=?, shutter_speed=?, notes=?, file_paths=?
                   WHERE log_id=?""",
                (log.scene, log.shot, log.take, log.description, log.camera,
                 log.lens, log.iso, log.aperture, log.shutter_speed, log.notes,
                 "|".join(log.file_paths), log.log_id)
            )
            conn.commit()
            logger.info(f"更新拍摄日志: {log.log_id}")
        finally:
            conn.close()

    def delete_shooting_log(self, log_id: str):
        """删除拍摄日志（级联清除关联素材的 log_id）"""
        conn = self._get_conn()
        try:
            conn.execute("UPDATE media_assets SET log_id = NULL WHERE log_id = ?", (log_id,))
            conn.execute("DELETE FROM shooting_logs WHERE log_id = ?", (log_id,))
            conn.commit()
            logger.info(f"删除拍摄日志: {log_id}")
        finally:
            conn.close()

    def create_log_with_assets(
        self,
        log: ShootingLog,
        asset_ids: List[str],
        sync_scene_shot: bool = False
    ) -> ShootingLog:
        """创建拍摄日志并原子地关联多个素材。

        Args:
            log: 拍摄日志对象
            asset_ids: 要关联的素材 asset_id 列表
            sync_scene_shot: True 时把 log.scene/log.shot 写入这些素材
        Returns:
            已创建的 log
        """
        conn = self._get_conn()
        try:
            conn.execute("BEGIN TRANSACTION")
            conn.execute(
                """INSERT INTO shooting_logs
                   (log_id, project_id, scene, shot, take, description, camera, lens, iso, aperture, shutter_speed, notes, file_paths, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (log.log_id, log.project_id, log.scene, log.shot, log.take,
                 log.description, log.camera, log.lens, log.iso, log.aperture,
                 log.shutter_speed, log.notes, "|".join(log.file_paths),
                 log.created_at.isoformat())
            )
            if sync_scene_shot:
                for aid in asset_ids:
                    conn.execute(
                        "UPDATE media_assets SET log_id = ?, scene = ?, shot = ? WHERE asset_id = ?",
                        (log.log_id, log.scene, log.shot, aid)
                    )
            else:
                for aid in asset_ids:
                    conn.execute(
                        "UPDATE media_assets SET log_id = ? WHERE asset_id = ?",
                        (log.log_id, aid)
                    )
            conn.commit()
            logger.info(f"创建日志并关联素材: {log.log_id} - {len(asset_ids)} 个素材")
        except Exception as e:
            conn.rollback()
            logger.error(f"create_log_with_assets 失败: {e}")
            raise
        finally:
            conn.close()
        return log

    # ===== 素材资产 =====

    def add_media_asset(self, asset: MediaAsset) -> MediaAsset:
        """添加素材资产"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO media_assets
                   (asset_id, project_id, file_path, file_name, file_size, file_type,
                    asset_type, checksum_algorithm, checksum_value, scene, shot, take, date_imported,
                    date_taken, camera_make, camera_model, backup_locations, log_id,
                    is_working_copy, original_path, width, height, duration_seconds,
                    lens_model, focal_length, video_metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (asset.asset_id, asset.project_id, asset.file_path, asset.file_name,
                 asset.file_size, asset.file_type, asset.asset_type,
                 asset.checksum_algorithm, asset.checksum_value,
                 asset.scene, asset.shot, asset.take,
                 asset.date_imported.isoformat(),
                 asset.date_taken.isoformat() if asset.date_taken else None,
                 asset.camera_make, asset.camera_model,
                 "|".join(asset.backup_locations), asset.log_id,
                 1 if asset.is_working_copy else 0,
                 asset.original_path,
                 asset.width, asset.height, asset.duration_seconds,
                 asset.lens_model, asset.focal_length, asset.video_metadata)
            )
            conn.commit()
            logger.info(f"添加素材资产: {asset.asset_id} - {asset.file_name}")
        finally:
            conn.close()
        return asset

    def add_media_assets_batch(self, assets: List[MediaAsset]) -> int:
        """批量添加素材资产"""
        if not assets:
            return 0

        conn = self._get_conn()
        try:
            conn.execute("BEGIN TRANSACTION")
            for asset in assets:
                conn.execute(
                    """INSERT INTO media_assets
                       (asset_id, project_id, file_path, file_name, file_size, file_type,
                        asset_type, checksum_algorithm, checksum_value, scene, shot, take, date_imported,
                        date_taken, camera_make, camera_model, backup_locations, log_id,
                        is_working_copy, original_path, width, height, duration_seconds,
                        lens_model, focal_length, video_metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (asset.asset_id, asset.project_id, asset.file_path, asset.file_name,
                     asset.file_size, asset.file_type, asset.asset_type,
                     asset.checksum_algorithm, asset.checksum_value,
                     asset.scene, asset.shot, asset.take,
                     asset.date_imported.isoformat(),
                     asset.date_taken.isoformat() if asset.date_taken else None,
                     asset.camera_make, asset.camera_model,
                     "|".join(asset.backup_locations), asset.log_id,
                     1 if asset.is_working_copy else 0,
                     asset.original_path,
                     asset.width, asset.height, asset.duration_seconds,
                     asset.lens_model, asset.focal_length, asset.video_metadata)
                )
            conn.commit()
            logger.info(f"批量添加素材资产: {len(assets)} 个")
            return len(assets)
        except Exception as e:
            conn.rollback()
            logger.error(f"批量添加素材资产失败: {e}")
            return 0
        finally:
            conn.close()

    def get_media_assets(self, project_id: str) -> List[MediaAsset]:
        """获取项目素材"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM media_assets WHERE project_id = ? ORDER BY date_imported DESC",
                (project_id,)
            ).fetchall()
            return [self._row_to_asset(r) for r in rows]
        finally:
            conn.close()

    def get_media_asset(self, asset_id: str) -> Optional[MediaAsset]:
        """获取单个素材资产"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM media_assets WHERE asset_id = ?", (asset_id,)).fetchone()
            return self._row_to_asset(row) if row else None
        finally:
            conn.close()

    def update_media_asset(self, asset_id: str, **kwargs) -> bool:
        """更新素材资产"""
        if not kwargs:
            return False

        fields = []
        params = []
        for key, value in kwargs.items():
            if key in ['file_path', 'file_name', 'file_size', 'file_type',
                       'asset_type', 'checksum_algorithm', 'checksum_value',
                       'scene', 'shot', 'take', 'date_taken',
                       'camera_make', 'camera_model',
                       'backup_locations', 'log_id',
                       'is_working_copy', 'original_path',
                       'width', 'height', 'duration_seconds',
                       'lens_model', 'focal_length', 'video_metadata']:
                if key == 'backup_locations' and isinstance(value, list):
                    value = "|".join(value)
                if key == 'is_working_copy' and isinstance(value, bool):
                    value = 1 if value else 0
                if key == 'date_taken' and hasattr(value, 'isoformat'):
                    value = value.isoformat()
                fields.append(f"{key} = ?")
                params.append(value)
        params.append(asset_id)

        conn = self._get_conn()
        try:
            conn.execute(f"UPDATE media_assets SET {', '.join(fields)} WHERE asset_id = ?", params)
            conn.commit()
            logger.info(f"更新素材资产: {asset_id}")
            return True
        except Exception as e:
            logger.error(f"更新素材资产失败 {asset_id}: {e}")
            return False
        finally:
            conn.close()

    def delete_media_asset(self, asset_id: str) -> bool:
        """删除素材资产"""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM media_assets WHERE asset_id = ?", (asset_id,))
            conn.commit()
            logger.info(f"删除素材资产: {asset_id}")
            return True
        except Exception as e:
            logger.error(f"删除素材资产失败 {asset_id}: {e}")
            return False
        finally:
            conn.close()

    def asset_exists_by_path(self, project_id: str, file_path: str) -> bool:
        """按文件路径查重"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND file_path = ?",
                (project_id, file_path)
            ).fetchone()
            return row[0] > 0
        finally:
            conn.close()

    def asset_exists_by_checksum(self, project_id: str, checksum_value: str) -> bool:
        """按校验和查重"""
        if not checksum_value:
            return False
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND checksum_value = ?",
                (project_id, checksum_value)
            ).fetchone()
            return row[0] > 0
        finally:
            conn.close()

    def update_asset_path(self, asset_id: str, new_path: str, new_name: str = None) -> bool:
        """更新素材路径"""
        conn = self._get_conn()
        try:
            if new_name:
                conn.execute(
                    "UPDATE media_assets SET file_path = ?, file_name = ? WHERE asset_id = ?",
                    (new_path, new_name, asset_id)
                )
            else:
                conn.execute(
                    "UPDATE media_assets SET file_path = ? WHERE asset_id = ?",
                    (new_path, asset_id)
                )
            conn.commit()
            logger.info(f"更新素材路径: {asset_id}")
            return True
        except Exception as e:
            logger.error(f"更新素材路径失败 {asset_id}: {e}")
            return False
        finally:
            conn.close()

    def search_assets(
        self,
        project_id: Optional[str] = None,
        scene: Optional[str] = None,
        shot: Optional[str] = None,
        file_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        keyword: Optional[str] = None,
        log_id: Optional[str] = None
    ) -> List[MediaAsset]:
        """搜索素材"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM media_assets WHERE 1=1"
            params = []

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
            if keyword:
                query += " AND (file_name LIKE ? OR scene LIKE ? OR shot LIKE ?)"
                params.extend([f"%{keyword}%"] * 3)
            if log_id:
                query += " AND log_id = ?"
                params.append(log_id)

            query += " ORDER BY date_imported DESC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_asset(r) for r in rows]
        finally:
            conn.close()

    def get_assets_by_log_id(self, log_id: str) -> List[MediaAsset]:
        """按拍摄日志ID获取关联的素材"""
        return self.search_assets(log_id=log_id)

    def update_media_asset_log_id(self, asset_id: str, log_id: Optional[str]) -> bool:
        """更新素材关联的拍摄日志（log_id=None 解除关联）"""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE media_assets SET log_id = ? WHERE asset_id = ?",
                (log_id, asset_id)
            )
            conn.commit()
            logger.info(f"更新素材日志关联: {asset_id} -> {log_id or 'None'}")
            return True
        except Exception as e:
            logger.error(f"更新素材日志关联失败 {asset_id}: {e}")
            return False
        finally:
            conn.close()

    # ===== 统计查询 =====

    def get_project_stats(self, project_id: str) -> dict:
        """获取项目统计信息"""
        conn = self._get_conn()
        try:
            asset_count = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ?",
                (project_id,)
            ).fetchone()[0]

            log_count = conn.execute(
                "SELECT COUNT(*) FROM shooting_logs WHERE project_id = ?",
                (project_id,)
            ).fetchone()[0]

            total_size = conn.execute(
                "SELECT COALESCE(SUM(file_size), 0) FROM media_assets WHERE project_id = ?",
                (project_id,)
            ).fetchone()[0]

            return {
                "asset_count": asset_count,
                "log_count": log_count,
                "total_size": total_size
            }
        finally:
            conn.close()

    # ===== 辅助方法 =====

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"],
            base_path=row["base_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"])
        )

    def _row_to_log(self, row: sqlite3.Row) -> ShootingLog:
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
            created_at=datetime.fromisoformat(row["created_at"])
        )

    def _row_to_asset(self, row: sqlite3.Row) -> MediaAsset:
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
        )
