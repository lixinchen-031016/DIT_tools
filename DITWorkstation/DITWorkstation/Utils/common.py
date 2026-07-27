"""通用工具模块"""
import re
import logging
import functools
from datetime import datetime
from pathlib import Path
from typing import Callable


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


# ===== 共享数据库服务单例 =====
_shared_db_service = None


def get_db_service():
    """
    返回全局共享的 DatabaseService 单例。

    各视图共用同一实例，避免启动时重复执行 _init_db / _migrate_db，
    也避免多实例并发写时无谓的锁竞争。
    """
    global _shared_db_service
    if _shared_db_service is None:
        from DITWorkstation.Services.database_service import DatabaseService
        _shared_db_service = DatabaseService()
    return _shared_db_service


# ===== 共享校验和服务单例 =====
# ChecksumService 内部维护文件级缓存（path+size+algo -> hash），
# 多个 Service 实例各自缓存会导致重复计算和内存浪费。
_shared_checksum_service = None


def get_checksum_service():
    """返回全局共享的 ChecksumService 单例。

    让 MediaImportService / BackupService / RawExtractionService 复用同一缓存，
    避免导入时已算过的 hash 在备份时再算一次。
    """
    global _shared_checksum_service
    if _shared_checksum_service is None:
        from DITWorkstation.Services.checksum_service import ChecksumService
        _shared_checksum_service = ChecksumService()
    return _shared_checksum_service


# ===== Qt 槽函数异常安全装饰器 =====
def safe_slot(error_title: str = "操作失败"):
    """
    装饰 Qt 槽函数：捕获异常并弹出 QMessageBox 提示，避免槽函数崩溃。

    用法：
        @safe_slot("删除项目失败")
        def _delete_project(self):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                logger.error(f"{error_title}: {e}", exc_info=True)
                try:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.critical(self, error_title, f"{error_title}：\n{e}")
                except Exception:
                    pass
        return wrapper
    return decorator
