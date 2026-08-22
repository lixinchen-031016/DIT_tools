"""应用配置管理"""
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _resolve_data_dir() -> Path:
    """
    解析数据目录路径（跨平台 + 冻结态兼容）

    开发模式：代码库根目录下的 data 文件夹
    冻结模式（PyInstaller 打包后）：用户主目录下的应用数据目录
        macOS: ~/Library/Application Support/DITWorkstation
        Windows: %APPDATA%/DITWorkstation
        Linux: ~/.local/share/DITWorkstation
    """
    if getattr(sys, "frozen", False):
        # 打包后：使用各平台标准的用户数据目录（可写）
        app_name = "DITWorkstation"
        system = platform.system()
        if system == "Windows":
            base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            return Path(base) / app_name
        elif system == "Darwin":
            return Path.home() / "Library" / "Application Support" / app_name
        else:
            return Path.home() / ".local" / "share" / app_name
    # 开发模式：代码库根目录下的 data 文件夹
    return Path(__file__).resolve().parent.parent.parent.parent / "data"


def _resolve_report_dir() -> Path:
    """解析报告输出目录（跨平台 + 冻结态兼容）"""
    if getattr(sys, "frozen", False):
        # 打包后：输出到用户文档目录
        return Path.home() / "Documents" / "DIT_Reports"
    return Path.home() / "Documents" / "DIT_Reports"


def _resolve_log_dir() -> Path:
    """解析日志输出目录（开发态与冻结态一致，均在用户主目录下）"""
    return Path.home() / ".dit_workstation" / "logs"


def _resolve_personal_workspace_dir() -> Path:
    """解析个人模式默认工作区；优先使用显式环境变量覆盖。"""
    override = os.environ.get("DIT_PERSONAL_WS_PATH", "").strip()
    if override:
        return Path(override)
    # 与数据库使用同一应用数据根，避免打包后写入只读的 app bundle/安装目录。
    return _resolve_data_dir() / "DIT_Projects"


@dataclass
class AppConfig:
    """应用全局配置"""
    # 数据库路径（开发态：代码库下 data/；冻结态：用户数据目录）
    db_dir: Path = field(default_factory=_resolve_data_dir)
    db_name: str = "dit_workstation.db"

    # 设置文件目录：固定位置（默认用户数据目录），与数据库目录解耦。
    # 否则更换数据库目录后，settings.json 写入新目录，下次启动时应用在
    # 默认目录里读不到新配置，导致「修改数据库存放点」不生效。
    settings_dir: Path = field(default_factory=_resolve_data_dir)

    @property
    def effective_db_dir(self) -> Path:
        """
        返回实际可写的数据库目录。

        在 macOS 冻结态下，若 adhoc 签名应用被 TCC 拒绝写入
        ~/Library/Application Support/，则回退到 ~/.ditworkstation。
        """
        return self._writable_dir_or_fallback(self.db_dir)

    @property
    def effective_settings_dir(self) -> Path:
        """
        返回实际可写的设置文件目录（与数据库目录解耦）。

        默认使用用户数据目录，不可写时与数据库目录走同一套回退逻辑。
        """
        return self._writable_dir_or_fallback(self.settings_dir)

    @staticmethod
    def _writable_dir_or_fallback(target: Path) -> Path:
        """检查目标目录是否可写，否则依次回退到 ~/.ditworkstation、系统临时目录"""
        import tempfile
        candidates = [target, Path.home() / ".ditworkstation"]
        for cand in candidates:
            try:
                cand.mkdir(parents=True, exist_ok=True)
                # 测试是否真的可写
                with tempfile.NamedTemporaryFile(
                    dir=str(cand), prefix=".dit-write-", delete=True
                ):
                    pass
                return cand
            except (PermissionError, OSError):
                continue
        # 所有标准路径都不可写，回退到系统临时目录
        fallback = Path(tempfile.gettempdir()) / "DITWorkstation"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    # 校验和配置
    default_checksum_algorithm: str = "xxhash64"  # xxhash64 或 md5
    checksum_buffer_size: int = 1024 * 1024 * 4  # 4MB 缓冲区
    checksum_cache_size: int = 10_000  # 校验和内存缓存最大条数
    checksum_persistent_cache_size: int = 100_000  # 校验和持久化缓存最大条数

    # 历史记录保留策略
    operation_log_retention_days: int = 180  # 操作审计日志保留天数
    task_history_limit_per_project: int = 200  # 每个项目保留的最近任务数

    # 备份配置
    max_parallel_copies: int = 4  # 最大并行拷贝数
    verify_after_copy: bool = True  # 拷贝后验证

    # 存储卡自动识别
    auto_detect_volume: bool = True  # 检测到存储卡时自动跳转到导入视图并预填源目录
    auto_card_automation_enabled: bool = False  # 检测到存储卡时按规则自动导入/备份
    auto_card_import: bool = True
    auto_card_backup: bool = False
    # 非空时按此顺序执行 SOP；为空时由兼容的导入/备份开关推导步骤。
    auto_card_steps: list[str] = field(default_factory=list)
    auto_card_raw_output_dir: str = ""
    auto_card_rename_pattern: str = ""
    auto_card_report_path: str = ""
    auto_card_template_id: str = ""
    auto_card_project_id: str = ""

    # 主题模式：light / dark（重启后生效）
    theme_mode: str = "light"

    # 完整性校验调度：间隔小时数（0=禁用）；scope=all/backup
    integrity_check_interval_hours: int = 0
    integrity_check_scope: str = "all"

    # 自动更新：检查 URL（空字符串=禁用）；返回 {"version":"alpha.YYYYMMDD","download_url":"..."} 的 JSON
    auto_update_check_url: str = ""

    # 已处理的存储卡指纹列表（多卡去重）
    # 多卡去重：跳过已处理过的存储卡指纹
    skip_processed_cards: bool = True
    processed_card_fingerprints: list[str] = field(default_factory=list)

    # 保存搜索上限
    saved_search_limit: int = 20


    # 功能模式（设备级配置）：team=团队版（完整 DIT 工作流），personal=个人版
    # （隐藏团队向入口）。修改后重启生效；读取/校验逻辑见 App/feature_flags.py。
    usage_mode: str = "team"

    # 个人模式默认工作区目录：应用数据目录下的 DIT_Projects 子文件夹。
    # 个人模式不显式创建带目录的工作区，default 工作区需要一个合法物理路径，
    # 作为「复制到工作区」的目标根（<path>/<项目名>/）。
    # 可通过环境变量 DIT_PERSONAL_WS_PATH 覆盖，便于自定义默认路径（步骤4）。
    personal_default_workspace_path: Path = field(default_factory=_resolve_personal_workspace_dir)

    # 检索分页
    search_page_size: int = 500  # 素材检索每页显示的条数

    # 支持的RAW格式
    raw_extensions: list[str] = field(default_factory=lambda: [
        ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"
    ])

    # 支持的图片格式
    image_extensions: list[str] = field(default_factory=lambda: [
        ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"
    ])

    # 支持的视频格式
    video_extensions: list[str] = field(default_factory=lambda: [
        ".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".wmv", ".flv", ".webm"
    ])

    # 支持的音频格式
    audio_extensions: list[str] = field(default_factory=lambda: [
        ".mp3", ".wav", ".aac", ".flac", ".m4a", ".wma"
    ])

    # 报告输出目录（用户文档下，跨平台可写）
    report_dir: Path = field(default_factory=_resolve_report_dir)

    # 日志输出目录（可经设置界面重设）
    log_dir: Path = field(default_factory=_resolve_log_dir)

    # 缩略图缓存目录；None 时使用 effective_db_dir / "thumbnails"
    thumbnail_cache_dir: Path | None = None

    # 导入配置
    project_work_dir_name: str = "DIT_Workspace"
    import_copy_on_modify: bool = True
    import_default_compute_checksum: bool = True
    import_default_read_metadata: bool = True
    max_import_workers: int = 4  # 导入时并行处理（元数据/校验和读取）的线程数

    @property
    def db_path(self) -> Path:
        return self.effective_db_dir / self.db_name

    @property
    def effective_thumbnail_dir(self) -> Path:
        """实际缩略图缓存目录（未单独设置时跟随数据库目录）。"""
        if self.thumbnail_cache_dir:
            return self.thumbnail_cache_dir
        return self.effective_db_dir / "thumbnails"

    @property
    def all_media_extensions(self) -> list[str]:
        """所有支持的媒体格式（图片+视频+RAW+音频）"""
        return self.image_extensions + self.video_extensions + self.raw_extensions + self.audio_extensions

    def is_media_file(self, filename: str) -> bool:
        """判断是否为媒体文件"""
        ext = Path(filename).suffix.lower()
        return ext in self.all_media_extensions

    def is_image_file(self, filename: str) -> bool:
        """判断是否为图片文件"""
        ext = Path(filename).suffix.lower()
        return ext in self.image_extensions

    def is_video_file(self, filename: str) -> bool:
        """判断是否为视频文件"""
        ext = Path(filename).suffix.lower()
        return ext in self.video_extensions

    def is_raw_file(self, filename: str) -> bool:
        """判断是否为RAW文件"""
        ext = Path(filename).suffix.lower()
        return ext in self.raw_extensions

    def is_audio_file(self, filename: str) -> bool:
        """判断是否为音频文件"""
        ext = Path(filename).suffix.lower()
        return ext in self.audio_extensions

    def ensure_dirs(self):
        """确保必要目录存在（含多级回退）"""
        # db_dir 经 effective_db_dir 自动处理多级回退
        _ = self.effective_db_dir
        # report_dir 同样尝试创建，失败时复用同一回退逻辑
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            self.report_dir = self._writable_dir_or_fallback(
                Path.home() / ".ditworkstation" / "reports"
            )
        # 日志目录
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            self.log_dir = self._writable_dir_or_fallback(
                Path.home() / ".ditworkstation" / "logs"
            )


# 全局配置实例
config = AppConfig()
