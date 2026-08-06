"""应用配置管理"""
import os
import sys
import platform
from pathlib import Path
from dataclasses import dataclass, field
from typing import List


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


@dataclass
class AppConfig:
    """应用全局配置"""
    # 数据库路径（开发态：代码库下 data/；冻结态：用户数据目录）
    db_dir: Path = field(default_factory=_resolve_data_dir)
    db_name: str = "dit_workstation.db"

    @property
    def effective_db_dir(self) -> Path:
        """
        返回实际可写的数据库目录。

        在 macOS 冻结态下，若 adhoc 签名应用被 TCC 拒绝写入
        ~/Library/Application Support/，则回退到 ~/.ditworkstation。
        """
        return self._writable_dir_or_fallback(self.db_dir)

    @staticmethod
    def _writable_dir_or_fallback(target: Path) -> Path:
        """检查目标目录是否可写，否则依次回退到 ~/.ditworkstation、系统临时目录"""
        import tempfile
        candidates = [target, Path.home() / ".ditworkstation"]
        for cand in candidates:
            try:
                cand.mkdir(parents=True, exist_ok=True)
                # 测试是否真的可写
                test_file = cand / ".write_test"
                test_file.touch()
                test_file.unlink()
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

    # 备份配置
    max_parallel_copies: int = 4  # 最大并行拷贝数
    copy_buffer_size: int = 1024 * 1024 * 8  # 8MB 拷贝缓冲区
    verify_after_copy: bool = True  # 拷贝后验证

    # 支持的RAW格式
    raw_extensions: List[str] = field(default_factory=lambda: [
        ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"
    ])

    # 支持的图片格式
    image_extensions: List[str] = field(default_factory=lambda: [
        ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"
    ])

    # 支持的视频格式
    video_extensions: List[str] = field(default_factory=lambda: [
        ".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".wmv", ".flv", ".webm"
    ])

    # 支持的音频格式
    audio_extensions: List[str] = field(default_factory=lambda: [
        ".mp3", ".wav", ".aac", ".flac", ".m4a", ".wma"
    ])

    # 报告输出目录（用户文档下，跨平台可写）
    report_dir: Path = field(default_factory=_resolve_report_dir)

    # 导入配置
    project_work_dir_name: str = "DIT_Workspace"
    import_copy_on_modify: bool = True
    import_default_compute_checksum: bool = True
    import_default_read_metadata: bool = True

    @property
    def db_path(self) -> Path:
        return self.effective_db_dir / self.db_name

    @property
    def all_media_extensions(self) -> List[str]:
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


# 全局配置实例
config = AppConfig()
