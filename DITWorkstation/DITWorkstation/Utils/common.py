"""通用工具模块"""
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def generate_timestamp() -> str:
    """生成时间戳字符串"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def generate_log_message(message: str) -> str:
    """生成带时间戳的日志消息"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    return f"[{timestamp}] {message}"


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除非法字符"""
    illegal_chars = r'[\\/:*?"<>|]'
    return re.sub(illegal_chars, '_', filename)


def ensure_directory(path: str) -> bool:
    """确保目录存在"""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def get_file_extension(file_path: str, include_dot: bool = True) -> str:
    """获取文件扩展名"""
    ext = Path(file_path).suffix.lower()
    return ext if include_dot else ext.lstrip('.')


def get_file_stem(file_path: str) -> str:
    """获取文件主干（不含扩展名）"""
    return Path(file_path).stem


def path_exists(path: str) -> bool:
    """检查路径是否存在"""
    return Path(path).exists()


def is_file(path: str) -> bool:
    """检查是否为文件"""
    return Path(path).is_file()


def is_directory(path: str) -> bool:
    """检查是否为目录"""
    return Path(path).is_dir()


def get_relative_path(base: str, target: str) -> str:
    """获取相对路径"""
    return str(Path(target).relative_to(base))


def calculate_speed(elapsed_seconds: float, bytes_transferred: int) -> float:
    """计算传输速度（Mbps）"""
    if elapsed_seconds <= 0:
        return 0.0
    return (bytes_transferred / 1024 / 1024) / elapsed_seconds


class Logger:
    """简易日志记录器"""

    def __init__(self, name: str = "DITWorkstation", log_level: str = "INFO"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, log_level.upper()))

        if not self.logger.handlers:
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

            try:
                log_dir = Path.home() / ".dit_workstation" / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(
                    log_dir / f"{generate_timestamp()}.log", encoding="utf-8"
                )
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
            except PermissionError:
                pass

    def info(self, message: str):
        self.logger.info(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str, exc_info: bool = False):
        self.logger.error(message, exc_info=exc_info)

    def debug(self, message: str):
        self.logger.debug(message)


logger = Logger()
