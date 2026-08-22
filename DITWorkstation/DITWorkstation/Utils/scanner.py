"""统一的可取消文件扫描器。"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path


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
    for path in _iter_scan_paths(base, recursive, cancel_check):
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


def _is_visible_file(path: Path, include_hidden: bool) -> bool:
    return path.is_file() and (include_hidden or not path.name.startswith("."))


def _emit_batch(callback: Callable[[list[Path]], None], batch: list[Path]) -> None:
    callback(batch)


def _matches(
    path: Path, extensions: Iterable[str] | None, include_hidden: bool
) -> bool:
    if not include_hidden and path.name.startswith("."):
        return False
    return extensions is None or path.suffix.lower() in {
        ext.lower() for ext in extensions
    }
