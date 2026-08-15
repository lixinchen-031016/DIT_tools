"""统一的可取消文件扫描器。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional


def scan_files(
    root: str | Path,
    extensions: Optional[Iterable[str]] = None,
    *,
    recursive: bool = True,
    include_hidden: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
    batch_size: int = 500,
    batch_callback: Optional[Callable[[list[Path]], None]] = None,
) -> list[Path]:
    """扫描文件，支持扩展名过滤、取消和分批回调。

    提供 ``batch_callback`` 时仍返回完整列表以兼容简单调用方；长任务可仅在
    回调中消费每批并忽略返回值，后续可无缝换为纯迭代接口。
    """
    base = Path(root)
    if not base.exists():
        raise FileNotFoundError(f"目录不存在: {base}")
    if base.is_file():
        return [base] if _matches(base, extensions, include_hidden) else []
    allowed = {extension.lower() for extension in extensions} if extensions else None
    iterator = base.rglob("*") if recursive else base.glob("*")
    files, batch = [], []
    for path in iterator:
        if cancel_check and cancel_check():
            raise InterruptedError("文件扫描已取消")
        if not path.is_file() or (not include_hidden and path.name.startswith(".")):
            continue
        if allowed is not None and path.suffix.lower() not in allowed:
            continue
        files.append(path)
        batch.append(path)
        if batch_callback and len(batch) >= max(1, batch_size):
            batch_callback(batch)
            batch = []
    if batch_callback and batch:
        batch_callback(batch)
    return files


def _matches(path: Path, extensions: Optional[Iterable[str]], include_hidden: bool) -> bool:
    if not include_hidden and path.name.startswith("."):
        return False
    return extensions is None or path.suffix.lower() in {ext.lower() for ext in extensions}
