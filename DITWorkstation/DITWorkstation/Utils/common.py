"""通用工具模块"""
import re
import json
import logging
import functools
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from DITWorkstation.App import config


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


# ===== 最近使用路径管理 =====
_RECENT_PATHS_KEY = "recent_directories"
_MAX_RECENT = 10


def _get_settings_path() -> Path:
    """返回 settings.json 路径（与 DB 同目录）"""
    return Path(config.effective_db_dir) / "settings.json"


def _load_settings() -> dict:
    """加载 settings.json"""
    path = _get_settings_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(settings: dict):
    """保存 settings.json"""
    path = _get_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存 settings.json 失败: {e}")


def add_recent_path(path: str, category: str = "default"):
    """记录最近使用的路径。

    Args:
        path: 目录路径
        category: 路径分类（如 'import_source', 'backup_target' 等），
                  不同分类各自维护独立的最近列表
    """
    if not path:
        return
    settings = _load_settings()
    key = f"{_RECENT_PATHS_KEY}_{category}"
    paths = settings.get(key, [])
    # 去重并移到最前
    if path in paths:
        paths.remove(path)
    paths.insert(0, path)
    # 保留最多 _MAX_RECENT 条
    paths = paths[:_MAX_RECENT]
    settings[key] = paths
    _save_settings(settings)


def get_recent_paths(category: str = "default") -> list:
    """获取最近使用的路径列表。

    Args:
        category: 路径分类

    Returns:
        最近路径字符串列表（最近使用的在最前），无记录时返回空列表
    """
    settings = _load_settings()
    key = f"{_RECENT_PATHS_KEY}_{category}"
    return settings.get(key, [])


def _show_recent_paths_dialog(parent, title: str, recent_paths: list) -> str:
    """弹出最近路径选择对话框。

    Returns:
        选中的路径、'__browse__'（浏览新目录）或空字符串（取消）。
    """
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
        QPushButton, QHBoxLayout, QLabel,
    )

    dialog = QDialog(parent)
    dialog.setWindowTitle(f"{title} - 最近使用")
    dialog.setMinimumWidth(500)
    layout = QVBoxLayout(dialog)

    layout.addWidget(QLabel("选择最近使用的目录，或点击「浏览…」选择新目录："))

    list_widget = QListWidget()
    for p in recent_paths:
        item = QListWidgetItem(p)
        item.setToolTip(p)
        list_widget.addItem(item)
    list_widget.itemDoubleClicked.connect(lambda item: dialog.done(QDialog.Accepted))
    layout.addWidget(list_widget)

    btn_row = QHBoxLayout()
    browse_btn = QPushButton("浏览…")
    browse_btn.clicked.connect(lambda: dialog.done(2))  # 自定义返回码 2 = 浏览
    btn_row.addWidget(browse_btn)
    btn_row.addStretch()

    # 如果列表非空，默认选中第一项
    if list_widget.count() > 0:
        list_widget.setCurrentRow(0)
        select_btn = QPushButton("选择")
        select_btn.clicked.connect(lambda: dialog.done(QDialog.Accepted))
        btn_row.addWidget(select_btn)

    cancel_btn = QPushButton("取消")
    cancel_btn.clicked.connect(lambda: dialog.done(QDialog.Rejected))
    btn_row.addWidget(cancel_btn)
    layout.addLayout(btn_row)

    result = dialog.exec()
    if result == QDialog.Accepted:
        item = list_widget.currentItem()
        return item.text() if item else ""
    elif result == 2:
        return "__browse__"
    else:
        return ""


def pick_directory(
    parent=None,
    title: str = "选择目录",
    start_path: str = "",
    category: str = "default",
) -> str:
    """统一的目录选择对话框（打包后兼容），支持最近使用记录。

    Windows 打包后原生 QFileDialog 可能因 COM 初始化 / manifest 问题返回空字符串
    （即使选中了目录）。若先弹原生再回退非原生，会弹两次对话框，用户取消第二次
    后路径为空，导致目标列表不显示等bug。

    此处在 frozen（PyInstaller 打包）环境下直接使用 Qt 非原生对话框；
    开发环境仍用原生以获得更好体验。

    Args:
        parent: 父窗口
        title: 对话框标题
        start_path: 起始路径
        category: 路径分类（如 'import_source', 'backup_target', 'raw_jpg' 等），
                  不同分类各自维护独立的最近使用列表

    Returns: 选中的目录路径，未选择或取消返回空字符串。
    """
    recent = get_recent_paths(category)
    # 如果有最近路径，先弹出选择对话框
    if recent:
        choice = _show_recent_paths_dialog(parent, title, recent)
        if choice == "__browse__":
            pass  # 继续走 QFileDialog
        elif not choice:
            return ""  # 用户取消
        else:
            # 用户选了最近路径，更新 MRU 顺序后直接返回
            add_recent_path(choice, category)
            return choice

    import sys
    from PySide6.QtWidgets import QFileDialog
    if getattr(sys, 'frozen', False):
        path = QFileDialog.getExistingDirectory(
            parent, title, start_path,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
    else:
        path = QFileDialog.getExistingDirectory(parent, title, start_path)
    path = path or ""
    if path:
        add_recent_path(path, category)
    return path


def pick_save_file(
    parent=None,
    title: str = "保存文件",
    start_path: str = "",
    filter_str: str = "",
    default_suffix: str = "",
) -> str:
    """统一的保存文件对话框（打包后兼容，对应 pick_directory）。

    Windows 打包后原生 QFileDialog.getSaveFileName 同样可能返回空字符串
    （与 pick_directory 同因 COM/manifest）。frozen 环境下直接使用非原生对话框。

    Args:
        default_suffix: 若用户未输入扩展名，自动附加（如 "pdf"）

    Returns: 选中的文件完整路径，未选择或取消返回空字符串。
    """
    import sys
    from PySide6.QtWidgets import QFileDialog
    options = QFileDialog.Option.DontUseNativeDialog if getattr(sys, 'frozen', False) else QFileDialog.Option(0)
    path, _ = QFileDialog.getSaveFileName(
        parent, title, start_path, filter_str, "", options
    )
    if not path:
        return ""
    if default_suffix and not Path(path).suffix:
        path = str(Path(path).with_suffix(f".{default_suffix.lstrip('.')}"))
    return path


def open_in_file_manager(path: str) -> bool:
    """跨平台在系统文件管理器中打开路径（目录或文件所在目录）。

    - 目录：直接打开
    - 文件：打开所在目录（Windows 上额外选中该文件）
    - 失败返回 False，调用方自行弹错
    """
    import sys
    import subprocess
    from pathlib import Path as _Path
    try:
        target = _Path(path)
        if target.is_file():
            dir_path = str(target.parent)
        else:
            dir_path = str(target)
        if sys.platform == "darwin":
            subprocess.Popen(["open", dir_path])
        elif sys.platform == "win32":
            # explorer /select,<file> 可同时打开目录并选中文件
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", dir_path])
        else:
            subprocess.Popen(["xdg-open", dir_path])
        return True
    except Exception as e:
        logger.error(f"打开文件管理器失败: {e}", exc_info=True)
        return False


def find_overwrite_conflicts(
    source_files: list,
    target_dirs: list,
    name_getter=None,
) -> dict:
    """扫描目标目录中与源文件同名的文件，返回冲突清单。

    用于备份/导入/提取等写操作启动前判断是否会覆盖已有文件。
    文件名比较不区分大小写（Windows/APFS 默认文件系统不区分大小写，
    避免 IMG_001.CR2 源文件与 IMG_001.cr2 目标文件被误判为不冲突）。

    Args:
        source_files: 源文件对象列表（字符串路径或对象）
        target_dirs: 目标目录字符串列表
        name_getter: 可选函数，从源文件对象中提取文件名；默认按 str 取 basename

    Returns:
        {目标目录路径: [冲突文件名列表]} 仅包含有冲突的目标目录
    """
    from pathlib import Path as _Path
    conflicts: dict = {}
    for target_dir in target_dirs:
        if not target_dir:
            continue
        target_path = _Path(target_dir)
        if not target_path.is_dir():
            continue
        existing = {p.name.lower() for p in target_path.iterdir() if p.is_file()}
        if not existing:
            continue
        clashes = []
        for src in source_files:
            if name_getter is not None:
                name = name_getter(src)
            else:
                name = _Path(str(src)).name
            if name.lower() in existing:
                clashes.append(name)
        if clashes:
            conflicts[target_dir] = clashes
    return conflicts


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
            except PermissionError as e:
                self.logger.warning(f"无法创建日志文件（仅控制台输出）: {e}")

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
        if _shared_db_service is not None:
            try:
                _shared_db_service.close_all()
            except Exception:
                pass
        _shared_db_service = None
        _shared_checksum_service = None


# ===== Qt 槽函数异常安全装饰器 =====

# 异常类型 → 友好文案映射。命中后向用户展示口语化提示，而非裸 Python 异常。
# 未命中的异常走通用文案，但仍保留"复制错误信息"按钮以便排查。
_FRIENDLY_ERROR_MAP = {
    PermissionError: (
        "没有权限访问目标路径或文件。请检查：\n"
        "  · 路径是否被其他程序占用\n"
        "  · 是否需要管理员权限\n"
        "  · macOS 下是否在「系统设置 → 隐私与安全性 → 文件与文件夹」中授权"
    ),
    FileNotFoundError: "找不到文件或目录，可能已被移动或删除。请返回上一步重新选择。",
    FileExistsError: "目标已存在同名文件，请重命名或更换目标路径后重试。",
    IsADirectoryError: "期望文件路径但收到目录路径，请检查输入。",
    NotADirectoryError: "期望目录路径但收到文件路径，请检查输入。",
    TimeoutError: "操作超时，可能因磁盘响应过慢或网络驱动器中断。请稍后重试。",
    OSError: "系统 IO 错误，可能是磁盘空间不足或路径过长。请检查磁盘状态后重试。",
    ValueError: "输入值不合法，请检查表单内容（如命名规则、场景/镜头/镜次格式）。",
    KeyError: "缺少必要的数据字段，可能因数据库记录不完整。建议重建项目或联系开发者。",
    IndexError: "列表索引越界，可能因数据被并发修改。请刷新视图后重试。",
}


def _friendly_message(e: Exception, error_title: str) -> str:
    """根据异常类型返回友好文案，未命中则返回通用文案。"""
    for exc_type, msg in _FRIENDLY_ERROR_MAP.items():
        if isinstance(e, exc_type):
            return f"{msg}\n\n（技术细节：{type(e).__name__}: {e}）"
    # 通用兜底
    return f"{error_title}。\n\n详细信息：\n{type(e).__name__}: {e}"


def safe_slot(error_title: str = "操作失败"):
    """
    装饰 Qt 槽函数：捕获异常并弹出友好的 QMessageBox 提示，避免槽函数崩溃。

    改进点：
    - 异常类型 → 口语化文案映射（PermissionError/FileNotFoundError 等）
    - 未命中映射时仍保留技术细节，便于排查
    - 提供"复制错误信息"按钮，方便用户反馈

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
                    from PySide6.QtWidgets import QMessageBox, QApplication
                    msg = _friendly_message(e, error_title)
                    box = QMessageBox(self)
                    box.setIcon(QMessageBox.Critical)
                    box.setWindowTitle(error_title)
                    box.setText(msg)
                    copy_btn = box.addButton("复制错误信息", QMessageBox.ActionRole)
                    box.addButton(QMessageBox.Ok)
                    box.exec()
                    if box.clickedButton() is copy_btn:
                        clipboard = QApplication.clipboard()
                        if clipboard is not None:
                            clipboard.setText(
                                f"{error_title}\n{type(e).__name__}: {e}\n\n"
                                f"Traceback:\n{traceback.format_exc()}"
                            )
                except Exception as e:
                    logger.debug(f"错误对话框/剪贴板操作失败: {e}")
        return wrapper
    return decorator
