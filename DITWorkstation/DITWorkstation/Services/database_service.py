"""拍摄日志与项目管理服务 - SQLite持久化"""
import sqlite3
import threading
import uuid
import shutil
import re
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from DITWorkstation.App import config
from DITWorkstation.Models import (
    Project, ProjectTemplate, BackupTemplate, ShootingLog, MediaAsset, Workspace,
    ChecksumAlgorithm, OperationResult, OperationStatus,
)
from DITWorkstation.Utils import logger, normalize_path
from DITWorkstation.Services.repositories.field_registry import (
    BACKUP_TEMPLATE_FIELDS,
    PROJECT_FIELDS,
    PROJECT_TEMPLATE_FIELDS,
    WORKSPACE_FIELDS,
)
from DITWorkstation.Services.repositories.workspace_repository import WorkspaceRepository
from DITWorkstation.Services.repositories.template_repository import TemplateRepository
from DITWorkstation.Services.repositories.project_repository import ProjectRepository
from DITWorkstation.Services.repositories.log_repository import LogRepository
from DITWorkstation.Services.repositories.asset_repository import AssetRepository


def _chunked(seq, size):
    """把序列切分为固定大小的块（SQLite 变量上限 999，保守取 500）。"""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class DatabaseService:
    """数据库服务 - SQLite持久化"""

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        """解析历史时间字段；损坏的旧值不应阻断列表或报告读取。"""
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def __init__(self, db_path: Optional[Path] = None):
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
    _DB_VERSION = 7

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
                "DELETE FROM recycle_bin WHERE expires_at <= ?", (datetime.now().isoformat(),)
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
                    datetime.now().isoformat(),
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
            now = datetime.now().isoformat()
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
        return None

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
    def _fts_query(keyword: str) -> Optional[str]:
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
            now = datetime.now().isoformat()
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

    def get_workspaces(self) -> List[Workspace]:
        """获取所有工作区，按创建时间倒序"""
        return self.workspaces.list_all()

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
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

    def delete_workspace(self, workspace_id: str, reassign_to: Optional[str] = None) -> bool:
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
        workspace_id: Optional[str] = None
    ) -> Project:
        """创建项目。

        workspace_id 指定所属工作区；为 None 时归入"默认工作区"（id='default'），
        保证新项目不会成为孤儿。
        """
        return self.projects.create(name, description, base_path, workspace_id)

    def get_projects(self, workspace_id: Optional[str] = None) -> List[Project]:
        """获取项目列表。

        Args:
            workspace_id: 指定则只返回该工作区下的项目；None 返回所有项目
        """
        return self.projects.list_all(workspace_id)

    def get_project(self, project_id: str) -> Optional[Project]:
        """获取单个项目"""
        return self.projects.get(project_id)

    def _legacy_update_project(self, project_id: str, **kwargs) -> bool:
        """兼容旧内部调用，委托给当前项目更新入口。"""
        return bool(self.update_project_result(project_id, **kwargs))

    def _legacy_delete_project(self, project_id: str):
        """删除项目（事务级联清理 media_assets + shooting_logs + projects）

        注意：backup_jobs 表无 project_id 外键，保留备份历史记录不级联删除。
        """
        self.projects.delete_legacy(project_id)

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

    def get_project_templates(self) -> List[ProjectTemplate]:
        """获取所有项目模板，按创建时间倒序。"""
        return self.templates.list_projects()

    def get_project_template(self, template_id: str) -> Optional[ProjectTemplate]:
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
        target_paths: List[str],
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        verify_after_copy: bool = True,
        description: str = "",
    ) -> BackupTemplate:
        """创建备份方案模板。目标路径支持 ``{source_name}`` 占位符。"""
        return self.templates.create_backup(
            name, target_paths, algorithm, verify_after_copy, description
        )

    def get_backup_templates(self) -> List[BackupTemplate]:
        """获取备份方案模板，按创建时间倒序。"""
        return self.templates.list_backups()

    def get_backup_template(self, template_id: str) -> Optional[BackupTemplate]:
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

    def get_shooting_logs(self, project_id: str) -> List[ShootingLog]:
        """获取项目的拍摄日志"""
        return self.logs.list_shooting(project_id)

    def get_shooting_log(self, log_id: str) -> Optional[ShootingLog]:
        """获取单个拍摄日志"""
        return self.logs.get_shooting(log_id)

    def update_shooting_log(self, log: ShootingLog):
        """更新拍摄日志"""
        self.logs.update_shooting(log)

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
        return self.logs.create_shooting_with_assets(log, asset_ids, sync_scene_shot)

    # ===== 素材资产 =====

    def _sync_asset_tags(self, conn, asset_id: str, tags: str):
        """把 tags 逗号字符串同步到 asset_tags 关联表（须在事务内调用）。"""
        self.assets.sync_tags(conn, asset_id, tags)

    def add_media_asset(self, asset: MediaAsset) -> MediaAsset:
        """添加素材资产"""
        return self.assets.create(asset)

    def add_media_assets_batch(self, assets: List[MediaAsset]) -> int:
        """批量添加素材资产"""
        return self.assets.create_batch(assets)

    def get_media_assets(
        self, project_id: str, limit: Optional[int] = None, offset: Optional[int] = None,
    ) -> List[MediaAsset]:
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
        cursor: Optional[tuple[str, str]] = None,
    ) -> tuple[List[MediaAsset], Optional[tuple[str, str]]]:
        """基于 ``date_imported, asset_id`` 的 keyset 分页读取项目素材。"""
        return self.assets.get_page(project_id, page_size, cursor)

    def get_media_asset(self, asset_id: str) -> Optional[MediaAsset]:
        """获取单个素材资产"""
        return self.assets.get(asset_id)

    def _legacy_update_media_asset(self, asset_id: str, **kwargs) -> bool:
        """兼容旧内部调用，委托给当前素材更新入口。"""
        return bool(self.update_media_asset_result(asset_id, **kwargs))

    def _legacy_delete_media_asset(self, asset_id: str) -> bool:
        """删除素材资产"""
        try:
            with self._transaction() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = ?", (asset_id,)
                ).fetchone()
                if exists is None:
                    logger.warning(f"素材资产不存在，未删除: {asset_id}")
                    return False
                if self._fts_available:
                    conn.execute("DELETE FROM media_assets_fts WHERE asset_id = ?", (asset_id,))
                conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
                conn.execute("DELETE FROM media_assets WHERE asset_id = ?", (asset_id,))
                logger.info(f"删除素材资产: {asset_id}")
                return True
        except Exception as e:
            logger.error(f"删除素材资产失败 {asset_id}: {e}")
            return False

    def _legacy_delete_media_assets(self, asset_ids: List[str]) -> int:
        """批量删除素材资产记录（不触碰磁盘文件），返回成功删除的条数。

        用于「清理文件已丢失的素材」场景：仅移除数据库中失效的路径记录，
        保持数据库与实际文件系统一致，绝不删除磁盘上的源文件。

        Args:
            asset_ids: 待删除素材的 asset_id 列表

        Returns:
            实际删除的记录数量；重复或不存在的 ID 不重复计数。
        """
        unique_ids = list(dict.fromkeys(aid for aid in asset_ids if aid))
        if not unique_ids:
            return 0

        deleted = 0
        try:
            # SQLite 参数数量存在上限，分块但保持在同一事务中，避免 N 次提交。
            with self._transaction() as conn:
                for chunk in _chunked(unique_ids, 500):
                    placeholders = ", ".join("?" for _ in chunk)
                    params = tuple(chunk)
                    if self._fts_available:
                        conn.execute(
                            f"DELETE FROM media_assets_fts WHERE asset_id IN ({placeholders})",
                            params,
                        )
                    conn.execute(
                        f"DELETE FROM asset_tags WHERE asset_id IN ({placeholders})",
                        params,
                    )
                    cursor = conn.execute(
                        f"DELETE FROM media_assets WHERE asset_id IN ({placeholders})",
                        params,
                    )
                    deleted += max(cursor.rowcount, 0)
        except Exception as e:
            logger.error(f"批量删除素材资产失败: {e}")
            return 0
        logger.info(f"批量删除素材资产: 成功 {deleted}/{len(asset_ids)}")
        return deleted

    def _record_operation_in_transaction(
        self, conn, event: str, detail: str = "", project_id: Optional[str] = None,
        status: str = OperationStatus.SUCCESS.value, object_type: str = "",
        object_id: str = "", recovery_id: str = "",
    ) -> str:
        return self.logs.record_in_transaction(
            conn, event, detail, project_id, status, object_type, object_id, recovery_id,
        )

    def _store_recycle_snapshot(
        self, conn, entity_type: str, entity_id: str, project_id: Optional[str], snapshot: dict,
        retention_days: int = 30,
    ) -> str:
        recycle_id = str(uuid.uuid4())
        now = datetime.now()
        conn.execute(
            "INSERT INTO recycle_bin "
            "(recycle_id, entity_type, entity_id, project_id, snapshot_json, deleted_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (recycle_id, entity_type, entity_id, project_id,
             json.dumps(snapshot, ensure_ascii=False), now.isoformat(),
             (now + timedelta(days=retention_days)).isoformat()),
        )
        return recycle_id

    def _asset_snapshot(self, conn, row: sqlite3.Row) -> dict:
        tags = [item["tag"] for item in conn.execute(
            "SELECT tag FROM asset_tags WHERE asset_id = ? ORDER BY tag", (row["asset_id"],)
        )]
        return {"asset": dict(row), "tags": tags}

    def _delete_asset_rows(self, conn, asset_ids: List[str]) -> None:
        for chunk in _chunked(asset_ids, 500):
            placeholders = ", ".join("?" for _ in chunk)
            if self._fts_available:
                conn.execute(f"DELETE FROM media_assets_fts WHERE asset_id IN ({placeholders})", chunk)
            conn.execute(f"DELETE FROM asset_tags WHERE asset_id IN ({placeholders})", chunk)
            conn.execute(f"DELETE FROM media_assets WHERE asset_id IN ({placeholders})", chunk)

    def update_media_asset_result(self, asset_id: str, **kwargs) -> OperationResult:
        """更新素材并区分参数、未找到与数据库错误。"""
        return self.assets.update_result(asset_id, **kwargs)

    def update_media_asset(self, asset_id: str, **kwargs) -> bool:
        """兼容旧调用方的布尔更新接口。"""
        return bool(self.update_media_asset_result(asset_id, **kwargs))

    def delete_media_asset_result(self, asset_id: str, retention_days: int = 30) -> OperationResult:
        return self.delete_media_assets_result([asset_id], retention_days=retention_days)

    def delete_media_assets_result(self, asset_ids: List[str], retention_days: int = 30) -> OperationResult:
        """软删除素材记录，保留标签和完整元数据快照。"""
        unique_ids = list(dict.fromkeys(asset_id for asset_id in asset_ids if asset_id))
        if not unique_ids:
            return OperationResult(OperationStatus.INVALID, "未提供素材 ID")
        try:
            with self._transaction() as conn:
                rows = []
                for chunk in _chunked(unique_ids, 500):
                    placeholders = ", ".join("?" for _ in chunk)
                    rows.extend(conn.execute(
                        f"SELECT * FROM media_assets WHERE asset_id IN ({placeholders})", chunk
                    ).fetchall())
                if not rows:
                    return OperationResult(OperationStatus.NOT_FOUND, "素材不存在")
                recovery_ids = []
                for row in rows:
                    recovery_ids.append(self._store_recycle_snapshot(
                        conn, "asset", row["asset_id"], row["project_id"],
                        self._asset_snapshot(conn, row), retention_days,
                    ))
                self._delete_asset_rows(conn, [row["asset_id"] for row in rows])
                for row, recovery_id in zip(rows, recovery_ids):
                    self._record_operation_in_transaction(
                        conn, "删除素材", "素材记录已移入回收站", row["project_id"],
                        object_type="asset", object_id=row["asset_id"], recovery_id=recovery_id,
                    )
            return OperationResult(
                OperationStatus.SUCCESS, affected_count=len(rows), recovery_id=recovery_ids[0],
                value=recovery_ids,
            )
        except sqlite3.Error as exc:
            logger.error(f"软删除素材失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def delete_media_asset(self, asset_id: str) -> bool:
        return bool(self.delete_media_asset_result(asset_id))

    def delete_media_assets(self, asset_ids: List[str]) -> int:
        return self.delete_media_assets_result(asset_ids).affected_count

    def relink_media_assets(self, project_id: str, updates: List[tuple[str, str, str, int]]) -> OperationResult:
        """原子回写预览中确认的素材路径，并记录重定位审计。"""
        if not updates:
            return OperationResult(OperationStatus.INVALID, "没有重新链接更新")
        try:
            with self._transaction() as conn:
                if conn.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone() is None:
                    return OperationResult(OperationStatus.NOT_FOUND, f"项目不存在: {project_id}")
                seen_paths = set()
                for asset_id, new_path, _name, _size in updates:
                    if new_path in seen_paths:
                        return OperationResult(OperationStatus.CONFLICT, f"多个素材选择了同一路径: {new_path}")
                    seen_paths.add(new_path)
                    row = conn.execute(
                        "SELECT asset_id FROM media_assets WHERE asset_id = ? AND project_id = ?",
                        (asset_id, project_id),
                    ).fetchone()
                    if row is None:
                        return OperationResult(OperationStatus.NOT_FOUND, f"素材不存在或不属于项目: {asset_id}")
                    conflict = conn.execute(
                        "SELECT asset_id FROM media_assets WHERE project_id = ? AND file_path = ? AND asset_id != ?",
                        (project_id, new_path, asset_id),
                    ).fetchone()
                    if conflict is not None:
                        return OperationResult(OperationStatus.CONFLICT, f"路径已关联其他素材: {new_path}")
                for asset_id, new_path, name, size in updates:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ?, file_name = ?, file_size = ? WHERE asset_id = ?",
                        (new_path, name, size, asset_id),
                    )
                    self._refresh_asset_fts(conn, asset_id)
                    self._record_operation_in_transaction(
                        conn, "重新链接素材", new_path, project_id,
                        object_type="asset", object_id=asset_id,
                    )
            return OperationResult(OperationStatus.SUCCESS, affected_count=len(updates))
        except sqlite3.Error as exc:
            logger.error(f"重新链接素材失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    @staticmethod
    def _insert_snapshot_row(conn, table: str, values: dict) -> None:
        """Restore a row using only columns available in the current schema."""
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        selected = {key: value for key, value in values.items() if key in columns}
        names = list(selected)
        placeholders = ", ".join("?" for _ in names)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
            [selected[name] for name in names],
        )

    def _restore_asset_snapshot(self, conn, snapshot: dict) -> None:
        asset = snapshot["asset"]
        self._insert_snapshot_row(conn, "media_assets", asset)
        for tag in snapshot.get("tags", []):
            conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                (asset["asset_id"], tag),
            )
        self._refresh_asset_fts(conn, asset["asset_id"])

    def get_recycle_bin_items(self, project_id: Optional[str] = None) -> List[dict]:
        """返回未过期回收站项，供恢复入口和健康检查使用。"""
        with self._connection() as conn:
            query = "SELECT recycle_id, entity_type, entity_id, project_id, deleted_at, expires_at FROM recycle_bin"
            params = []
            if project_id:
                query += " WHERE project_id = ?"
                params.append(project_id)
            query += " ORDER BY deleted_at DESC"
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def restore_recycle_item(self, recycle_id: str) -> OperationResult:
        """恢复单个素材或项目快照；冲突时不覆盖当前记录。"""
        try:
            with self._transaction() as conn:
                item = conn.execute("SELECT * FROM recycle_bin WHERE recycle_id = ?", (recycle_id,)).fetchone()
                if item is None:
                    return OperationResult(OperationStatus.NOT_FOUND, f"回收站项不存在: {recycle_id}")
                snapshot = json.loads(item["snapshot_json"])
                if item["entity_type"] == "asset":
                    asset = snapshot["asset"]
                    if conn.execute("SELECT 1 FROM media_assets WHERE asset_id = ?", (asset["asset_id"],)).fetchone():
                        return OperationResult(OperationStatus.CONFLICT, "素材 ID 已被重新使用")
                    if conn.execute("SELECT 1 FROM projects WHERE project_id = ?", (asset["project_id"],)).fetchone() is None:
                        return OperationResult(OperationStatus.CONFLICT, "素材所属项目不存在，无法恢复")
                    self._restore_asset_snapshot(conn, snapshot)
                    project_id = asset["project_id"]
                    object_id = asset["asset_id"]
                elif item["entity_type"] == "project":
                    project = snapshot["project"]
                    if conn.execute("SELECT 1 FROM projects WHERE project_id = ?", (project["project_id"],)).fetchone():
                        return OperationResult(OperationStatus.CONFLICT, "项目 ID 已被重新使用")
                    asset_ids = [asset["asset"]["asset_id"] for asset in snapshot.get("assets", [])]
                    if asset_ids:
                        placeholders = ", ".join("?" for _ in asset_ids)
                        if conn.execute(
                            f"SELECT 1 FROM media_assets WHERE asset_id IN ({placeholders}) LIMIT 1", asset_ids
                        ).fetchone():
                            return OperationResult(OperationStatus.CONFLICT, "项目素材 ID 已被重新使用")
                    workspace_id = project.get("workspace_id")
                    if workspace_id and conn.execute(
                        "SELECT 1 FROM workspaces WHERE workspace_id = ?", (workspace_id,)
                    ).fetchone() is None:
                        project["workspace_id"] = "default"
                        now = datetime.now().isoformat()
                        conn.execute(
                            "INSERT OR IGNORE INTO workspaces "
                            "(workspace_id, name, path, description, created_at, updated_at) "
                            "VALUES ('default', ?, '', ?, ?, ?)",
                            ("默认工作区", "默认工作区", now, now),
                        )
                    self._insert_snapshot_row(conn, "projects", project)
                    for log in snapshot.get("shooting_logs", []):
                        self._insert_snapshot_row(conn, "shooting_logs", log)
                    for asset_snapshot in snapshot.get("assets", []):
                        self._restore_asset_snapshot(conn, asset_snapshot)
                    for job_id in snapshot.get("backup_job_ids", []):
                        conn.execute(
                            "UPDATE backup_jobs SET project_id = ? WHERE job_id = ? AND project_id IS NULL",
                            (project["project_id"], job_id),
                        )
                    project_id = project["project_id"]
                    object_id = project_id
                else:
                    return OperationResult(OperationStatus.INVALID, "不支持的回收站实体类型")
                conn.execute("DELETE FROM recycle_bin WHERE recycle_id = ?", (recycle_id,))
                self._record_operation_in_transaction(
                    conn, "恢复回收站项目", "已恢复数据库记录", project_id,
                    object_type=item["entity_type"], object_id=object_id, recovery_id=recycle_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=1, recovery_id=recycle_id)
        except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
            logger.error(f"恢复回收站项失败 {recycle_id}: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def restore_all_recycle_items(self) -> OperationResult:
        """尽可能恢复全部回收站记录，并优先恢复项目以满足素材依赖。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT recycle_id, entity_type FROM recycle_bin "
                "ORDER BY CASE entity_type WHEN 'project' THEN 0 ELSE 1 END, deleted_at ASC"
            ).fetchall()
        if not rows:
            return OperationResult(OperationStatus.INVALID, "回收站为空")

        failed = []
        restored = 0
        for row in rows:
            result = self.restore_recycle_item(row["recycle_id"])
            if result:
                restored += result.affected_count
            else:
                failed.append({"recycle_id": row["recycle_id"], "message": result.message})

        message = "" if not failed else f"{len(failed)} 项因冲突或数据错误未恢复"
        return OperationResult(
            OperationStatus.SUCCESS, message, value={"failed": failed}, affected_count=restored,
        )

    def empty_recycle_bin(self) -> OperationResult:
        """永久清空回收站快照；不会触碰任何源媒体文件。"""
        try:
            with self._transaction() as conn:
                cursor = conn.execute("DELETE FROM recycle_bin")
            return OperationResult(OperationStatus.SUCCESS, affected_count=max(cursor.rowcount, 0))
        except sqlite3.Error as exc:
            logger.error(f"清空回收站失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def cleanup_expired_recycle_bin(self, now: Optional[datetime] = None) -> OperationResult:
        """永久清理超过保留期的快照；不会触碰任何源媒体文件。"""
        now = now or datetime.now()
        try:
            with self._transaction() as conn:
                cursor = conn.execute("DELETE FROM recycle_bin WHERE expires_at <= ?", (now.isoformat(),))
            return OperationResult(OperationStatus.SUCCESS, affected_count=max(cursor.rowcount, 0))
        except sqlite3.Error as exc:
            logger.error(f"清理回收站失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def create_rename_history(
        self, mappings: List[tuple[str, str]], project_id: Optional[str] = None,
    ) -> OperationResult:
        """持久化一次成功的文件重命名映射，供安全回退。"""
        if not mappings:
            return OperationResult(OperationStatus.INVALID, "没有可记录的重命名映射")
        rename_id = str(uuid.uuid4())
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO rename_history (rename_id, project_id, mappings_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (rename_id, project_id, json.dumps(mappings, ensure_ascii=False), datetime.now().isoformat()),
                )
                self._record_operation_in_transaction(
                    conn, "文件重命名", f"成功 {len(mappings)} 个", project_id,
                    object_type="rename", object_id=rename_id, recovery_id=rename_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=len(mappings), recovery_id=rename_id)
        except sqlite3.Error as exc:
            logger.error(f"保存重命名回退记录失败: {exc}")
            return OperationResult(OperationStatus.ERROR, str(exc))

    def get_rename_history(self, rename_id: str) -> OperationResult:
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

    def mark_rename_history_reverted(self, rename_id: str) -> OperationResult:
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "UPDATE rename_history SET reverted_at = ? WHERE rename_id = ? AND reverted_at IS NULL",
                    (datetime.now().isoformat(), rename_id),
                )
                if cursor.rowcount != 1:
                    return OperationResult(OperationStatus.CONFLICT, "重命名记录已回退或不存在")
                self._record_operation_in_transaction(
                    conn, "回退文件重命名", project_id=None,
                    object_type="rename", object_id=rename_id, recovery_id=rename_id,
                )
            return OperationResult(OperationStatus.SUCCESS, affected_count=1, recovery_id=rename_id)
        except sqlite3.Error as exc:
            return OperationResult(OperationStatus.ERROR, str(exc))

    def get_missing_file_asset_ids(self, project_id: str) -> List[str]:
        """返回当前项目中文件已丢失（路径为空或磁盘上不存在）的素材 asset_id 列表。

        实时扫描磁盘，是「文件存在性验证」的事实来源，供素材信息列表的
        “文件已丢失”标识与「一键清理丢失素材」功能共用，确保二者结论一致。

        Args:
            project_id: 项目 ID

        Returns:
            文件已丢失的素材 asset_id 列表（按项目素材默认排序）
        """
        missing = []
        for asset in self.iter_project_assets(project_id):
            if not asset.file_path or not Path(asset.file_path).exists():
                missing.append(asset.asset_id)
        return missing

    def asset_exists_by_path(self, project_id: str, file_path: str) -> bool:
        """按文件路径查重"""
        # asset.file_path 在导入时存的是 normalize_path() 规范化路径，
        # 查询键同样规范化，避免符号链接环境下查重失效导致重复入库。
        fp_key = normalize_path(file_path)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND file_path = ?",
                (project_id, fp_key)
            ).fetchone()
            return row[0] > 0

    def existing_asset_paths(self, project_id: str, file_paths: List[str]) -> set:
        """批量查重：返回 file_paths 中已存在于该项目的规范化路径集合。

        一次 IN 查询取回全部已存在路径，替代逐文件 SELECT 的 N+1 查询，
        适合大卡导入（数千文件）场景。

        Args:
            project_id: 项目 ID
            file_paths: 待检查的原始文件路径列表

        Returns:
            已存在的规范化路径集合（与入库存储形式一致）
        """
        if not file_paths:
            return set()
        keys = {normalize_path(fp) for fp in file_paths}
        existing = set()
        with self._connection() as conn:
            for chunk in _chunked(sorted(keys), 500):
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT file_path FROM media_assets "
                    f"WHERE project_id = ? AND file_path IN ({placeholders})",
                    (project_id, *chunk)
                ).fetchall()
                existing.update(r["file_path"] for r in rows)
        return existing

    def get_asset_log_id_by_path(self, file_path: str) -> Optional[str]:
        """按文件路径全局查询素材关联的 log_id（不限 project_id）。

        供 RAW 提取等场景使用：JPG 可能在任意项目，按路径反查其 log_id
        以便把提取出的 RAW 文件继承同一日志关联。

        注意：file_path 必须经过 normalize_path() 规范化，与入库时的
        存储形式保持一致，否则在符号链接 / 相对路径场景下会匹配失败。
        """
        fp_key = normalize_path(file_path)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT log_id FROM media_assets WHERE file_path = ?",
                (fp_key,)
            ).fetchone()
            return row["log_id"] if row else None

    def asset_exists_by_checksum(self, project_id: str, checksum_value: str) -> bool:
        """按校验和查重"""
        if not checksum_value:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM media_assets WHERE project_id = ? AND checksum_value = ?",
                (project_id, checksum_value)
            ).fetchone()
            return row[0] > 0

    def find_duplicate_assets(self) -> List[Dict]:
        """跨项目按校验和聚合，找出重复入库的素材。

        同一文件被导入多个项目、或同卡多次导入时会留下相同 checksum_value
        的多条记录。此方法按 (checksum_value, checksum_algorithm) 分组，
        仅返回出现次数 > 1 的组，供用户人工判定是否清理。

        Returns:
            [{"checksum_value", "algorithm", "count", "assets": [MediaAsset]}]
            按出现次数降序排列。
        """
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT checksum_value, checksum_algorithm, COUNT(*) AS c
                   FROM media_assets
                   WHERE checksum_value != ''
                   GROUP BY checksum_value, checksum_algorithm
                   HAVING COUNT(*) > 1
                   ORDER BY COUNT(*) DESC"""
            ).fetchall()

            results = []
            for r in rows:
                asset_rows = conn.execute(
                    """SELECT * FROM media_assets
                       WHERE checksum_value = ? AND checksum_algorithm = ?
                       ORDER BY project_id, date_imported""",
                    (r["checksum_value"], r["checksum_algorithm"])
                ).fetchall()
                results.append({
                    "checksum_value": r["checksum_value"],
                    "algorithm": r["checksum_algorithm"],
                    "count": r["c"],
                    "assets": [self._row_to_asset(a) for a in asset_rows],
                })
            return results

    def update_asset_path(self, asset_id: str, new_path: str, new_name: str = None) -> bool:
        """更新素材路径"""
        try:
            with self._transaction() as conn:
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
                self._refresh_asset_fts(conn, asset_id)
                logger.info(f"更新素材路径: {asset_id}")
                return True
        except Exception as e:
            logger.error(f"更新素材路径失败 {asset_id}: {e}")
            return False

    def update_asset_path_by_old_path(
        self, old_path: str, new_path: str, new_name: Optional[str] = None
    ) -> bool:
        """按旧文件路径查找 asset 并更新为新路径/文件名（用于重命名后同步 DB）。

        old_path / new_path 均会经过 normalize_path() 规范化，与入库时的
        存储形式保持一致，避免符号链接 / 相对路径场景下匹配失败。

        Returns:
            True 表示找到并更新；False 表示未找到匹配 asset（如该文件未入库）
        """
        old_key = normalize_path(old_path)
        new_key = normalize_path(new_path)
        try:
            with self._transaction() as conn:
                row = conn.execute(
                    "SELECT asset_id FROM media_assets WHERE file_path = ?",
                    (old_key,)
                ).fetchone()
                if not row:
                    return False
                if new_name:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ?, file_name = ? WHERE asset_id = ?",
                        (new_key, new_name, row["asset_id"])
                    )
                else:
                    conn.execute(
                        "UPDATE media_assets SET file_path = ? WHERE asset_id = ?",
                        (new_key, row["asset_id"])
                    )
                self._refresh_asset_fts(conn, row["asset_id"])
                logger.info(f"按旧路径同步重命名: {old_key} -> {new_key}")
                return True
        except Exception as e:
            logger.error(f"按旧路径同步重命名失败 {old_key}: {e}")
            return False

    def _asset_filter_clause(
        self,
        project_id: Optional[str] = None,
        scene: Optional[str] = None,
        shot: Optional[str] = None,
        file_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        keyword: Optional[str] = None,
        log_id: Optional[str] = None,
        rating: Optional[int] = None,
        tag: Optional[str] = None,
    ) -> tuple:
        """构造素材筛选子句 (WHERE_SQL, params)，供 search_assets / count_assets 复用。"""
        query = " WHERE 1=1"
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
        fts_keyword = self._fts_query(keyword or "")
        if keyword and not (self._fts_available and fts_keyword):
            query += " AND (file_name LIKE ? OR scene LIKE ? OR shot LIKE ? OR notes LIKE ?)"
            params.extend([f"%{keyword}%"] * 4)
        if log_id:
            query += " AND log_id = ?"
            params.append(log_id)
        if rating is not None and rating > 0:
            query += " AND rating >= ?"
            params.append(rating)
        if tag:
            # 基于 asset_tags 关联表的子串匹配：行为与旧 tags LIKE 一致，
            # 但按规范化后的标签 token 匹配，避免逗号/空白导致误匹配。
            query += (" AND EXISTS (SELECT 1 FROM asset_tags WHERE "
                      "asset_id = media_assets.asset_id AND tag LIKE ? COLLATE NOCASE)")
            params.append(f"%{tag}%")

        return query, params

    def _search_sql(self, **filters):
        """构造带 FTS 条件的素材查询 SQL 与参数。"""
        keyword = filters.get("keyword") or ""
        fts_keyword = self._fts_query(keyword)
        where_sql, params = self._asset_filter_clause(**filters)
        if keyword and self._fts_available and fts_keyword:
            where_sql += (
                " AND asset_id IN (SELECT asset_id FROM media_assets_fts "
                "WHERE media_assets_fts MATCH ?)"
            )
            params.append(fts_keyword)
        return where_sql, params

    def iter_search_assets(
        self,
        project_id: Optional[str] = None,
        scene: Optional[str] = None,
        shot: Optional[str] = None,
        file_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        keyword: Optional[str] = None,
        log_id: Optional[str] = None,
        rating: Optional[int] = None,
        tag: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        batch_size: int = 500,
        cursor: Optional[tuple[str, str]] = None,
    ):
        """按游标批量读取素材，避免导出/批处理一次性加载全部记录。"""
        filters = dict(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword,
            log_id=log_id, rating=rating, tag=tag,
        )
        where_sql, params = self._search_sql(**filters)
        if cursor:
            where_sql += " AND (date_imported < ? OR (date_imported = ? AND asset_id < ?))"
            params.extend([cursor[0], cursor[0], cursor[1]])
        query = f"SELECT * FROM media_assets{where_sql} ORDER BY date_imported DESC, asset_id DESC"
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None and offset > 0:
                query += " OFFSET ?"
                params.append(offset)
        with self._connection() as conn:
            cursor = conn.execute(query, params)
            while True:
                rows = cursor.fetchmany(max(1, batch_size))
                if not rows:
                    break
                for row in rows:
                    yield self._row_to_asset(row)

    def get_search_asset_page(
        self, *, page_size: int = 500, cursor: Optional[tuple[str, str]] = None, **filters,
    ) -> tuple[List[MediaAsset], Optional[tuple[str, str]]]:
        """返回一个 keyset 页面和下一页游标，避免深页 OFFSET 扫描。"""
        page_size = max(1, page_size)
        rows = list(self.iter_search_assets(
            **filters, cursor=cursor, limit=page_size + 1, batch_size=page_size + 1,
        ))
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = None
        if has_next and rows:
            last = rows[-1]
            next_cursor = (last.date_imported.isoformat(), last.asset_id)
        return rows, next_cursor

    def search_assets(
        self,
        project_id: Optional[str] = None,
        scene: Optional[str] = None,
        shot: Optional[str] = None,
        file_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        keyword: Optional[str] = None,
        log_id: Optional[str] = None,
        rating: Optional[int] = None,
        tag: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[MediaAsset]:
        """搜索素材（支持分页）。

        Args:
            rating: 按 rating 过滤（>=rating）。None 表示不过滤。
            tag: 按自定义标签过滤（基于 asset_tags 关联表，子串匹配）。
            limit: 返回结果上限。None 表示不限制。
            offset: 分页偏移。None 表示从第一条开始。
        """
        return list(self.iter_search_assets(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword,
            log_id=log_id, rating=rating, tag=tag, limit=limit, offset=offset,
        ))

    def count_assets(
        self,
        project_id: Optional[str] = None,
        scene: Optional[str] = None,
        shot: Optional[str] = None,
        file_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        keyword: Optional[str] = None,
        log_id: Optional[str] = None,
        rating: Optional[int] = None,
        tag: Optional[str] = None,
    ) -> int:
        """统计符合条件的素材总数（与 search_assets 同条件）。"""
        filters = dict(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword,
            log_id=log_id, rating=rating, tag=tag,
        )
        where_sql, params = self._search_sql(**filters)
        with self._connection() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM media_assets{where_sql}", params).fetchone()
            return row[0]

    def get_all_tags(self) -> List[str]:
        """返回全部素材标签（去重、按使用次数降序），供检索自动补全。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT tag, COUNT(*) AS cnt FROM asset_tags "
                "GROUP BY tag ORDER BY cnt DESC, tag ASC"
            ).fetchall()
            return [r["tag"] for r in rows]

    def get_assets_by_log_id(self, log_id: str) -> List[MediaAsset]:
        """按拍摄日志ID获取关联的素材"""
        return self.search_assets(log_id=log_id)

    def update_media_asset_log_id(self, asset_id: str, log_id: Optional[str]) -> bool:
        """更新素材关联的拍摄日志。

        - log_id=None 表示解除关联，同时清空 scene/shot，避免遗留"无主"字段
          （与 delete_shooting_log 保持一致的数据一致性策略）。
        - log_id 非 None 时只更新 log_id，scene/shot 由调用方另行处理
          （如 create_log_with_assets 的 sync_scene_shot 路径）。
        """
        try:
            with self._transaction() as conn:
                if log_id is None:
                    conn.execute(
                        "UPDATE media_assets SET log_id = NULL, scene = '', shot = '' WHERE asset_id = ?",
                        (asset_id,)
                    )
                else:
                    conn.execute(
                        "UPDATE media_assets SET log_id = ? WHERE asset_id = ?",
                        (log_id, asset_id)
                    )
                logger.info(f"更新素材日志关联: {asset_id} -> {log_id or 'None'}")
                return True
        except Exception as e:
            logger.error(f"更新素材日志关联失败 {asset_id}: {e}")
            return False

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
        project_id: Optional[str] = None,
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
        project_id: Optional[str] = None,
        event: str = "",
        status: str = "",
        object_type: str = "",
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List[dict]:
        """按项目、事件、结果、对象与时间筛选审计日志（按时间倒序）。"""
        return self.logs.list_recent(
            limit, project_id, event, status, object_type, date_from, date_to,
        )

    # ===== 统一任务历史 =====

    def create_task_history(
        self, task_name: str, project_id: Optional[str] = None, *,
        state: str = "queued", parameters: Optional[dict] = None,
        recovery_info: Optional[dict] = None,
    ) -> str:
        """创建可审计的后台任务记录，返回稳定任务 ID。"""
        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO task_history "
                "(task_id, task_name, project_id, state, parameters_json, recovery_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id, task_name, project_id, state,
                    json.dumps(parameters or {}, ensure_ascii=False, default=str),
                    json.dumps(recovery_info or {}, ensure_ascii=False, default=str), now,
                ),
            )
        return task_id

    def update_task_history(
        self, task_id: str, state: str, *, output: Optional[dict] = None,
        error_summary: str = "", recovery_info: Optional[dict] = None,
    ) -> bool:
        """更新任务状态与可恢复上下文；终态自动写入结束时间。"""
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
                    (
                        state, json.dumps(output or {}, ensure_ascii=False, default=str), error_summary,
                        json.dumps(merged_recovery, ensure_ascii=False, default=str), datetime.now().isoformat(),
                        int(state in terminal), datetime.now().isoformat(), task_id,
                    ),
                )
            return True
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"更新任务历史失败 {task_id}: {exc}")
            return False

    def get_task_history(self, project_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        """读取最近任务，供健康页、审计与恢复入口使用。"""
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

    # ===== 项目健康容量快照 =====

    def record_storage_health_snapshot(
        self, project_id: Optional[str], target_path: str, total_bytes: int, free_bytes: int,
    ) -> bool:
        """记录可达备份目标的容量，用于健康页展示近期趋势。"""
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

    def get_storage_health_snapshots(self, project_id: Optional[str], limit: int = 60) -> List[dict]:
        """读取项目近期的目标容量快照，按采集时间正序返回。"""
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
            item["captured_at"] = self._parse_datetime(item.get("captured_at"))
        return result

    # ===== 备份作业持久化 =====

    def save_backup_job(self, job, project_id: Optional[str] = None) -> bool:
        """持久化备份作业（含 targets 列表的 JSON 序列化）。

        Args:
            job: BackupJob 实例
            project_id: 关联项目 ID（可空，表示未关联项目的备份）
        Returns:
            是否成功
        """
        import json
        if project_id:
            with self._connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone()
            if exists is None:
                # 兼容未关联项目的历史调用，同时保持外键约束有效。
                logger.warning(f"备份作业关联项目不存在，改为未关联: {project_id}")
                project_id = None
        targets_json = json.dumps([
            {
                "path": t.path,
                "name": t.name,
                "status": t.status.value,
                "total_files": t.total_files,
                "completed_files": t.completed_files,
                "total_bytes": t.total_bytes,
                "copied_bytes": t.copied_bytes,
                "verified": t.verified,
                "error_message": t.error_message,
                "failed_files": list(t.failed_files),
                "pending_files": list(t.pending_files),
            } for t in job.targets
        ], ensure_ascii=False)
        failed_files_json = json.dumps([
            {"path": t.path, "files": list(t.failed_files)}
            for t in job.targets if t.failed_files
        ], ensure_ascii=False)
        snapshots_json = json.dumps(
            list(getattr(job, "_files_cache", []) or []),
            ensure_ascii=False,
            default=str,
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
                     job.completed_at.isoformat() if job.completed_at else None)
                )
                logger.info(f"持久化备份作业: {job.job_id} (project={project_id})")
                return True
        except Exception as e:
            logger.error(f"持久化备份作业失败 {job.job_id}: {e}")
            return False

    def get_backup_job(self, job_id: str) -> Optional[dict]:
        """获取单个备份作业的原始 dict；不存在返回 None。"""
        for raw in self.get_backup_jobs():
            if raw["job_id"] == job_id:
                return raw
        return None

    def get_backup_jobs(self, project_id: Optional[str] = None) -> List[dict]:
        """获取备份作业列表（原始 dict 形式，由调用方解析）。

        Args:
            project_id: 若提供则只返回该项目的备份；None 返回全部
        """
        import json
        with self._connection() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM backup_jobs WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM backup_jobs ORDER BY created_at DESC"
                ).fetchall()

            results = []
            for r in rows:
                try:
                    targets = json.loads(r["targets_json"]) if r["targets_json"] else []
                except (json.JSONDecodeError, TypeError):
                    targets = []
                for t in targets:
                    # 兼容旧数据：未记录 failed_files 时视为空列表
                    t.setdefault("failed_files", [])
                    t.setdefault("pending_files", [])
                completed_at = None
                if r["completed_at"]:
                    try:
                        completed_at = datetime.fromisoformat(r["completed_at"])
                    except ValueError:
                        pass
                try:
                    failed = json.loads(r["failed_files_json"]) if r["failed_files_json"] else []
                except (json.JSONDecodeError, TypeError):
                    failed = []
                try:
                    snapshots = json.loads(r["snapshot_json"]) if r["snapshot_json"] else []
                except (json.JSONDecodeError, TypeError):
                    snapshots = []
                failed_by_target = {f["path"]: f.get("files", []) for f in failed}
                results.append({
                    "job_id": r["job_id"],
                    "project_id": r["project_id"],
                    "source_path": r["source_path"],
                    "algorithm": r["algorithm"],
                    "total_files": r["total_files"],
                    "total_bytes": r["total_bytes"],
                    "status": r["status"],
                    "targets": targets,
                    "created_at": self._parse_datetime(r["created_at"]),
                    "completed_at": completed_at,
                    "failed_files_by_target": failed_by_target,
                    "file_snapshots": snapshots,
                })
            return results

    def add_backup_location_to_assets(
        self,
        file_paths: List[str],
        target_path: str,
        project_id: Optional[str] = None
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
        if not file_paths or not target_path:
            return 0

        try:
            keys = list(dict.fromkeys(normalize_path(fp) for fp in file_paths))
            with self._transaction() as conn:
                # 批量取回已存在 asset（首个匹配优先，语义与原实现一致）
                rows_by_path = {}
                for chunk in _chunked(keys, 500):
                    placeholders = ",".join("?" * len(chunk))
                    sql = (
                        "SELECT asset_id, file_path, backup_locations FROM media_assets "
                        f"WHERE file_path IN ({placeholders})"
                    )
                    if project_id:
                        sql += " AND project_id = ?"
                        params = (*chunk, project_id)
                    else:
                        params = chunk
                    for r in conn.execute(sql, params).fetchall():
                        rows_by_path.setdefault(r["file_path"], r)

                updated = 0
                for fp in keys:
                    row = rows_by_path.get(fp)
                    if not row:
                        continue
                    existing = row["backup_locations"] or ""
                    locations = [l for l in existing.split("|") if l] if existing else []
                    if target_path in locations:
                        continue  # 幂等
                    locations.append(target_path)
                    new_val = "|".join(locations)
                    conn.execute(
                        "UPDATE media_assets SET backup_locations = ? WHERE asset_id = ?",
                        (new_val, row["asset_id"])
                    )
                    updated += 1
                if updated:
                    logger.info(f"批量回写 backup_locations: {updated} 个 asset += {target_path}")
                return updated
        except Exception as e:
            logger.error(f"批量回写 backup_locations 失败: {e}")
            return 0

    # ===== 辅助方法 =====

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> MediaAsset:
        """兼容仍在门面层的搜索和恢复流程，复用素材仓储的行映射。"""
        return AssetRepository._row_to_asset(row)
