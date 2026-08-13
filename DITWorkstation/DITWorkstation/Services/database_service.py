"""拍摄日志与项目管理服务 - SQLite持久化"""
import sqlite3
import threading
import uuid
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from DITWorkstation.App import config
from DITWorkstation.Models import Project, ProjectTemplate, ShootingLog, MediaAsset, Workspace
from DITWorkstation.Utils import logger, normalize_path


def _chunked(seq, size):
    """把序列切分为固定大小的块（SQLite 变量上限 999，保守取 500）。"""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class DatabaseService:
    """数据库服务 - SQLite持久化"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程级连接池：每个线程复用同一连接，避免高频操作反复建连 + 重复设置 PRAGMA
        self._conns: dict = {}
        self._conns_lock = threading.Lock()
        self._had_existing_db = self.db_path.exists() and self.db_path.stat().st_size > 0
        self._init_db()

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
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );

                CREATE INDEX IF NOT EXISTS idx_backup_jobs_project ON backup_jobs(project_id);

                CREATE TABLE IF NOT EXISTS operation_logs (
                    log_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    event TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_operation_logs_project ON operation_logs(project_id);
                CREATE INDEX IF NOT EXISTS idx_operation_logs_created ON operation_logs(created_at);

                CREATE TABLE IF NOT EXISTS project_templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    base_path TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
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
    _DB_VERSION = 2

    def _migrate_db(self):
        """按 PRAGMA user_version 分版本迁移数据库。

        版本化迁移保证：
        - 旧库升级路径明确（v0 → v1 → v2 …），新库直接建全量 schema；
        - 每个迁移幂等，重复执行不破坏数据。
        """
        conn = self._create_conn()
        try:
            version = self._get_user_version(conn)
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

            # 向后兼容：旧项目（workspace_id 为 NULL）自动归入"默认工作区"
            self._migrate_legacy_projects_to_default_workspace(conn)
            self._recover_interrupted_backup_jobs(conn)
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
        """创建工作区

        Args:
            name: 工作区名称（如「2026 春季广告片」）
            path: 工作区对应的物理目录路径（作为该工作区下项目的默认工作目录根）
            description: 工作区描述
        """
        workspace = Workspace(
            workspace_id=str(uuid.uuid4())[:8],
            name=name,
            path=path,
            description=description
        )
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO workspaces (workspace_id, name, path, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (workspace.workspace_id, workspace.name, workspace.path, workspace.description,
                 workspace.created_at.isoformat(), workspace.updated_at.isoformat())
            )
            logger.info(f"创建工作区: {workspace.workspace_id} - {workspace.name} (path={workspace.path})")
        return workspace

    def get_workspaces(self) -> List[Workspace]:
        """获取所有工作区，按创建时间倒序"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workspaces ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_workspace(r) for r in rows]

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """获取单个工作区"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            return self._row_to_workspace(row) if row else None

    def update_workspace(self, workspace_id: str, **kwargs) -> bool:
        """更新工作区（支持 name / path / description）"""
        if not kwargs:
            return False
        fields = []
        params = []
        for key, value in kwargs.items():
            if key in ['name', 'path', 'description']:
                fields.append(f"{key} = ?")
                params.append(value)
        if not fields:
            return False
        fields.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(workspace_id)

        try:
            with self._transaction() as conn:
                conn.execute(f"UPDATE workspaces SET {', '.join(fields)} WHERE workspace_id = ?", params)
                logger.info(f"更新工作区: {workspace_id}")
                return True
        except Exception as e:
            logger.error(f"更新工作区失败 {workspace_id}: {e}")
            return False

    def delete_workspace(self, workspace_id: str, reassign_to: Optional[str] = None) -> bool:
        """删除工作区。

        Args:
            workspace_id: 待删除的工作区 ID
            reassign_to: 删除前把该工作区下的项目迁移到此 ID；为 None 时项目 workspace_id 置 NULL
                         （会立即触发默认工作区归集，确保不留孤儿项目）

        Returns:
            是否成功（默认工作区 'default' 不可删除，防止误操作）
        """
        if workspace_id == "default":
            logger.warning("拒绝删除默认工作区")
            return False

        try:
            with self._transaction() as conn:
                default_exists = conn.execute(
                    "SELECT 1 FROM workspaces WHERE workspace_id = 'default'"
                ).fetchone()
                if default_exists is None:
                    now = datetime.now().isoformat()
                    conn.execute(
                        "INSERT INTO workspaces "
                        "(workspace_id, name, path, description, created_at, updated_at) "
                        "VALUES ('default', ?, '', ?, ?, ?)",
                        ("默认工作区", "默认工作区", now, now),
                    )
                if reassign_to:
                    conn.execute(
                        "UPDATE projects SET workspace_id = ? WHERE workspace_id = ?",
                        (reassign_to, workspace_id)
                    )
                else:
                    # 置 NULL，迁移逻辑会在下次启动时归入默认工作区；
                    # 此处也立即归集以保持运行期一致性
                    conn.execute(
                        "UPDATE projects SET workspace_id = 'default' WHERE workspace_id = ?",
                        (workspace_id,)
                    )
                conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,))
                logger.info(f"删除工作区: {workspace_id}（项目已归入 default）")
                return True
        except Exception as e:
            logger.error(f"删除工作区失败 {workspace_id}: {e}")
            return False

    def _row_to_workspace(self, row: sqlite3.Row) -> Workspace:
        return Workspace(
            workspace_id=row["workspace_id"],
            name=row["name"],
            path=row["path"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"])
        )

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
        # 兜底：未指定 workspace_id，或显式指定 default 但旧库尚未建默认工作区时，
        # 都确保默认工作区存在。
        if workspace_id is None or workspace_id == "default":
            ws = self.get_workspace("default")
            if ws is None:
                # 数据库刚初始化且无任何工作区时，先建默认工作区
                now = datetime.now().isoformat()
                with self._transaction() as conn:
                    conn.execute(
                        "INSERT INTO workspaces "
                        "(workspace_id, name, path, description, created_at, updated_at) "
                        "VALUES ('default', ?, '', ?, ?, ?)",
                        ("默认工作区", "默认工作区", now, now),
                    )
                ws = self.get_workspace("default")
            workspace_id = ws.workspace_id

        project = Project(
            project_id=str(uuid.uuid4())[:8],
            name=name,
            description=description,
            base_path=base_path,
            workspace_id=workspace_id
        )
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO projects (project_id, name, description, base_path, workspace_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project.project_id, project.name, project.description, project.base_path,
                 project.workspace_id, project.created_at.isoformat(), project.updated_at.isoformat())
            )
            logger.info(f"创建项目: {project.project_id} - {project.name} (workspace={workspace_id})")
        return project

    def get_projects(self, workspace_id: Optional[str] = None) -> List[Project]:
        """获取项目列表。

        Args:
            workspace_id: 指定则只返回该工作区下的项目；None 返回所有项目
        """
        with self._connection() as conn:
            if workspace_id:
                rows = conn.execute(
                    "SELECT * FROM projects WHERE workspace_id = ? ORDER BY created_at DESC",
                    (workspace_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
            return [self._row_to_project(r) for r in rows]

    def get_project(self, project_id: str) -> Optional[Project]:
        """获取单个项目"""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            return self._row_to_project(row) if row else None

    def update_project(self, project_id: str, **kwargs) -> bool:
        """更新项目"""
        if not kwargs:
            return False

        fields = []
        params = []
        for key, value in kwargs.items():
            if key in ['name', 'description', 'base_path', 'workspace_id']:
                fields.append(f"{key} = ?")
                params.append(value)
        fields.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(project_id)

        try:
            with self._transaction() as conn:
                conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE project_id = ?", params)
                logger.info(f"更新项目: {project_id}")
                return True
        except Exception as e:
            logger.error(f"更新项目失败 {project_id}: {e}")
            return False

    def delete_project(self, project_id: str):
        """删除项目（事务级联清理 media_assets + shooting_logs + projects）

        注意：backup_jobs 表无 project_id 外键，保留备份历史记录不级联删除。
        """
        with self._transaction() as conn:
            # 备份历史需要保留，但启用外键后必须先解除项目关联。
            conn.execute(
                "UPDATE backup_jobs SET project_id = NULL WHERE project_id = ?",
                (project_id,)
            )
            conn.execute(
                "DELETE FROM asset_tags WHERE asset_id IN "
                "(SELECT asset_id FROM media_assets WHERE project_id = ?)",
                (project_id,)
            )
            conn.execute("DELETE FROM media_assets WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM shooting_logs WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            logger.info(f"删除项目: {project_id}")

    # ===== 项目模板 =====

    def create_project_template(
        self,
        name: str,
        description: str = "",
        base_path: str = "",
        notes: str = "",
    ) -> ProjectTemplate:
        """创建项目模板。"""
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
                    template.template_id, template.name, template.description,
                    template.base_path, template.notes,
                    template.created_at.isoformat(), template.updated_at.isoformat(),
                ),
            )
            logger.info(f"创建项目模板: {template.template_id} - {template.name}")
        return template

    def get_project_templates(self) -> List[ProjectTemplate]:
        """获取所有项目模板，按创建时间倒序。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM project_templates ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_template(r) for r in rows]

    def get_project_template(self, template_id: str) -> Optional[ProjectTemplate]:
        """获取单个项目模板。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM project_templates WHERE template_id = ?", (template_id,)
            ).fetchone()
            return self._row_to_template(row) if row else None

    def update_project_template(self, template_id: str, **kwargs) -> bool:
        """更新项目模板（支持 name / description / base_path / notes）。"""
        if not kwargs:
            return False
        fields = []
        params = []
        for key, value in kwargs.items():
            if key in ['name', 'description', 'base_path', 'notes']:
                fields.append(f"{key} = ?")
                params.append(value)
        if not fields:
            return False
        fields.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(template_id)
        try:
            with self._transaction() as conn:
                conn.execute(
                    f"UPDATE project_templates SET {', '.join(fields)} WHERE template_id = ?",
                    params,
                )
                logger.info(f"更新项目模板: {template_id}")
                return True
        except Exception as e:
            logger.error(f"更新项目模板失败 {template_id}: {e}")
            return False

    def delete_project_template(self, template_id: str) -> bool:
        """删除项目模板。"""
        try:
            with self._transaction() as conn:
                conn.execute(
                    "DELETE FROM project_templates WHERE template_id = ?", (template_id,)
                )
                logger.info(f"删除项目模板: {template_id}")
                return True
        except Exception as e:
            logger.error(f"删除项目模板失败 {template_id}: {e}")
            return False

    def _row_to_template(self, row: sqlite3.Row) -> ProjectTemplate:
        return ProjectTemplate(
            template_id=row["template_id"],
            name=row["name"],
            description=row["description"],
            base_path=row["base_path"],
            notes=row["notes"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ===== 拍摄日志 =====

    def create_shooting_log(self, log: ShootingLog) -> ShootingLog:
        """创建拍摄日志"""
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO shooting_logs
                   (log_id, project_id, scene, shot, take, description, camera, lens, iso, aperture, shutter_speed, notes, file_paths, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (log.log_id, log.project_id, log.scene, log.shot, log.take,
                 log.description, log.camera, log.lens, log.iso, log.aperture,
                 log.shutter_speed, log.notes, "|".join(log.file_paths),
                 log.created_at.isoformat())
            )
            logger.info(f"创建拍摄日志: {log.log_id} - {log.scene}/{log.shot}/{log.take}")
        return log

    def get_shooting_logs(self, project_id: str) -> List[ShootingLog]:
        """获取项目的拍摄日志"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM shooting_logs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,)
            ).fetchall()
            return [self._row_to_log(r) for r in rows]

    def get_shooting_log(self, log_id: str) -> Optional[ShootingLog]:
        """获取单个拍摄日志"""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM shooting_logs WHERE log_id = ?", (log_id,)).fetchone()
            return self._row_to_log(row) if row else None

    def update_shooting_log(self, log: ShootingLog):
        """更新拍摄日志"""
        with self._transaction() as conn:
            conn.execute(
                """UPDATE shooting_logs SET scene=?, shot=?, take=?, description=?, camera=?,
                   lens=?, iso=?, aperture=?, shutter_speed=?, notes=?, file_paths=?
                   WHERE log_id=?""",
                (log.scene, log.shot, log.take, log.description, log.camera,
                 log.lens, log.iso, log.aperture, log.shutter_speed, log.notes,
                 "|".join(log.file_paths), log.log_id)
            )
            logger.info(f"更新拍摄日志: {log.log_id}")

    def delete_shooting_log(self, log_id: str):
        """删除拍摄日志（级联清除关联素材的 log_id 与 scene/shot）

        背景：create_log_with_assets(sync_scene_shot=True) 会把 log.scene/shot
        冗余写入 media_assets。如果删除日志时只清 log_id，会留下"无主"的
        scene/shot，导致后续检索/报告把残留字段当作有效数据。
        故删除日志时一并清空 scene/shot，保持数据一致性。
        """
        try:
            with self._transaction() as conn:
                conn.execute(
                    "UPDATE media_assets SET log_id = NULL, scene = '', shot = '' WHERE log_id = ?",
                    (log_id,)
                )
                conn.execute("DELETE FROM shooting_logs WHERE log_id = ?", (log_id,))
                logger.info(f"删除拍摄日志: {log_id}（已级联清空关联素材的 scene/shot）")
        except Exception as e:
            logger.error(f"删除拍摄日志失败 {log_id}: {e}")
            raise

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
        try:
            with self._transaction() as conn:
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
                logger.info(f"创建日志并关联素材: {log.log_id} - {len(asset_ids)} 个素材")
        except Exception as e:
            logger.error(f"create_log_with_assets 失败: {e}")
            raise
        return log

    # ===== 素材资产 =====

    def _sync_asset_tags(self, conn, asset_id: str, tags: str):
        """把 tags 逗号字符串同步到 asset_tags 关联表（须在事务内调用）。"""
        conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
        for tag in self._split_tags(tags):
            conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                (asset_id, tag),
            )

    def add_media_asset(self, asset: MediaAsset) -> MediaAsset:
        """添加素材资产"""
        with self._transaction() as conn:
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
                 asset.checksum_algorithm, asset.checksum_value,
                 asset.scene, asset.shot, asset.take,
                 asset.date_imported.isoformat(),
                 asset.date_taken.isoformat() if asset.date_taken else None,
                 asset.camera_make, asset.camera_model,
                 "|".join(asset.backup_locations), asset.log_id,
                 1 if asset.is_working_copy else 0,
                 asset.original_path,
                 asset.width, asset.height, asset.duration_seconds,
                 asset.lens_model, asset.focal_length, asset.video_metadata,
                 asset.rating, asset.tags, asset.notes)
            )
            self._sync_asset_tags(conn, asset.asset_id, asset.tags)
            logger.info(f"添加素材资产: {asset.asset_id} - {asset.file_name}")
        return asset

    def add_media_assets_batch(self, assets: List[MediaAsset]) -> int:
        """批量添加素材资产"""
        if not assets:
            return 0

        try:
            with self._transaction() as conn:
                for asset in assets:
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
                         asset.checksum_algorithm, asset.checksum_value,
                         asset.scene, asset.shot, asset.take,
                         asset.date_imported.isoformat(),
                         asset.date_taken.isoformat() if asset.date_taken else None,
                         asset.camera_make, asset.camera_model,
                         "|".join(asset.backup_locations), asset.log_id,
                         1 if asset.is_working_copy else 0,
                         asset.original_path,
                         asset.width, asset.height, asset.duration_seconds,
                         asset.lens_model, asset.focal_length, asset.video_metadata,
                         asset.rating, asset.tags, asset.notes)
                    )
                    self._sync_asset_tags(conn, asset.asset_id, asset.tags)
                logger.info(f"批量添加素材资产: {len(assets)} 个")
                return len(assets)
        except Exception as e:
            logger.error(f"批量添加素材资产失败: {e}")
            return 0

    def get_media_assets(self, project_id: str) -> List[MediaAsset]:
        """获取项目素材"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM media_assets WHERE project_id = ? ORDER BY date_imported DESC",
                (project_id,)
            ).fetchall()
            return [self._row_to_asset(r) for r in rows]

    def get_media_asset(self, asset_id: str) -> Optional[MediaAsset]:
        """获取单个素材资产"""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM media_assets WHERE asset_id = ?", (asset_id,)).fetchone()
            return self._row_to_asset(row) if row else None

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
                       'lens_model', 'focal_length', 'video_metadata',
                       'rating', 'tags', 'notes']:
                if key == 'backup_locations' and isinstance(value, list):
                    value = "|".join(value)
                if key == 'is_working_copy' and isinstance(value, bool):
                    value = 1 if value else 0
                if key == 'date_taken' and hasattr(value, 'isoformat'):
                    value = value.isoformat()
                fields.append(f"{key} = ?")
                params.append(value)
        tags_updated = 'tags' in kwargs
        params.append(asset_id)

        try:
            with self._transaction() as conn:
                conn.execute(f"UPDATE media_assets SET {', '.join(fields)} WHERE asset_id = ?", params)
                if tags_updated:
                    self._sync_asset_tags(conn, asset_id, kwargs.get('tags') or '')
                logger.info(f"更新素材资产: {asset_id}")
                return True
        except Exception as e:
            logger.error(f"更新素材资产失败 {asset_id}: {e}")
            return False

    def delete_media_asset(self, asset_id: str) -> bool:
        """删除素材资产"""
        try:
            with self._transaction() as conn:
                conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
                conn.execute("DELETE FROM media_assets WHERE asset_id = ?", (asset_id,))
                logger.info(f"删除素材资产: {asset_id}")
                return True
        except Exception as e:
            logger.error(f"删除素材资产失败 {asset_id}: {e}")
            return False

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
        if keyword:
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
        where_sql, params = self._asset_filter_clause(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword,
            log_id=log_id, rating=rating, tag=tag,
        )
        with self._connection() as conn:
            query = f"SELECT * FROM media_assets{where_sql} ORDER BY date_imported DESC"
            if limit is not None and limit > 0:
                query += " LIMIT ?"
                params.append(limit)
                if offset is not None and offset > 0:
                    query += " OFFSET ?"
                    params.append(offset)
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_asset(r) for r in rows]

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
        where_sql, params = self._asset_filter_clause(
            project_id=project_id, scene=scene, shot=shot, file_type=file_type,
            date_from=date_from, date_to=date_to, keyword=keyword,
            log_id=log_id, rating=rating, tag=tag,
        )
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

            return {
                "asset_count": asset_count,
                "log_count": log_count,
                "total_size": total_size,
                "backup_job_count": backup_job_count,
                "backed_up_count": backed_up_count,
            }

    # ===== 操作审计日志 =====

    def record_operation(
        self,
        event: str,
        detail: str = "",
        project_id: Optional[str] = None
    ) -> bool:
        """记录一条操作审计日志（导入/备份/重命名/评级等关键操作）。

        Args:
            event: 事件类型（如 "导入素材"、"数据备份"）
            detail: 事件详情（如成功/失败统计）
            project_id: 关联项目（可空）

        Returns:
            是否写入成功
        """
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO operation_logs (log_id, project_id, event, detail, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4())[:8], project_id, event, detail,
                     datetime.now().isoformat())
                )
                return True
        except Exception as e:
            logger.error(f"记录操作日志失败: {e}")
            return False

    def get_recent_operations(
        self,
        limit: int = 10,
        project_id: Optional[str] = None
    ) -> List[dict]:
        """获取最近的操作审计日志（按时间倒序）。"""
        with self._connection() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM operation_logs WHERE project_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (project_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            results = []
            for r in rows:
                try:
                    created_at = datetime.fromisoformat(r["created_at"])
                except ValueError:
                    created_at = None
                results.append({
                    "log_id": r["log_id"],
                    "project_id": r["project_id"],
                    "event": r["event"],
                    "detail": r["detail"],
                    "created_at": created_at,
                })
            return results

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

        try:
            with self._transaction() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO backup_jobs
                       (job_id, project_id, source_path, algorithm, total_files, total_bytes,
                        status, targets_json, failed_files_json, created_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job.job_id, project_id, job.source_path, job.algorithm.value,
                     job.total_files, job.total_bytes, job.status.value, targets_json,
                     failed_files_json, job.created_at.isoformat(),
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
                    "created_at": datetime.fromisoformat(r["created_at"]),
                    "completed_at": completed_at,
                    "failed_files_by_target": failed_by_target,
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

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        # workspace_id 是迁移新增列，旧数据库可能无此列；用 keys() 兜底
        workspace_id = row["workspace_id"] if "workspace_id" in row.keys() else None
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"],
            base_path=row["base_path"],
            workspace_id=workspace_id,
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
            rating=row["rating"] if "rating" in row.keys() else 0,
            tags=row["tags"] if "tags" in row.keys() else "",
            notes=row["notes"] if "notes" in row.keys() else "",
        )
