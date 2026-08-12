"""存储卡 / 可移动卷自动识别

通过轮询系统挂载点（macOS /Volumes、Windows 盘符、Linux /media 等）检测
新插入的存储卡，识别为媒体卡后发出 volume_mounted 信号，供主窗口自动跳转
到导入视图并预填源目录。
"""
import os
import platform
import string
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from DITWorkstation.App import config
from DITWorkstation.Utils import logger


def list_volume_roots() -> list:
    """返回当前系统挂载根目录的候选路径列表（含已存在的卷）。"""
    system = platform.system()
    if system == "Darwin":
        vol = Path("/Volumes")
        if vol.is_dir():
            return [str(p) for p in vol.iterdir() if p.is_dir() and not p.name.startswith(".")]
        return []
    if system == "Windows":
        roots = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                roots.append(drive)
        return roots
    # Linux / 其他类 Unix
    roots = []
    for base in ("/media", "/mnt", "/run/media"):
        b = Path(base)
        if b.is_dir():
            roots.extend(str(p) for p in b.iterdir() if p.is_dir())
    return roots


def looks_like_card(path: str) -> bool:
    """判断路径是否像存储卡：顶层含 DCIM 目录，或至少 3 个媒体文件。

    只扫描顶层（不递归），避免大容量卡/大量小文件时轮询阻塞。
    """
    try:
        root = Path(path)
        if not root.is_dir():
            return False
        media_count = 0
        for entry in root.iterdir():
            if entry.is_dir():
                # 相机标准目录：DCIM（照片） / AVCHD / PRIVATE（视频）
                if entry.name.upper() in ("DCIM", "AVCHD", "PRIVATE", "M4ROOT"):
                    return True
            elif entry.is_file() and config.is_media_file(entry.name):
                media_count += 1
                if media_count >= 3:
                    return True
        return False
    except (OSError, PermissionError):
        return False


class VolumeMonitor(QObject):
    """轮询挂载卷变化，识别新增存储卡。"""

    volume_mounted = Signal(str)
    volume_unmounted = Signal(str)

    def __init__(self, parent=None, interval_ms: int = 3000):
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._known = set()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

    def start(self):
        """启动监听。启动时已存在的卷不触发挂载事件（避免启动即跳转）。"""
        self._known = set(list_volume_roots())
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        current = set(list_volume_roots())
        for path in sorted(current - self._known):
            if looks_like_card(path):
                logger.info(f"检测到存储卡: {path}")
                self.volume_mounted.emit(path)
        for path in sorted(self._known - current):
            self.volume_unmounted.emit(path)
        self._known = current
