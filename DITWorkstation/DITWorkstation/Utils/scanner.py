"""统一的可取消文件扫描器。"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

#: Windows 隐藏/系统属性；非 Windows 平台不存在这些常量，取默认值即可
_WINDOWS_HIDDEN_ATTRS = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2) | getattr(
    stat, "FILE_ATTRIBUTE_SYSTEM", 0x4
)


def scan_files(
    root: str | Path,
    extensions: Iterable[str] | None = None,
    *,
    recursive: bool = True,
    include_hidden: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    batch_size: int = 500,
    batch_callback: Callable[[list[Path]], None] | None = None,
) -> list[Path]:
    """扫描文件，支持扩展名过滤、取消和分批回调。

    提供 ``batch_callback`` 时仍返回完整列表以兼容简单调用方；长任务可仅在
    回调中消费每批并忽略返回值，后续可无缝换为纯迭代接口。
    """
    base = Path(root)
    if not base.exists():
        raise FileNotFoundError(f"目录不存在: {base}")
    if cancel_check and cancel_check():
        raise InterruptedError("文件扫描已取消")
    if base.is_file():
        return [base] if _matches(base, extensions, include_hidden) else []
    allowed = {extension.lower() for extension in extensions} if extensions else None
    files, batch = [], []
    for path in _iter_scan_paths(base, recursive, include_hidden, cancel_check):
        if cancel_check and cancel_check():
            raise InterruptedError("文件扫描已取消")
        if not _is_visible_file(path, include_hidden):
            continue
        if allowed is not None and path.suffix.lower() not in allowed:
            continue
        files.append(path)
        batch.append(path)
        if batch_callback and len(batch) >= max(1, batch_size):
            _emit_batch(batch_callback, batch)
            batch = []
    if batch_callback and batch:
        _emit_batch(batch_callback, batch)
    return files


def _iter_scan_paths(
    base: Path,
    recursive: bool,
    include_hidden: bool = False,
    cancel_check: Callable[[], bool] | None = None,
):
    """以可中断的 scandir 遍历返回路径，避免 rglob 卡住取消响应。"""
    pending = [base]
    while pending:
        if cancel_check and cancel_check():
            raise InterruptedError("文件扫描已取消")
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in reversed(entries):
            if cancel_check and cancel_check():
                raise InterruptedError("文件扫描已取消")
            if not include_hidden and _is_hidden_entry(entry):
                # 隐藏目录整体跳过，避免扫描 System Volume Information 等系统目录
                continue
            path = Path(entry.path)
            yield path
            if recursive:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                except OSError:
                    continue
        if not recursive:
            break


def _is_hidden_entry(entry: os.DirEntry) -> bool:
    """判断 scandir 条目是否隐藏（Unix 点前缀 / Windows 隐藏或系统属性）。"""
    if entry.name.startswith("."):
        return True
    if os.name != "nt":
        return False
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return False
    return bool(attributes & _WINDOWS_HIDDEN_ATTRS)


def _is_hidden_path(path: Path) -> bool:
    """判断路径是否隐藏（用于直接传入的文件/目录）。"""
    if path.name.startswith("."):
        return True
    if os.name != "nt":
        return False
    try:
        attributes = path.stat().st_file_attributes
    except OSError:
        return False
    return bool(attributes & _WINDOWS_HIDDEN_ATTRS)


def _is_visible_file(path: Path, include_hidden: bool) -> bool:
    return path.is_file() and (include_hidden or not _is_hidden_path(path))


def _emit_batch(callback: Callable[[list[Path]], None], batch: list[Path]) -> None:
    callback(batch)


def _matches(
    path: Path, extensions: Iterable[str] | None, include_hidden: bool
) -> bool:
    if not include_hidden and _is_hidden_path(path):
        return False
    return extensions is None or path.suffix.lower() in {
        ext.lower() for ext in extensions
    }
