"""拍摄日志与项目管理服务 - SQLite持久化"""
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from DITWorkstation.App import config
from DITWorkstation.Models import (
    BackupTemplate,
    ChecksumAlgorithm,
    MediaAsset,
    OperationResult,
    OperationStatus,
    Project,
    ProjectTemplate,
    ShootingLog,
    Workspace,
)
from DITWorkstation.Services.repositories.asset_repository import AssetRepository
from DITWorkstation.Services.repositories.backup_job_repository import (
    BackupJobRepository,
)
from DITWorkstation.Services.repositories.checksum_cache_repository import (
    ChecksumCacheRepository,
)
from DITWorkstation.Services.repositories.log_repository import LogRepository
from DITWorkstation.Services.repositories.project_repository import ProjectRepository
from DITWorkstation.Services.repositories.recycle_repository import RecycleRepository
from DITWorkstation.Services.repositories.rename_repository import RenameRepository
from DITWorkstation.Services.repositories.saved_search_repository import (
    SavedSearchRepository,
)
from DITWorkstation.Services.repositories.task_repository import TaskRepository
from DITWorkstation.Services.repositories.template_repository import TemplateRepository
from DITWorkstation.Services.repositories.workspace_repository import (
    WorkspaceRepository,
)
from DITWorkstation.Utils import logger, now_local


def _chunked(seq, size):
    """把序列切分为固定大小的块（SQLite 变量上限 999，保守取 500）。"""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class DatabaseService:
    """数据库服务 - SQLite持久化"""

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        """解析历史时间字段；损坏的旧值不应阻断列表或报告读取。"""
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程级连接池：每个线程复用同一连接，避免高频操作反复建连 + 重复设置 PRAGMA
        self._conns: dict = {}
        self._conns_lock = threading.Lock()
        self._had_existing_db = self.db_path.exists() and self.db_path.stat().st_size > 0
        self._fts_available = False
        self._init_db()
        self.workspaces = WorkspaceRepository(self)
        self.templates = TemplateRepository(self)
        self.projects = ProjectRepository(self)
        self.logs = LogRepository(self)
        self.assets = AssetRepository(self)
        self.recycle = RecycleRepository(self)
        self.renames = RenameRepository(self)
        self.backup_jobs = BackupJobRepository(self)
        self.tasks = TaskRepository(self)
        self.checksum_cache = ChecksumCacheRepository(self)
        self.saved_searches = SavedSearchRepository(self)
        self.cleanup_history()

    def _create_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # 外键约束必须在每条连接上启用，SQLite 默认关闭该选项。
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-20000")
        # WAL 模式下允许多读 + 单写；主线程与 worker 线程并发写时，
        # busy_timeout 让第二个写者等待 5 秒而非立即抛 SQLITE_BUSY，
        # 避免备份回写 / UI 更新等操作静默失败。
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的复用连接（连接池）。"""
        tid = threading.get_ident()
        current_thread = threading.current_thread()
        with self._conns_lock:
            # worker 线程结束后清理其连接，避免长期运行时连接表只增不减。
            dead = [old_tid for old_tid, (thread, _conn) in self._conns.items()
                    if old_tid != tid and not thread.is_alive()]
            for old_tid in dead:
                _thread, old_conn = self._conns.pop(old_tid)
                try:
                    old_conn.close()
                except sqlite3.Error:
                    pass

            entry = self._conns.get(tid)
            if entry and entry[0] is not current_thread:
                # 线程 ID 可能被操作系统复用，不能把旧线程的连接交给新线程。
                try:
                    entry[1].close()
                except sqlite3.Error:
                    pass
                self._conns.pop(tid, None)
                entry = None
            conn = entry[1] if entry else None
            if conn is None:
                conn = self._create_conn()
                self._conns[tid] = (current_thread, conn)
            return conn

    @contextmanager
    def _connection(self):
        """连接上下文管理器：复用连接，不自动提交。

        复用连接时不能 close；退出时回滚悬挂事务，避免异常路径遗留写锁。
        """
        conn = self._get_conn()
        try:
            yield conn
        finally:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass

    @contextmanager
    def _transaction(self):
        """事务上下文管理器：自动提交/回滚（复用连接，不关闭）。"""
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self):
        """初始化数据库表"""
        conn = self._create_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    base_path TEXT DEFAULT '',
                    workspace_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
                );

                CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id);

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
                    rating INTEGER DEFAULT 0,
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
                CREATE INDEX IF NOT EXISTS idx_assets_project_date
                    ON media_assets(project_id, date_imported);

                CREATE TABLE IF NOT EXISTS asset_tags (
                    asset_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (asset_id, tag),
                    FOREIGN KEY (asset_id) REFERENCES media_assets(asset_id)
                );

                CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag);

                CREATE TABLE IF NOT EXISTS backup_jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    source_path TEXT NOT NULL,
                    algorithm TEXT DEFAULT 'xxhash64',
                    total_files INTEGER DEFAULT 0,
                    total_bytes INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'idle',
                    targets_json TEXT DEFAULT '[]',
                    snapshot_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );

                CREATE INDEX IF NOT EXISTS idx_backup_jobs_project ON backup_jobs(project_id);

                CREATE TABLE IF NOT EXISTS task_history (
                    task_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    project_id TEXT,
                    state TEXT NOT NULL,
                    parameters_json TEXT DEFAULT '{}',
                    recovery_json TEXT DEFAULT '{}',
                    output_json TEXT DEFAULT '{}',
                    error_summary TEXT DEFAULT '',
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_history_project ON task_history(project_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_task_history_state ON task_history(state, created_at);

                CREATE TABLE IF NOT EXISTS operation_logs (
                    log_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    event TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    result_status TEXT DEFAULT 'success',
                    object_type TEXT DEFAULT '',
                    object_id TEXT DEFAULT '',
                    recovery_id TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_operation_logs_project ON operation_logs(project_id);
                CREATE INDEX IF NOT EXISTS idx_operation_logs_created ON operation_logs(created_at);

                CREATE TABLE IF NOT EXISTS recycle_bin (
                    recycle_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    project_id TEXT,
                    snapshot_json TEXT NOT NULL,
                    deleted_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_recycle_bin_expires ON recycle_bin(expires_at);

                CREATE TABLE IF NOT EXISTS rename_history (
                    rename_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    mappings_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reverted_at TEXT
                );

                CREATE TABLE IF NOT EXISTS project_templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    base_path TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backup_templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    target_paths TEXT DEFAULT '[]',
                    algorithm TEXT DEFAULT 'xxhash64',
                    verify_after_copy INTEGER DEFAULT 1,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_backup_templates_created
                    ON backup_templates(created_at);

                CREATE TABLE IF NOT EXISTS checksum_cache (
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    algorithm TEXT NOT NULL,
                    hash_value TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    PRIMARY KEY (file_path, file_size, mtime_ns, algorithm)
                );
                CREATE INDEX IF NOT EXISTS idx_checksum_cache_accessed
                    ON checksum_cache(accessed_at);
            """)
            conn.commit()
        finally:
            conn.close()
        self._migrate_db()

    def close_all(self):
        """关闭连接池中所有连接（测试/退出时调用）。"""
        with self._conns_lock:
            conns = [conn for _thread, conn in self._conns.values()]
            self._conns.clear()
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def close_current_thread(self):
        """关闭当前线程的连接，供长任务线程在退出前显式调用。"""
        tid = threading.get_ident()
        with self._conns_lock:
            entry = self._conns.pop(tid, None)
        if entry:
            try:
                entry[1].close()
            except sqlite3.Error:
                pass

    # 当前数据库 schema 版本：每次结构变更 +1，并在 _migrate_db 中补充对应迁移
    _DB_VERSION = 10

    def _migrate_db(self):
        """按 PRAGMA user_version 分版本迁移数据库。

        版本化迁移保证：
        - 旧库升级路径明确（v0 → v1 → v2 …），新库直接建全量 schema；
        - 每个迁移幂等，重复执行不破坏数据。
        """
        conn = self._create_conn()
        try:
            version = self._get_user_version(conn)
            fts_exists = self._fts_table_exists(conn)
            if version < self._DB_VERSION and self._had_existing_db:
                self._backup_before_migration(conn)
            if version < 1:
                self._migrate_v1(conn)
                self._set_user_version(conn, 1)
                logger.info("数据库迁移完成: v1")
            if version < 2:
                self._migrate_v2(conn)
                self._set_user_version(conn, 2)
                logger.info("数据库迁移完成: v2")
            if version < 3:
                self._migrate_v3(conn)
                self._set_user_version(conn, 3)
                logger.info("数据库迁移完成: v3")
            if version < 4:
                self._migrate_v4(conn)
                self._set_user_version(conn, 4)
                logger.info("数据库迁移完成: v4")
            if version < 5:
                self._migrate_v5(conn)
                self._set_user_version(conn, 5)
                logger.info("数据库迁移完成: v5")
            if version < 6:
                self._migrate_v6(conn)
                self._set_user_version(conn, 6)
                logger.info("数据库迁移完成: v6")
            if version < 7:
                self._migrate_v7(conn)
                self._set_user_version(conn, 7)
                logger.info("数据库迁移完成: v7")
            if version < 8:
                self._migrate_v8(conn)
                self._set_user_version(conn, 8)
                logger.info("数据库迁移完成: v8")
            if version < 9:
                self._migrate_v9(conn)
                self._set_user_version(conn, 9)
                logger.info("数据库迁移完成: v9")
            if version < 10:
                self._migrate_v10(conn)
                self._set_user_version(conn, 10)
                logger.info("数据库迁移完成: v10")

            # FTS 创建必须晚于迁移前备份，否则备份会混入新建索引表。
            # v3 数据库若因中断或旧版本缺失索引，也在这里补建并重建一次。
            self._fts_available = self._ensure_fts(conn)
            if self._fts_available and (version < 3 or not fts_exists):
                conn.execute("INSERT INTO media_assets_fts(media_assets_fts) VALUES ('rebuild')")

            # 向后兼容：旧项目（workspace_id 为 NULL）自动归入"默认工作区"
            self._migrate_legacy_projects_to_default_workspace(conn)
            self._recover_interrupted_backup_jobs(conn)
            # 回收站不依赖用户主动打开窗口才清理，避免长期运行时过期快照积累。
            expired = conn.execute(
                "DELETE FROM recycle_bin WHERE expires_at <= ?", (now_local().isoformat(),)
            ).rowcount
            if expired:
                logger.info(f"启动时清理过期回收站快照: {expired} 项")
            conn.commit()
        finally:
            conn.close()

    def _backup_before_migration(self, conn):
        """在升级旧库前生成可恢复副本，避免迁移失败后只能人工抢救。"""
        backup_path = self.db_path.with_suffix(self.db_path.suffix + ".pre-migration.bak")
        try:
            # SQLite backup API 会同时读取主库和 WAL，生成一致快照；
            # 直接复制 .db 可能遗漏尚未合并到主库的数据。
            backup_conn = sqlite3.connect(str(backup_path))
            try:
                conn.backup(backup_conn)
                backup_conn.commit()
            finally:
                backup_conn.close()
            logger.info(f"数据库迁移前备份已生成: {backup_path}")
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(f"无法创建数据库迁移备份: {exc}") from exc

    @staticmethod
    def _recover_interrupted_backup_jobs(conn):
        """把上次进程退出时仍为 running 的备份转为可重试状态。"""
        import json

        rows = conn.execute(
            "SELECT job_id, targets_json FROM backup_jobs WHERE status = 'running'"
        ).fetchall()
        for row in rows:
            try:
                targets = json.loads(row["targets_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                targets = []
            has_completed = False
            for target in targets:
                if target.get("status") == "completed":
                    has_completed = True
                    continue
                pending = list(target.get("pending_files", []))
                failed = list(target.get("failed_files", []))
                for relative in pending:
                    if relative not in failed:
                        failed.append(relative)
                target["failed_files"] = failed
                target["pending_files"] = []
                target["status"] = "failed"
                target["error_message"] = (
                    "应用在备份执行期间退出，未完成文件已转入重试队列"
                )
            status = "partial" if has_completed else "failed"
            conn.execute(
                "UPDATE backup_jobs SET status = ?, targets_json = ?, "
                "failed_files_json = ?, completed_at = ? WHERE job_id = ?",
                (
                    status,
                    json.dumps(targets, ensure_ascii=False),
                    json.dumps([
                        {"path": t.get("path", ""), "files": t.get("failed_files", [])}
                        for t in targets if t.get("failed_files")
                    ], ensure_ascii=False),
                    now_local().isoformat(),
                    row["job_id"],
                ),
            )
            logger.warning(f"备份任务已标记为中断待重试: {row['job_id']}")

    @staticmethod
    def _get_user_version(conn) -> int:
        return conn.execute("PRAGMA user_version").fetchone()[0]

    @staticmethod
    def _set_user_version(conn, version: int):
        conn.execute(f"PRAGMA user_version = {int(version)}")

    def _migrate_v1(self, conn):
        """v1：为旧表补齐历史新增字段（幂等 ALTER）。"""
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
                ("rating", "INTEGER DEFAULT 0"),
                ("tags", "TEXT DEFAULT ''"),
                ("notes", "TEXT DEFAULT ''"),
            ],
            "projects": [
                ("workspace_id", "TEXT"),
            ],
            "backup_jobs": [
                ("failed_files_json", "TEXT DEFAULT '[]'"),
            ],
        }
        for table, columns in migrations.items():
            existing = {row["name"] for row in
                        conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for col_name, col_def in columns:
                if col_name not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                    )
                    logger.info(f"数据库迁移 v1: {table} 新增列 {col_name}")

    def _migrate_v2(self, conn):
        """v2：asset_tags 关联表回填 + project_templates 建表 + 默认模板。"""
        # 回填 asset_tags：把历史 tags 逗号字符串拆分为关联表记录
        rows = conn.execute(
            "SELECT asset_id, tags FROM media_assets WHERE TRIM(COALESCE(tags, '')) != ''"
        ).fetchall()
        for row in rows:
            for tag in self._split_tags(row["tags"]):
                conn.execute(
                    "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                    (row["asset_id"], tag),
                )
        if rows:
            logger.info(f"数据库迁移 v2: 回填 {len(rows)} 条素材的标签到 asset_tags")

        # 预置默认项目模板（仅当模板表为空时）
        count = conn.execute("SELECT COUNT(*) FROM project_templates").fetchone()[0]
        if count == 0:
            now = now_local().isoformat()
            conn.execute(
                "INSERT INTO project_templates "
                "(template_id, name, description, base_path, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "default",
                    "标准影视项目",
                    "适用于常规影视/广告拍摄：自动创建 DIT_Workspace 工作目录",
                    "DIT_Workspace",
                    "基于模板创建项目时会自动预填工作目录与描述。",
                    now,
                    now,
                ),
            )
            logger.info("数据库迁移 v2: 预置默认项目模板")

    def _migrate_v3(self, conn):
        """v3：预留素材全文索引版本号。索引在迁移备份后统一创建。"""
        return

    def _migrate_v4(self, conn):
        """v4：增加可复用的备份方案模板表。"""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS backup_templates (
                template_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                target_paths TEXT DEFAULT '[]',
                algorithm TEXT DEFAULT 'xxhash64',
                verify_after_copy INTEGER DEFAULT 1,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_backup_templates_created "
            "ON backup_templates(created_at)"
        )

    def _migrate_v5(self, conn):
        """v5：回收站、重命名回退记录和可诊断审计字段。"""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(operation_logs)")}
        for name, definition in (
            ("result_status", "TEXT DEFAULT 'success'"),
            ("object_type", "TEXT DEFAULT ''"),
            ("object_id", "TEXT DEFAULT ''"),
            ("recovery_id", "TEXT DEFAULT ''"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE operation_logs ADD COLUMN {name} {definition}")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS recycle_bin (
                recycle_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                project_id TEXT,
                snapshot_json TEXT NOT NULL,
                deleted_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recycle_bin_expires ON recycle_bin(expires_at)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS rename_history (
                rename_id TEXT PRIMARY KEY,
                project_id TEXT,
                mappings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reverted_at TEXT
            )"""
        )

    def _migrate_v6(self, conn):
        """v6：统一任务历史及备份源文件快照。"""
        backup_columns = {row["name"] for row in conn.execute("PRAGMA table_info(backup_jobs)")}
        if "snapshot_json" not in backup_columns:
            conn.execute("ALTER TABLE backup_jobs ADD COLUMN snapshot_json TEXT DEFAULT '[]'")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_history (
                task_id TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                project_id TEXT,
                state TEXT NOT NULL,
                parameters_json TEXT DEFAULT '{}',
                recovery_json TEXT DEFAULT '{}',
                output_json TEXT DEFAULT '{}',
                error_summary TEXT DEFAULT '',
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_history_project ON task_history(project_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_history_state ON task_history(state, created_at)")

    def _migrate_v7(self, conn):
        """v7：项目健康页所需的备份目标容量历史。"""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS storage_health_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                project_id TEXT,
                target_path TEXT NOT NULL,
                total_bytes INTEGER NOT NULL,
                free_bytes INTEGER NOT NULL,
                captured_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_storage_health_project "
            "ON storage_health_snapshots(project_id, captured_at)"
        )

    @staticmethod
    def _migrate_v8(conn):
        """v8：按拍摄日期聚合时间线的查询索引。"""
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_taken "
            "ON media_assets(date_taken, project_id)"
        )

    @staticmethod
    def _migrate_v9(conn):
        """v9：跨会话校验和缓存。"""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS checksum_cache (
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                hash_value TEXT NOT NULL,
                accessed_at TEXT NOT NULL,
                PRIMARY KEY (file_path, file_size, mtime_ns, algorithm)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checksum_cache_accessed "
            "ON checksum_cache(accessed_at)"
        )

    @staticmethod
    def _migrate_v10(conn):
        """v10：保存搜索 / 智能集合。"""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS saved_searches (
                search_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                filters_json TEXT NOT NULL DEFAULT '{}',
                workspace_id TEXT,
                project_id TEXT,
                is_smart INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saved_searches_name ON saved_searches(name)"
        )

    @staticmethod
    def _fts_table_exists(conn) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("media_assets_fts",),
        ).fetchone() is not None

    @staticmethod
    def _ensure_fts(conn) -> bool:
        """创建 FTS5 索引表；系统 SQLite 未编译 FTS5 时返回 False。"""
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS media_assets_fts USING fts5("
                "asset_id UNINDEXED, file_name, scene, shot, notes, tags, "
                "tokenize='unicode61')"
            )
            return True
        except sqlite3.OperationalError as exc:
            logger.warning(f"SQLite 未启用 FTS5，搜索回退 LIKE: {exc}")
            return False

    @staticmethod
    def _fts_query(keyword: str) -> str | None:
        """将简单关键词转换为 FTS5 前缀查询；复杂/非 ASCII 输入走 LIKE。"""
        if not keyword or not re.fullmatch(r"[A-Za-z0-9_ .-]+", keyword):
            return None
        terms = re.findall(r"[A-Za-z0-9_]+", keyword.lower())
        return " AND ".join(f'"{term}"*' for term in terms) if terms else None

    def _sync_asset_fts(self, conn, asset_id: str, *, file_name="", scene="",
                        shot="", notes="", tags=""):
        if not self._fts_available:
            return
        conn.execute("DELETE FROM media_assets_fts WHERE asset_id = ?", (asset_id,))
        conn.execute(
            "INSERT INTO media_assets_fts(asset_id, file_name, scene, shot, notes, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (asset_id, file_name or "", scene or "", shot or "", notes or "", tags or ""),
        )

    def _refresh_asset_fts(self, conn, asset_id: str):
        """从素材主表重新同步单条 FTS 记录（须在事务内调用）。"""
        if not self._fts_available:
            return
        row = conn.execute(
            "SELECT file_name, scene, shot, notes, tags FROM media_assets "
            "WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if row:
            self._sync_asset_fts(
                conn, asset_id, file_name=row["file_name"], scene=row["scene"],
                shot=row["shot"], notes=row["notes"], tags=row["tags"],
            )

    @staticmethod
    def _split_tags(tags: str) -> list:
        """把逗号分隔的标签字符串拆分为去空白、去空的标签列表。"""
        return [t.strip() for t in (tags or "").split(",") if t.strip()]

    def _migrate_legacy_projects_to_default_workspace(self, conn):
        """把所有 workspace_id 为 NULL 的旧项目归入"默认工作区"。

        - 首次启动且已有项目时：创建默认工作区（id 固定为 'default'，便于幂等），
          把所有旧项目 workspace_id 设为 'default'。
        - 用户可后续手动调整项目归属或重命名默认工作区。
        """
        orphan_count = conn.execute(
            "SELECT COUNT(*) FROM projects WHERE workspace_id IS NULL"
        ).fetchone()[0]
        if orphan_count == 0:
            return

        # 幂等：用固定 ID 'default'，避免重复创建
        existing_default = conn.execute(
            "SELECT workspace_id FROM workspaces WHERE workspace_id = 'default'"
        ).fetchone()
        if not existing_default:
            now = now_local().isoformat()
            conn.execute(
                "INSERT INTO workspaces (workspace_id, name, path, description, created_at, updated_at) "
                "VALUES ('default', '默认工作区', '', '由旧项目自动归集生成', ?, ?)",
                (now, now)
            )
            logger.info("数据库迁移: 创建默认工作区用于归集旧项目")

        conn.execute("UPDATE projects SET workspace_id = 'default' WHERE workspace_id IS NULL")
        conn.commit()
        logger.info(f"数据库迁移: 已将 {orphan_count} 个旧项目归入默认工作区")

    def execute_transaction(self, operations: list[tuple[str, tuple]]) -> bool:
        """
        执行事务操作

        Args:
            operations: [(SQL语句, 参数元组)] 列表

        Returns:
            是否成功
        """
        try:
            with self._transaction() as conn:
                for sql, params in operations:
                    conn.execute(sql, params)
            return True
        except Exception as e:
            logger.error(f"事务执行失败: {e}")
            return False

    # ===== 工作区管理 =====

    def create_workspace(self, name: str, path: str = "", description: str = "") -> Workspace:
        """创建工作区。"""
        return self.workspaces.create(name, path, description)

    def get_workspaces(self) -> list[Workspace]:
        """获取所有工作区，按创建时间倒序"""
        return self.workspaces.list_all()

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        """获取单个工作区"""
        return self.workspaces.get(workspace_id)

    def get_or_create_default_workspace(self) -> Workspace:
        """获取或创建 id='default' 的默认工作区。

        个人模式不显式创建工作区，项目（workspace_id=None）自动归入此处；
        首启向导与启动兼容检查依赖本方法确保该工作区一定存在（path 可能为空）。
        """
        return self.workspaces.get_or_create_default()

    def update_workspace(self, workspace_id: str, **kwargs) -> bool:
        """更新工作区（支持 name / path / description）"""
        return self.workspaces.update(workspace_id, **kwargs)

    def delete_workspace(self, workspace_id: str, reassign_to: str | None = None) -> bool:
        """删除工作区。

        Args:
            workspace_id: 待删除的工作区 ID
            reassign_to: 删除前把该工作区下的项目迁移到此 ID；为 None 时项目 workspace_id 置 NULL
                         （会立即触发默认工作区归集，确保不留孤儿项目）

        Returns:
            是否成功（默认工作区 'default' 不可删除，防止误操作）
        """
        return self.workspaces.delete(workspace_id, reassign_to)

    # ===== 项目管理 =====

    def create_project(
        self,
        name: str,
        description: str = "",
        base_path: str = "",
        workspace_id: str | None = None
    ) -> Project:
        """创建项目。

        workspace_id 指定所属工作区；为 None 时归入"默认工作区"（id='default'），
        保证新项目不会成为孤儿。
        """
        return self.projects.create(name, description, base_path, workspace_id)

    def get_projects(self, workspace_id: str | None = None) -> list[Project]:
        """获取项目列表。

        Args:
            workspace_id: 指定则只返回该工作区下的项目；None 返回所有项目
        """
        return self.projects.list_all(workspace_id)

    def get_project(self, project_id: str) -> Project | None:
        """获取单个项目"""
        return self.projects.get(project_id)

    def update_project_result(self, project_id: str, **kwargs) -> OperationResult:
        return self.projects.update_result(project_id, **kwargs)

    def update_project(self, project_id: str, **kwargs) -> bool:
        return bool(self.update_project_result(project_id, **kwargs))

    def delete_project_result(self, project_id: str, retention_days: int = 30) -> OperationResult:
        """软删除项目，保留素材、标签、日志和备份关联快照。"""
        return self.projects.delete_result(project_id, retention_days)

    def delete_project(self, project_id: str) -> bool:
        return bool(self.delete_project_result(project_id))

    # ===== 项目模板 =====

    def create_project_template(
        self,
        name: str,
        description: str = "",
        base_path: str = "",
        notes: str = "",
    ) -> ProjectTemplate:
        """创建项目模板。"""
        return self.templates.create_project(name, description, base_path, notes)

    def get_project_templates(self) -> list[ProjectTemplate]:
        """获取所有项目模板，按创建时间倒序。"""
        return self.templates.list_projects()

    def get_project_template(self, template_id: str) -> ProjectTemplate | None:
        """获取单个项目模板。"""
        return self.templates.get_project(template_id)

    def update_project_template(self, template_id: str, **kwargs) -> bool:
        """更新项目模板（支持 name / description / base_path / notes）。"""
        return self.templates.update_project(template_id, **kwargs)

    def delete_project_template(self, template_id: str) -> bool:
        """删除项目模板。"""
        return self.templates.delete_project(template_id)

    # ===== 备份方案模板 =====

    def create_backup_template(
        self,
        name: str,
        target_paths: list[str],
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        verify_after_copy: bool = True,
        description: str = "",
    ) -> BackupTemplate:
        """创建备份方案模板。目标路径支持 ``{source_name}`` 占位符。"""
        return self.templates.create_backup(
            name, target_paths, algorithm, verify_after_copy, description
        )

    def get_backup_templates(self) -> list[BackupTemplate]:
        """获取备份方案模板，按创建时间倒序。"""
        return self.templates.list_backups()

    def get_backup_template(self, template_id: str) -> BackupTemplate | None:
        return self.templates.get_backup(template_id)

    def update_backup_template(self, template_id: str, **kwargs) -> bool:
        """更新备份模板字段。"""
        return self.templates.update_backup(template_id, **kwargs)

    def delete_backup_template(self, template_id: str) -> bool:
        return self.templates.delete_backup(template_id)

    # ===== 拍摄日志 =====

    def create_shooting_log(self, log: ShootingLog) -> ShootingLog:
        """创建拍摄日志"""
        return self.logs.create_shooting(log)

    def get_shooting_logs(self, project_id: str) -> list[ShootingLog]:
        """获取项目的拍摄日志"""
        return self.logs.list_shooting(project_id)

    def get_shooting_log(self, log_id: str) -> ShootingLog | None:
        """获取单个拍摄日志"""
        return self.logs.get_shooting(log_id)

    def update_shooting_log(self, log: ShootingLog):
        """更新拍摄日志"""
        self.logs.update_shooting(log)

    # ===== 保存搜索 / 智能集合 =====

    def create_saved_search(self, name, filters, *, workspace_id=None, project_id=None, is_smart=False) -> dict:
        """创建保存搜索/智能集合。"""
        return self.saved_searches.create(name, filters, workspace_id=workspace_id,
                                          project_id=project_id, is_smart=is_smart)

    def get_saved_searches(self, limit=None, workspace_id=None, project_id=None) -> list[dict]:
        """获取保存搜索列表（按更新时间倒序，默认取配置上限）。"""
        if limit is None:
            limit = int(getattr(config, "saved_search_limit", 20) or 20)
        return self.saved_searches.list(limit, workspace_id, project_id)

    def get_saved_search(self, search_id: str) -> dict | None:
        """获取单个保存搜索。"""
        return self.saved_searches.get(search_id)

    def update_saved_search(self, search_id: str, **kwargs) -> bool:
        """更新保存搜索（name / filters / is_smart）。"""
        return self.saved_searches.update(search_id, **kwargs)

    def delete_saved_search(self, search_id: str) -> bool:
        """删除保存搜索。"""
        return self.saved_searches.delete(search_id)

    def delete_shooting_log(self, log_id: str):
        """删除拍摄日志（级联清除关联素材的 log_id 与 scene/shot）

        背景：create_log_with_assets(sync_scene_shot=True) 会把 log.scene/shot
        冗余写入 media_assets。如果删除日志时只清 log_id，会留下"无主"的
        scene/shot，导致后续检索/报告把残留字段当作有效数据。
        故删除日志时一并清空 scene/shot，保持数据一致性。
        """
        self.logs.delete_shooting(log_id)

    def create_log_with_assets(
        self,
        log: ShootingLog,
        asset_ids: list[str],
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
        return self.logs.create_shooting_with_assets(log, asset_ids, sync_scene_shot)

    # ===== 素材资产 =====

    def _sync_asset_tags(self, conn, asset_id: str, tags: str):
        """把 tags 逗号字符串同步到 asset_tags 关联表（须在事务内调用）。"""
        self.assets.sync_tags(conn, asset_id, tags)

    def add_media_asset(self, asset: MediaAsset) -> MediaAsset:
        """添加素材资产"""
        return self.assets.create(asset)

    def add_media_assets_batch(self, assets: list[MediaAsset]) -> int:
        """批量添加素材资产"""
        return self.assets.create_batch(assets)

    def get_media_assets(
        self, project_id: str, limit: int | None = None, offset: int | None = None,
    ) -> list[MediaAsset]:
        """兼容旧调用方的素材读取接口。

        新代码必须使用 ``iter_project_assets``、``get_project_asset_page`` 或
        ``get_search_asset_page``；当未提供 ``limit`` 时本接口会物化整个项目。
        """
        return self.assets.list_all(project_id, limit, offset)

    def iter_project_assets(self, project_id: str, batch_size: int = 500):
        """以固定批次迭代项目素材，供导出、归档和校验等长任务使用。"""
        yield from self.assets.iter_project(project_id, batch_size)

    def count_project_assets(self, project_id: str) -> int:
        return self.assets.count_project(project_id)

    def get_project_asset_page(
        self, project_id: str, page_size: int = 500,
        cursor: tuple[str, str] | None = None,
    ) -> tuple[list[MediaAsset], tuple[str, str] | None]:
        """基于 ``date_imported, asset_id`` 的 keyset 分页读取项目素材。"""
        return self.assets.get_page(project_id, page_size, cursor)

    def get_media_asset(self, asset_id: str) -> MediaAsset | None:
        """获取单个素材资产"""
        return self.assets.get(asset_id)

    def _record_operation_in_transaction(
        self, conn, event: str, detail: str = "", project_id: str | None = None,
        status: str = OperationStatus.SUCCESS.value, object_type: str = "",
        object_id: str = "", recovery_id: str = "",
    ) -> str:
        return self.logs.record_in_transaction(
            conn, event, detail, project_id, status, object_type, object_id, recovery_id,
        )

    def _store_recycle_snapshot(
        self, conn, entity_type: str, entity_id: str, project_id: str | None, snapshot: dict,
        retention_days: int = 30,
    ) -> str:
        return self.recycle.store_snapshot(conn, entity_type, entity_id, project_id, snapshot, retention_days)

    def _asset_snapshot(self, conn, row: sqlite3.Row) -> dict:
        return self.assets.snapshot(conn, row)

    def _delete_asset_rows(self, conn, asset_ids: list[str]) -> None:
        self.assets.delete_rows(conn, asset_ids)

    def update_media_asset_result(self, asset_id: str, **kwargs) -> OperationResult:
        """更新素材并区分参数、未找到与数据库错误。"""
        return self.assets.update_result(asset_id, **kwargs)

    def update_media_asset(self, asset_id: str, **kwargs) -> bool:
        """兼容旧调用方的布尔更新接口。"""
        return bool(self.update_media_asset_result(asset_id, **kwargs))

    def delete_media_asset_result(self, asset_id: str, retention_days: int = 30) -> OperationResult:
        return self.delete_media_assets_result([asset_id], retention_days=retention_days)

    def delete_media_assets_result(self, asset_ids: list[str], retention_days: int = 30) -> OperationResult:
        """软删除素材记录，保留标签和完整元数据快照。"""
        return self.assets.delete_result(asset_ids, retention_days)

    def delete_media_asset(self, asset_id: str) -> bool:
        return bool(self.delete_media_asset_result(asset_id))

    def delete_media_assets(self, asset_ids: list[str]) -> int:
        return self.delete_media_assets_result(asset_ids).affected_count

    def relink_media_assets(self, project_id: str, updates: list[tuple[str, str, str, int]]) -> OperationResult:
        """原子回写预览中确认的素材路径，并记录重定位审计。"""
        return self.assets.relink_result(project_id, updates)

    @staticmethod
    def _insert_snapshot_row(conn, table: str, values: dict) -> None:
        """Restore a row using only columns available in the current schema."""
        RecycleRepository.insert_snapshot_row(conn, table, values)

    def _restore_asset_snapshot(self, conn, snapshot: dict) -> None:
        self.recycle.restore_asset_snapshot(conn, snapshot)

    def get_recycle_bin_items(self, project_id: str | None = None) -> list[dict]:
        """返回未过期回收站项，供恢复入口和健康检查使用。"""
        return self.recycle.list_items(project_id)

    def restore_recycle_item(self, recycle_id: str) -> OperationResult:
        """恢复单个素材或项目快照；冲突时不覆盖当前记录。"""
        return self.recycle.restore_item(recycle_id)

    def restore_all_recycle_items(self) -> OperationResult:
        """尽可能恢复全部回收站记录，并优先恢复项目以满足素材依赖。"""
        return self.recycle.restore_all()

    def empty_recycle_bin(self) -> OperationResult:
        """永久清空回收站快照；不会触碰任何源媒体文件。"""
        return self.recycle.empty()

    def cleanup_expired_recycle_bin(self, now: datetime | None = None) -> OperationResult:
        """永久清理超过保留期的快照；不会触碰任何源媒体文件。"""
        return self.recycle.cleanup_expired(now)

    def create_rename_history(
        self, mappings: list[tuple[str, str]], project_id: str | None = None,
    ) -> OperationResult:
        """持久化一次成功的文件重命名映射，供安全回退。"""
        return self.renames.create(mappings, project_id)

    def get_rename_history(self, rename_id: str) -> OperationResult:
        return self.renames.get(rename_id)

    def mark_rename_history_reverted(self, rename_id: str) -> OperationResult:
        return self.renames.mark_reverted(rename_id)

    def get_missing_file_asset_ids(self, project_id: str) -> list[str]:
        """返回当前项目中文件已丢失（路径为空或磁盘上不存在）的素材 asset_id 列表。

        实时扫描磁盘，是「文件存在性验证」的事实来源，供素材信息列表的
        “文件已丢失”标识与「一键清理丢失素材」功能共用，确保二者结论一致。

        Args:
            project_id: 项目 ID

        Returns:
            文件已丢失的素材 asset_id 列表（按项目素材默认排序）
        """
        return self.assets.missing_file_ids(project_id)

    def asset_exists_by_path(self, project_id: str, file_path: str) -> bool:
        """按文件路径查重"""
        return self.assets.exists_by_path(project_id, file_path)

    def existing_asset_paths(self, project_id: str, file_paths: list[str]) -> set:
        """批量查重：返回 file_paths 中已存在于该项目的规范化路径集合。

        一次 IN 查询取回全部已存在路径，替代逐文件 SELECT 的 N+1 查询，
        适合大卡导入（数千文件）场景。

        Args:
            project_id: 项目 ID
            file_paths: 待检查的原始文件路径列表

        Returns:
            已存在的规范化路径集合（与入库存储形式一致）
        """
        return self.assets.existing_paths(project_id, file_paths)

    def get_asset_log_id_by_path(self, file_path: str) -> str | None:
        """按文件路径全局查询素材关联的 log_id（不限 project_id）。

        供 RAW 提取等场景使用：JPG 可能在任意项目，按路径反查其 log_id
        以便把提取出的 RAW 文件继承同一日志关联。

        注意：file_path 必须经过 normalize_path() 规范化，与入库时的
        存储形式保持一致，否则在符号链接 / 相对路径场景下会匹配失败。
        """
        return self.assets.log_id_by_path(file_path)

    def asset_exists_by_checksum(self, project_id: str, checksum_value: str) -> bool:
        """按校验和查重"""
        return self.assets.exists_by_checksum(project_id, checksum_value)

    def find_duplicate_assets(self) -> list[dict]:
        """跨项目按校验和聚合，找出重复入库的素材。

        同一文件被导入多个项目、或同卡多次导入时会留下相同 checksum_value
        的多条记录。此方法按 (checksum_value, checksum_algorithm) 分组，
        仅返回出现次数 > 1 的组，供用户人工判定是否清理。

        Returns:
            [{"checksum_value", "algorithm", "count", "assets": [MediaAsset]}]
            按出现次数降序排列。
        """
        return self.assets.find_duplicates()

    def update_asset_path(self, asset_id: str, new_path: str, new_name: str | None = None) -> bool:
        """更新素材路径"""
        return self.assets.update_path(asset_id, new_path, new_name)

    def update_asset_path_by_old_path(
        self, old_path: str, new_path: str, new_name: str | None = None
    ) -> bool:
        """按旧文件路径查找 asset 并更新为新路径/文件名（用于重命名后同步 DB）。

        old_path / new_path 均会经过 normalize_path() 规范化，与入库时的
        存储形式保持一致，避免符号链接 / 相对路径场景下匹配失败。

        Returns:
            True 表示找到并更新；False 表示未找到匹配 asset（如该文件未入库）
        """
        return self.assets.update_path_by_old_path(old_path, new_path, new_name)

    def _asset_filter_clause(
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
    ) -> tuple:
        """构造素材筛选子句 (WHERE_SQL, params)，供 search_assets / count_assets 复用。"""
        return self.assets._filter_clause(
            project_id, scene, shot, file_type, date_from, date_to, keyword,
            log_id, rating, tag, taken_from, taken_to,
        )

    def get_capture_timeline(
        self, project_id: str | None = None, *, granularity: str = "day", limit: int = 366,
    ) -> list[dict]:
        """返回按拍摄日期聚合的素材时间线。"""
        return self.assets.capture_timeline(project_id, granularity=granularity, limit=limit)

    def _search_sql(self, **filters):
        """构造带 FTS 条件的素材查询 SQL 与参数。"""
        return self.assets._search_sql(**filters)

    def iter_search_assets(
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
        limit: int | None = None,
        offset: int | None = None,
        batch_size: int = 500,
        cursor: tuple[str, str] | None = None,
    ):
        """按游标批量读取素材，避免导出/批处理一次性加载全部记录。"""
        yield from self.assets.iter_search(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword, log_id=log_id,
            rating=rating, tag=tag, taken_from=taken_from, taken_to=taken_to,
            limit=limit, offset=offset,
            batch_size=batch_size, cursor=cursor,
        )

    def get_search_asset_page(
        self, *, page_size: int = 500, cursor: tuple[str, str] | None = None, **filters,
    ) -> tuple[list[MediaAsset], tuple[str, str] | None]:
        """返回一个 keyset 页面和下一页游标，避免深页 OFFSET 扫描。"""
        return self.assets.get_search_page(page_size=page_size, cursor=cursor, **filters)

    def search_assets(
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
        limit: int | None = None,
        offset: int | None = None
    ) -> list[MediaAsset]:
        """搜索素材（支持分页）。

        Args:
            rating: 按 rating 过滤（>=rating）。None 表示不过滤。
            tag: 按自定义标签过滤（基于 asset_tags 关联表，子串匹配）。
            limit: 返回结果上限。None 表示不限制。
            offset: 分页偏移。None 表示从第一条开始。
        """
        return self.assets.search(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword,
            log_id=log_id, rating=rating, tag=tag, taken_from=taken_from, taken_to=taken_to,
            limit=limit, offset=offset,
        )

    def count_assets(
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
    ) -> int:
        """统计符合条件的素材总数（与 search_assets 同条件）。"""
        return self.assets.count(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword,
            log_id=log_id, rating=rating, tag=tag, taken_from=taken_from, taken_to=taken_to,
        )

    def get_all_tags(self) -> list[str]:
        """返回全部素材标签（去重、按使用次数降序），供检索自动补全。"""
        return self.assets.all_tags()

    def get_assets_by_log_id(self, log_id: str) -> list[MediaAsset]:
        """按拍摄日志ID获取关联的素材"""
        return self.assets.by_log_id(log_id)

    def update_media_asset_log_id(self, asset_id: str, log_id: str | None) -> bool:
        """更新素材关联的拍摄日志。

        - log_id=None 表示解除关联，同时清空 scene/shot，避免遗留"无主"字段
          （与 delete_shooting_log 保持一致的数据一致性策略）。
        - log_id 非 None 时只更新 log_id，scene/shot 由调用方另行处理
          （如 create_log_with_assets 的 sync_scene_shot 路径）。
        """
        return self.assets.update_log_id(asset_id, log_id)

    # ===== 统计查询 =====

    def get_project_stats(self, project_id: str) -> dict:
        """获取项目统计信息"""
        with self._connection() as conn:
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

            backup_job_count = conn.execute(
                "SELECT COUNT(*) FROM backup_jobs WHERE project_id = ?",
                (project_id,)
            ).fetchone()[0]

            backed_up_count = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND backup_locations != ''",
                (project_id,)
            ).fetchone()[0]

            linked_asset_count = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND log_id IS NOT NULL",
                (project_id,)
            ).fetchone()[0]

            return {
                "asset_count": asset_count,
                "log_count": log_count,
                "total_size": total_size,
                "backup_job_count": backup_job_count,
                "backed_up_count": backed_up_count,
                "linked_asset_count": linked_asset_count,
            }

    # ===== 操作审计日志 =====

    def record_operation(
        self,
        event: str,
        detail: str = "",
        project_id: str | None = None,
        status: str = OperationStatus.SUCCESS.value,
        object_type: str = "",
        object_id: str = "",
        recovery_id: str = "",
    ) -> bool:
        """记录一条操作审计日志（导入/备份/重命名/评级等关键操作）。

        Args:
            event: 事件类型（如 "导入素材"、"数据备份"）
            detail: 事件详情（如成功/失败统计）
            project_id: 关联项目（可空）

        Returns:
            是否写入成功
        """
        return self.logs.record(
            event, detail, project_id, status, object_type, object_id, recovery_id,
        )

    def get_recent_operations(
        self,
        limit: int = 10,
        project_id: str | None = None,
        event: str = "",
        status: str = "",
        object_type: str = "",
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[dict]:
        """按项目、事件、结果、对象与时间筛选审计日志（按时间倒序）。"""
        return self.logs.list_recent(
            limit, project_id, event, status, object_type, date_from, date_to,
        )

    def cleanup_history(
        self,
        now: datetime | None = None,
        *,
        operation_log_retention_days: int | None = None,
        task_history_limit_per_project: int | None = None,
    ) -> dict[str, int]:
        """按保留策略清理审计日志和任务历史。

        清理只影响历史记录，不触碰素材、项目、备份作业或恢复快照。参数可覆盖
        全局配置，供维护命令和测试传入确定性的时间与容量。
        """
        retention_days = (
            config.operation_log_retention_days
            if operation_log_retention_days is None else operation_log_retention_days
        )
        task_limit = (
            config.task_history_limit_per_project
            if task_history_limit_per_project is None else task_history_limit_per_project
        )
        if not isinstance(retention_days, int) or isinstance(retention_days, bool) or retention_days <= 0:
            raise ValueError("operation_log_retention_days 必须是正整数")
        if not isinstance(task_limit, int) or isinstance(task_limit, bool) or task_limit <= 0:
            raise ValueError("task_history_limit_per_project 必须是正整数")

        current_time = now or now_local()
        deleted_logs = self.logs.delete_older_than(
            current_time - timedelta(days=retention_days)
        )
        deleted_tasks = self.tasks.prune_per_project(task_limit)
        if deleted_logs or deleted_tasks:
            logger.info(
                f"历史记录清理完成: 操作日志 {deleted_logs} 条，任务历史 {deleted_tasks} 条"
            )
        return {"operation_logs": deleted_logs, "task_history": deleted_tasks}

    # ===== 统一任务历史 =====

    def create_task_history(
        self, task_name: str, project_id: str | None = None, *,
        state: str = "queued", parameters: dict | None = None,
        recovery_info: dict | None = None,
    ) -> str:
        """创建可审计的后台任务记录，返回稳定任务 ID。"""
        return self.tasks.create(
            task_name, project_id, state=state, parameters=parameters, recovery_info=recovery_info,
        )

    def update_task_history(
        self, task_id: str, state: str, *, output: dict | None = None,
        error_summary: str = "", recovery_info: dict | None = None,
    ) -> bool:
        """更新任务状态与可恢复上下文；终态自动写入结束时间。"""
        return self.tasks.update(
            task_id, state, output=output, error_summary=error_summary, recovery_info=recovery_info,
        )

    def get_task_history(self, project_id: str | None = None, limit: int = 100) -> list[dict]:
        """读取最近任务，供健康页、审计与恢复入口使用。"""
        return self.tasks.list_all(project_id, limit)

    # ===== 持久化校验和缓存 =====

    def get_checksum_cache(
        self, file_path: str, file_size: int, mtime_ns: int, algorithm: str,
    ) -> str | None:
        return self.checksum_cache.get(file_path, file_size, mtime_ns, algorithm)

    def put_checksum_cache(
        self, file_path: str, file_size: int, mtime_ns: int, algorithm: str,
        hash_value: str, limit: int = 100_000,
    ) -> None:
        self.checksum_cache.put(
            file_path, file_size, mtime_ns, algorithm, hash_value, limit,
        )

    def clear_checksum_cache(self) -> None:
        self.checksum_cache.clear()

    def count_checksum_cache(self) -> int:
        return self.checksum_cache.count()

    # ===== 项目健康容量快照 =====

    def record_storage_health_snapshot(
        self, project_id: str | None, target_path: str, total_bytes: int, free_bytes: int,
    ) -> bool:
        """记录可达备份目标的容量，用于健康页展示近期趋势。"""
        return self.tasks.record_storage_health(project_id, target_path, total_bytes, free_bytes)

    def get_storage_health_snapshots(self, project_id: str | None, limit: int = 60) -> list[dict]:
        """读取项目近期的目标容量快照，按采集时间正序返回。"""
        return self.tasks.list_storage_health(project_id, limit)

    # ===== 备份作业持久化 =====

    def save_backup_job(self, job, project_id: str | None = None) -> bool:
        """持久化备份作业（含 targets 列表的 JSON 序列化）。

        Args:
            job: BackupJob 实例
            project_id: 关联项目 ID（可空，表示未关联项目的备份）
        Returns:
            是否成功
        """
        return self.backup_jobs.save(job, project_id)

    def get_backup_job(self, job_id: str) -> dict | None:
        """获取单个备份作业的原始 dict；不存在返回 None。"""
        return self.backup_jobs.get(job_id)

    def get_backup_jobs(self, project_id: str | None = None) -> list[dict]:
        """获取备份作业列表（原始 dict 形式，由调用方解析）。

        Args:
            project_id: 若提供则只返回该项目的备份；None 返回全部
        """
        return self.backup_jobs.list_all(project_id)

    def add_backup_location_to_assets(
        self,
        file_paths: list[str],
        target_path: str,
        project_id: str | None = None
    ) -> int:
        """批量把 target_path 追加到匹配 file_path 的 asset 的 backup_locations。

        匹配规则：asset.file_path 等于 file_paths 中任一即视为同一文件。
        若 asset 已有该 target_path，则跳过（幂等）。

        性能：一次 IN 查询批量取回全部匹配 asset，再逐条 UPDATE，
        替代原实现逐文件 SELECT 的 N+1 查询。

        Args:
            file_paths: 备份源文件路径列表
            target_path: 备份目标路径
            project_id: 可选，限定项目范围
        Returns:
            成功更新的 asset 数量
        """
        return self.assets.add_backup_locations(file_paths, target_path, project_id)

    # ===== 辅助方法 =====

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> MediaAsset:
        """兼容仍在门面层的搜索和恢复流程，复用素材仓储的行映射。"""
        return AssetRepository._row_to_asset(row)
