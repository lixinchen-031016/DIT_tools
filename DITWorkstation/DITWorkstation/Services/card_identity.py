"""存储卡介质身份与多卡批处理队列。

目标：
1. 介质身份：对存储卡内容计算稳定指纹（顶层媒体文件名 + 关键目录结构），
   用于「自动处理过的卡不再重复处理」和跨插拔识别同一张卡。
2. 多卡批处理：把多张卡排入队列串行处理，避免并发拷贝互相抢 IO，
   单卡失败不影响队列中后续卡片。

指纹策略说明：
- 采用「顶层媒体文件名 + 关键目录名」而不是卷序列号（跨平台读取困难），
  对同一张卡的内容唯一性足够；采样最多 FINGERPRINT_MAX_FILES 个文件名，
  避免超大型卡枚举过慢。
"""

import hashlib
from pathlib import Path

from DITWorkstation.App import config

# 指纹采样上限：同一卡只取前 N 个顶层媒体文件做哈希
FINGERPRINT_MAX_FILES = 200


def media_files_in_top_level(
    root: Path, limit: int = FINGERPRINT_MAX_FILES
) -> list[str]:
    """返回根目录下（非递归）媒体文件的文件名列表，按名称排序。"""
    if not root.is_dir():
        return []
    names: list[str] = []
    try:
        for entry in root.iterdir():
            if entry.is_file() and config.is_media_file(entry.name):
                names.append(entry.name)
                if len(names) >= limit:
                    break
    except (OSError, PermissionError):
        pass
    return sorted(names)


def key_directories(root: Path) -> list[str]:
    """返回根目录下与相机相关的顶层目录名（DCIM/AVCHD/PRIVATE 等）。"""
    if not root.is_dir():
        return []
    try:
        return sorted(
            e.name
            for e in root.iterdir()
            if e.is_dir()
            and e.name.upper() in ("DCIM", "AVCHD", "PRIVATE", "M4ROOT", "MISC")
        )
    except (OSError, PermissionError):
        return []


def compute_card_fingerprint(path: str) -> str:
    """计算存储卡内容指纹。

    哈希内容 = 关键目录名 + 且顶层媒体文件名（采样排序后）。
    返回固定长度十六进制字符串；空卡/不可读返回 ""。
    """
    root = Path(path)
    dirs = key_directories(root)
    names = media_files_in_top_level(root)
    payload = "|".join(dirs + names)
    if not payload:
        return ""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def is_fingerprint_processed(fingerprint: str) -> bool:
    """指纹是否已在已处理列表中（防重复自动处理）。"""
    if not fingerprint:
        return False
    return fingerprint in getattr(config, "processed_card_fingerprints", [])


def mark_fingerprint_processed(fingerprint: str) -> None:
    """记录指纹到已处理列表（最多保留 50 条，防止无限增长）。"""
    from DITWorkstation.Utils import save_app_settings

    if not fingerprint:
        return
    current = list(getattr(config, "processed_card_fingerprints", []))
    if fingerprint in current:
        return
    current.append(fingerprint)
    config.processed_card_fingerprints = current[-50:]
    save_app_settings(processed_card_fingerprints=config.processed_card_fingerprints)


class CardBatchQueue:
    """多卡串行处理队列。

    使用方式（主窗口持有单例）：
        queue = CardBatchQueue()
        queue.on_start(path)  # 插入队列，队列空闲时立即回调 start_cb(path)
        queue.on_finished()   # 处理完成，派发下一张
        queue.on_failed()     # 处理失败，同样派发下一张
    """

    def __init__(self, start_cb=None, dedupe: bool = True):
        self._queue: list[str] = []
        self._in_flight: str | None = None
        self._start_cb = start_cb
        self._dedupe = dedupe

    def set_start_callback(self, cb) -> None:
        self._start_cb = cb

    @property
    def in_flight(self) -> str | None:
        return self._in_flight

    @property
    def queued(self) -> list[str]:
        return list(self._queue)

    @property
    def busy(self) -> bool:
        return self._in_flight is not None

    def enqueue(self, path: str) -> bool:
        """入队；若内容指纹已处理过且开启去重，则不入队。返回是否真正入队。"""
        if not path or path in self._queue:
            return False
        if self._in_flight == path:
            return False
        if self._dedupe and is_fingerprint_processed(compute_card_fingerprint(path)):
            return False
        self._queue.append(path)
        if self._in_flight is None:
            self._pump()
        return True

    def _pump(self) -> None:
        if self._in_flight is not None:
            return
        if not self._queue:
            return
        self._in_flight = self._queue.pop(0)
        if self._start_cb:
            try:
                self._start_cb(self._in_flight)
            except Exception as exc:
                from DITWorkstation.Utils import logger

                logger.error(f"存储卡队列启动回调失败: {exc}")
                self.on_failed()

    def on_finished(self, fingerprint: str = "") -> None:
        """当前卡处理完成：记录指纹并派发下一张。"""
        if fingerprint:
            mark_fingerprint_processed(fingerprint)
        self._in_flight = None
        self._pump()

    def on_failed(self) -> None:
        """当前卡处理失败：不记录指纹（下次仍可重试），直接派发下一张。"""
        self._in_flight = None
        self._pump()

    def clear(self) -> None:
        self._queue.clear()
