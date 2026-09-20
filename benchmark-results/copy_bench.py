"""DIT 工作站文件复制策略基准测试。

对比当前代码库中实际使用的复制方式与若干候选优化方案，
量化「同一份数据被读写几次」以及缓冲区/并发带来的差异。

运行：.venv/bin/python benchmark-results/copy_bench.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import xxhash

ROOT = Path(__file__).resolve().parent
WORK = ROOT / ".bench-tmp"
WORK.mkdir(parents=True, exist_ok=True)

BIG_MB = 2048          # 大文件尺寸
REPS = 3               # 每个变体重复次数
SMALL_COUNT = 800      # 小文件数量
SMALL_KB = 1536        # 小文件尺寸

MB = 1024 * 1024


def human(n: float) -> str:
    return f"{n / MB:.1f}"


def make_file(path: Path, size: int, chunk: int = 4 * MB, compressible: bool = False):
    """生成测试文件。compressible=True 时用可压缩内容（模拟 ProRes/BRAW）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    block = b"\x00" * chunk if compressible else os.urandom(chunk)
    written = 0
    with open(path, "wb", buffering=0) as fh:
        while written < size:
            n = min(chunk, size - written)
            fh.write(block[:n])
            written += n
    return path


# ---------- 候选实现 ----------

def copy_shutil_copy2(src: Path, dst: Path) -> int:
    """当前 MediaImportService._copy_to_workspace / BackupRestoreService._restore_one 用法。"""
    shutil.copy2(str(src), str(dst))
    return 0


def copy_copyfileobj_default(src: Path, dst: Path) -> int:
    """当前 ArchiveService 还原暂存用法（shutil.copyfileobj 默认 64KiB）。"""
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst)
    return 0


def _chunk_loop(src: Path, dst: Path, buf: int, hasher_name: str | None) -> int:
    if hasher_name == "xxh64":
        hasher = xxhash.xxh64()
    elif hasher_name == "md5":
        hasher = hashlib.md5()
    else:
        hasher = None
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            chunk = fsrc.read(buf)
            if not chunk:
                break
            if hasher is not None:
                hasher.update(chunk)
            fdst.write(chunk)
    return 0 if hasher is None else int(hasher.intdigest())


def copy_loop_4m_xxhash(src: Path, dst: Path) -> int:
    """当前 ChecksumService.copy_file_with_checksum（4MiB 缓冲 + xxhash64）。"""
    return _chunk_loop(src, dst, 4 * MB, "xxh64")


def copy_loop_16m_xxhash(src: Path, dst: Path) -> int:
    """候选：16MiB 缓冲 + xxhash64。"""
    return _chunk_loop(src, dst, 16 * MB, "xxh64")


def copy_loop_4m_noop(src: Path, dst: Path) -> int:
    """当前 copy_file_with_checksum 去掉哈希，用于隔离哈希开销。"""
    return _chunk_loop(src, dst, 4 * MB, None)


def copy_loop_64k_xxhash(src: Path, dst: Path) -> int:
    """回归对照：64KiB + xxhash（验证缓冲区过小的影响）。"""
    return _chunk_loop(src, dst, 64 * 1024, "xxh64")


def copy_clone(src: Path, dst: Path) -> int:
    """候选：APFS clonefile（同卷写时复制，跨卷不可用），经 cp -c 调用。"""
    subprocess.run(["cp", "-c", str(src), str(dst)], check=True)
    return 0


def copy_then_hash_dst(src: Path, dst: Path) -> int:
    """当前 MediaImportService 复制模式：copy2 之后再读一遍目标算哈希。"""

    def _hash(path: Path) -> str:
        h = xxhash.xxh64()
        with open(path, "rb") as fh:
            while True:
                b = fh.read(4 * MB)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()

    shutil.copy2(str(src), str(dst))
    _hash(dst)
    return 0


def copy_single_pass_src_hash(src: Path, dst: Path) -> int:
    """候选：单趟读源 + 写目标同时算哈希（等价产出，少读一遍目标）。"""
    return _chunk_loop(src, dst, 4 * MB, "xxh64")


def copy_then_verify_readback(src: Path, dst: Path) -> int:
    """当前备份默认 verify=True 的完整成本：写入 + 回读目标全量哈希。"""
    _chunk_loop(src, dst, 4 * MB, "xxh64")

    def _hash(path: Path) -> None:
        h = xxhash.xxh64()
        with open(path, "rb") as fh:
            while True:
                b = fh.read(4 * MB)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()

    _hash(dst)
    return 0


VARIANTS = [
    ("A copy2(fcopyfile)", "现状·媒体导入/素材回拷", copy_shutil_copy2),
    ("B copyfileobj 64KiB", "现状·归档还原暂存", copy_copyfileobj_default),
    ("C chunk 64KiB+xxh", "对照·缓冲过小", copy_loop_64k_xxhash),
    ("D chunk 4MiB+xxh", "现状·备份/RAW提取", copy_loop_4m_xxhash),
    ("E chunk 4MiB 无哈希", "隔离·哈希开销", copy_loop_4m_noop),
    ("F chunk 16MiB+xxh", "候选·放大缓冲", copy_loop_16m_xxhash),
    ("G copy2 + 哈希目标", "现状·媒体导入复制模式", copy_then_hash_dst),
    ("H 单趟读源混合哈希", "候选·等价语义省一趟", copy_single_pass_src_hash),
    ("I 单趟 + 回读验证", "现状·备份 verify=True", copy_then_verify_readback),
    ("J APFS clone", "候选·同卷写时复制", copy_clone),
]


def bench_big() -> list[dict]:
    """大文件对照。每个重复写入独立目标路径，全程不执行删除操作。"""
    src = make_file(WORK / "big.src", BIG_MB * MB)
    size = src.stat().st_size
    results = []
    for v_idx, (name, note, fn) in enumerate(VARIANTS):
        samples: list[float] = []
        failed_note = None
        for rep in range(REPS):
            dst = WORK / f"big.{v_idx:02d}.{rep}.dst"
            t0 = time.perf_counter()
            try:
                fn(src, dst)
            except Exception as exc:  # 记录不可用方案
                failed_note = f"{note}（不可用: {exc}）"
                break
            samples.append(time.perf_counter() - t0)
        if failed_note or not samples:
            results.append(
                {"name": name, "note": failed_note or note, "mbps": 0.0, "sec": None}
            )
            continue
        med = statistics.median(samples)
        results.append(
            {
                "name": name,
                "note": note,
                "sec": round(med, 3),
                "mbps": round(size / MB / med, 1),
            }
        )
    return results


def bench_small() -> list[dict]:
    """小文件场景：逐文件调用开销 vs 缓冲策略。

    默认跳过（DIT_BENCH_SMALL=1 时启用）。小文件数据以 copy_bench2.py 为准：
    那里不执行任何删除操作，避免触发宿主环境的批量删除保护。
    """
    if os.environ.get("DIT_BENCH_SMALL") != "1":
        return []
    src_dir = WORK / "small.src"
    if not src_dir.exists():
        for i in range(SMALL_COUNT):
            make_file(src_dir / f"IMG_{i:05d}.dng", SMALL_KB * 1024)
    files = sorted(src_dir.glob("*.dng"))
    size = sum(f.stat().st_size for f in files)
    picks = [
        ("A copy2 逐文件", copy_shutil_copy2),
        ("B copyfileobj 64KiB", copy_copyfileobj_default),
        ("D chunk 4MiB+xxh", copy_loop_4m_xxhash),
        ("H 单趟读源混合哈希", copy_single_pass_src_hash),
    ]
    results = []
    for name, fn in picks:
        dst_dir = WORK / f"small.dst.{abs(hash(name)) % 9999}"
        dst_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        for f in files:
            fn(f, dst_dir / f.name)
        dt = time.perf_counter() - t0
        results.append(
            {
                "name": name,
                "files": len(files),
                "total_mb": round(size / MB, 1),
                "sec": round(dt, 3),
                "mbps": round(size / MB / dt, 1),
                "files_per_sec": round(len(files) / dt, 1),
            }
        )
    return results


def main():
    print(f"平台: macOS / 文件系统 APFS / Python {os.sys.version.split()[0]}")
    print(f"大文件 {BIG_MB} MiB × {REPS} 次取中位数\n")
    big = bench_big()
    print(f"{'方案':<24}{'说明':<26}{'MB/s':>9}{'耗时(s)':>10}")
    print("-" * 72)
    for r in big:
        sec = f"{r['sec']:.3f}" if r["sec"] is not None else "-"
        print(f"{r['name']:<24}{r['note']:<26}{r['mbps']:>9}{sec:>10}")

    small = bench_small()
    if small:
        print(f"\n小文件 {SMALL_COUNT} × {SMALL_KB} KiB\n")
        print(f"{'方案':<24}{'MB/s':>9}{'文件/秒':>10}{'耗时(s)':>10}")
        print("-" * 56)
        for r in small:
            print(
                f"{r['name']:<24}{r['mbps']:>9}{r['files_per_sec']:>10}{r['sec']:>10}"
            )
    else:
        print(
            "\n（小文件阶段已跳过；设置 DIT_BENCH_SMALL=1 启用，"
            "或运行 copy_bench2.py 查看小文件数据）"
        )

    payload = {"big_file": big, "small_file": small}
    (ROOT / "copy-bench.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# 文件复制策略基准（实际测得）", ""]
    lines.append(f"- 平台：macOS / APFS / Apple M4 / Python {os.sys.version.split()[0]}")
    lines.append(f"- 大文件：{BIG_MB} MiB，{REPS} 次取中位数")
    lines.append("")
    lines.append("| 方案 | 说明 | MB/s | 耗时(s) |")
    lines.append("| --- | --- | ---: | ---: |")
    for r in big:
        sec = f"{r['sec']:.3f}" if r["sec"] is not None else "-"
        lines.append(f"| {r['name']} | {r['note']} | {r['mbps']} | {sec} |")
    lines.append("")
    if small:
        lines.append(f"小文件：{SMALL_COUNT} × {SMALL_KB} KiB")
        lines.append("")
        lines.append("| 方案 | MB/s | 文件/秒 | 耗时(s) |")
        lines.append("| --- | ---: | ---: | ---: |")
        for r in small:
            lines.append(
                f"| {r['name']} | {r['mbps']} | {r['files_per_sec']} | {r['sec']} |"
            )
    (ROOT / "copy-bench.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {ROOT / 'copy-bench.md'}")
    print(f"（测试数据保留在 {WORK}，未执行删除；可手动清理）")


if __name__ == "__main__":
    main()
