"""CSV 导出统一入口。

所有 CSV 导出都走 ``utf-8-sig``（带 BOM），保证 Excel 在 Windows/macOS
直接打开中文不乱码；取消与进度回调语义也在本模块统一，避免各导出点
各自实现一套写文件循环。
"""

import csv
from pathlib import Path


def write_csv_rows(
    path: str | Path,
    headers: list[str],
    rows,
    *,
    encoding: str = "utf-8-sig",
    cancel_check=None,
    progress_callback=None,
) -> int:
    """把可迭代行写入 CSV，返回写入的数据行数。

    Args:
        path: 输出文件路径；父目录不存在时自动创建。
        headers: 表头列名。
        rows: 任意可迭代对象，每行是一个可写序列。
        encoding: 文本编码，默认 utf-8-sig。
        cancel_check: 可选取消检查；返回 True 时抛出 InterruptedError。
        progress_callback: 可选回调 ``(count, row)``，用于进度展示。

    Returns:
        实际写入的数据行数（不含表头）。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(target, "w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            if cancel_check and cancel_check():
                raise InterruptedError("CSV 导出已取消")
            writer.writerow(row)
            count += 1
            if progress_callback:
                progress_callback(count, row)
    return count
