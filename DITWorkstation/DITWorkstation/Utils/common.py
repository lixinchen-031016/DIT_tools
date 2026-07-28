"""通用工具模块"""
import re
import logging
import functools
import threading
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


def normalize_path(file_path: str) -> str:
    """规范化文件路径，作为 DB 中 file_path 的统一存储/查询键。

    - 使用 Path.resolve() 解析符号链接（macOS /var → /private/var）、相对路径、`..`
    - resolve 失败（文件已删除/权限问题）时回退到原始字符串，保证查询不抛异常
    - 所有入库（media_import_service）与按路径查询（database_service /
      rename_service / raw_extraction_service）必须经过本函数，避免表示形式
      不一致导致匹配失败（典型表现：重命名后 DB 路径未更新、RAW 提取无法
      继承 JPG 的 log_id）

    Args:
        file_path: 原始路径字符串

    Returns:
        规范化后的绝对路径字符串
    """
    if not file_path:
        return file_path
    try:
        return str(Path(file_path).resolve())
    except (OSError, ValueError):
        # resolve 失败（如文件已不存在、符号链接断裂），回退到原始字符串
        return file_path


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


def pick_directory(parent=None, title: str = "选择目录", start_path: str = "") -> str:
    """统一的目录选择对话框（打包后兼容）。

    Windows 打包后原生 QFileDialog 可能因 COM 初始化 / manifest 问题返回空字符串
    （即使选中了目录）。若先弹原生再回退非原生，会弹两次对话框，用户取消第二次
    后路径为空，导致目标列表不显示等bug。

    此处在 frozen（PyInstaller 打包）环境下直接使用 Qt 非原生对话框；
    开发环境仍用原生以获得更好体验。

    Returns: 选中的目录路径，未选择或取消返回空字符串。
    """
    import sys
    from PySide6.QtWidgets import QFileDialog
    if getattr(sys, 'frozen', False):
        path = QFileDialog.getExistingDirectory(
            parent, title, start_path,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
    else:
        path = QFileDialog.getExistingDirectory(parent, title, start_path)
    return path or ""


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
_singleton_lock = threading.Lock()


def get_db_service():
    """
    返回全局共享的 DatabaseService 单例。

    各视图共用同一实例，避免启动时重复执行 _init_db / _migrate_db，
    也避免多实例并发写时无谓的锁竞争。

    线程安全：使用 _singleton_lock 保护 check-then-set，避免主线程与 worker
    线程同时首次调用时各自创建实例导致单例失效。
    """
    global _shared_db_service
    if _shared_db_service is None:
        with _singleton_lock:
            # double-check：拿到锁后再次确认，避免等待期间已被其他线程初始化
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

    线程安全：同 get_db_service，使用 double-checked locking。
    """
    global _shared_checksum_service
    if _shared_checksum_service is None:
        with _singleton_lock:
            if _shared_checksum_service is None:
                from DITWorkstation.Services.checksum_service import ChecksumService
                _shared_checksum_service = ChecksumService()
    return _shared_checksum_service


def reset_singletons():
    """重置全局单例（DatabaseService + ChecksumService）。

    供单元测试在 setup/teardown 中调用，确保每个测试使用独立的 DB 实例，
    避免跨测试数据污染。生产代码不应调用此函数。
    """
    global _shared_db_service, _shared_checksum_service
    with _singleton_lock:
        _shared_db_service = None
        _shared_checksum_service = None


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
