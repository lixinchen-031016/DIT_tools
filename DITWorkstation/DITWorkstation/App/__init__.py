"""应用配置管理"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List


@dataclass
class AppConfig:
    """应用全局配置"""
    # 数据库路径（代码库根目录下的 data 文件夹）
    db_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent.parent / "data")
    db_name: str = "dit_workstation.db"

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

    # 报告输出目录
    report_dir: Path = field(default_factory=lambda: Path.home() / "Documents" / "DIT_Reports")

    # 导入配置
    project_work_dir_name: str = "DIT_Workspace"
    import_copy_on_modify: bool = True
    import_default_compute_checksum: bool = True
    import_default_read_metadata: bool = True

    @property
    def db_path(self) -> Path:
        return self.db_dir / self.db_name

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
        """确保必要目录存在"""
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)


# 全局配置实例
config = AppConfig()
