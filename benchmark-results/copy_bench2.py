"""复制策略基准（第二轮）：并发扩展性与哈希重叠。

补充第一轮未覆盖的两个问题：
1. 同一卷上并发拷贝是否线性扩展（决定 in-target 并行是否有价值）；
2. 把哈希与数据搬运重叠，能否找回被哈希吃掉的吞吐。

本脚本不做任何删除操作，测试数据留在系统临时目录由 OS 回收。
"""

from __future__ import annotations

import os
import shutil
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import xxhash

MB = 1024 * 1024
WORK = Path(tempfile.mkdtemp(prefix="dit-copybench-"))
BUF = 4 * MB


def make_file(path: Path, size: int, chunk: int = 4 * MB) -> Path:
    block = os.urandom(chunk)
    written = 0
    with open(path, "wb", buffering=0) as fh:
        while written < size:
            n = min(chunk, size - written)
            fh.write(block[:n])
            written += n
    return path


def chunk_copy_hash(src: Path, dst: Path) -> str:
    h = xxhash.xxh64()
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            b = fsrc.read(BUF)
            if not b:
                break
            h.update(b)
            fdst.write(b)
    return h.hexdigest()


def hash_only(path: Path) -> str:
    h = xxhash.xxh64()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(BUF)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------- 1. 同卷并发扩展性 ----------

def bench_concurrency(blob_mb: int = 512) -> list[dict]:
    blob = make_file(WORK / "conc.src", blob_mb * MB)
    size = blob.stat().st_size
    results = []
    for workers in (1, 2, 4, 8):
        # 每个 worker 拷到独立目标，避免互相覆盖
        dsts = [WORK / f"conc.{workers}.{i}.dst" for i in range(workers)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda d: chunk_copy_hash(blob, d), dsts))
        dt = time.perf_counter() - t0
        results.append(
            {
                "workers": workers,
                "total_mb": round(size * workers / MB, 1),
                "sec": round(dt, 3),
                "agg_mbps": round(size * workers / MB / dt, 1),
                "per_worker_mbps": round(size / MB / dt, 1),
            }
        )
    return results


# ---------- 2. 哈希与搬运是否重叠 ----------

def bench_overlap(blob_mb: int = 2048, reps: int = 2) -> list[dict]:
    src = make_file(WORK / "ovl.src", blob_mb * MB)
    size = src.stat().st_size
    out = []

    def serial_single_pass(dst: Path):
        chunk_copy_hash(src, dst)

    def serial_fcopy_then_hash(dst: Path):
        # fcopyfile 搬运 + 回读目标校验（当前备份 verify=True 的形态）
        shutil.copy2(str(src), str(dst))
        hash_only(dst)

    def overlapped_fcopy_hash(dst: Path):
        # fcopyfile 在子线程（内核拷贝、释放 GIL）搬运，主线程同时哈希源文件
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(shutil.copy2, str(src), str(dst))
            hash_only(src)
            fut.result()

    def chunk_copy_only(dst: Path):
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                b = fsrc.read(BUF)
                if not b:
                    break
                fdst.write(b)

    def overlapped_chunk_only_hash(dst: Path):
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(hash_only, src)
            chunk_copy_only(dst)
            fut.result()

    def pipelined_queue(dst: Path):
        """读线程 + 写线程 + 哈希线程，用有界队列流水线化。"""
        import queue

        q1: queue.Queue = queue.Queue(maxsize=6)
        q2: queue.Queue = queue.Queue(maxsize=6)
        digest: list[str] = []

        def reader():
            with open(src, "rb") as fh:
                while True:
                    b = fh.read(BUF)
                    if not b:
                        break
                    q1.put(b)
                    q2.put(b)
            q1.put(None)
            q2.put(None)

        def writer():
            with open(dst, "wb") as fh:
                while True:
                    b = q1.get()
                    if b is None:
                        break
                    fh.write(b)

        def hasher():
            h = xxhash.xxh64()
            while True:
                b = q2.get()
                if b is None:
                    break
                h.update(b)
            digest.append(h.hexdigest())

        ts = [threading.Thread(target=t) for t in (reader, writer, hasher)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

    for name, fn in (
        ("D 单线程 分块搬运+哈希", serial_single_pass),
        ("I fcopyfile搬运 + 回读哈希", serial_fcopy_then_hash),
        ("M fcopyfile搬运 ∥ 线程哈希源", overlapped_fcopy_hash),
        ("N 分块搬运 ∥ 线程哈希(同源)", overlapped_chunk_only_hash),
        ("P 读/写/哈希 三线程流水线", pipelined_queue),
    ):
        samples = []
        for _ in range(reps):
            dst = WORK / f"ovl.{abs(hash(name)) % 99999}.dst"
            t0 = time.perf_counter()
            fn(dst)
            samples.append(time.perf_counter() - t0)
        med = statistics.median(samples)
        out.append(
            {
                "name": name,
                "sec": round(med, 3),
                "mbps": round(size / MB / med, 1),
            }
        )
    return out


# ---------- 3. 小文件（不删除，仅测量） ----------

def bench_small(count: int = 40, kb: int = 1536, reps: int = 3) -> list[dict]:
    src_dir = WORK / "small.src"
    src_dir.mkdir(parents=True, exist_ok=True)
    blob = os.urandom(kb * 1024)
    files = []
    for i in range(count):
        p = src_dir / f"IMG_{i:05d}.dng"
        p.write_bytes(blob)
        files.append(p)
    total = sum(f.stat().st_size for f in files)

    def via_copy2(dst_dir: Path):
        for f in files:
            shutil.copy2(str(f), str(dst_dir / f.name))

    def via_chunk_hash(dst_dir: Path):
        for f in files:
            chunk_copy_hash(f, dst_dir / f.name)

    def via_copy2_then_hash(dst_dir: Path):
        for f in files:
            d = dst_dir / f.name
            shutil.copy2(str(f), str(d))
            hash_only(d)

    def via_chunk_hash_with_verify(dst_dir: Path):
        for f in files:
            d = dst_dir / f.name
            chunk_copy_hash(f, d)
            hash_only(d)

    out = []
    for name, fn in (
        ("A copy2 逐文件", via_copy2),
        ("D 分块4MiB+哈希 逐文件", via_chunk_hash),
        ("G copy2 + 回读哈希(现状导入)", via_copy2_then_hash),
        ("I 分块+哈希 + 回读验证(现状备份)", via_chunk_hash_with_verify),
    ):
        samples = []
        for r in range(reps):
            dst_dir = WORK / f"small.{abs(hash(name)) % 9999}.{r}"
            dst_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            fn(dst_dir)
            samples.append(time.perf_counter() - t0)
        med = statistics.median(samples)
        out.append(
            {
                "name": name,
                "files": count,
                "total_mb": round(total / MB, 1),
                "sec": round(med, 4),
                "mbps": round(total / MB / med, 1),
                "files_per_sec": round(count / med, 1),
                "ms_per_file": round(med / count * 1000, 3),
            }
        )
    return out


def main():
    print(f"临时工作目录: {WORK}\n")
    print("=== 同卷并发扩展性（512MiB × N，含分块哈希）===")
    print(f"{'workers':>8}{'总MB':>10}{'耗时(s)':>10}{'聚合MB/s':>12}{'单线程MB/s':>12}")
    for r in bench_concurrency():
        print(
            f"{r['workers']:>8}{r['total_mb']:>10}{r['sec']:>10}"
            f"{r['agg_mbps']:>12}{r['per_worker_mbps']:>12}"
        )

    print("\n=== 哈希与搬运重叠（2048MiB）===")
    print(f"{'方案':<34}{'MB/s':>10}{'耗时(s)':>10}")
    for r in bench_overlap():
        print(f"{r['name']:<34}{r['mbps']:>10}{r['sec']:>10}")

    print("\n=== 小文件 40 × 1.5MiB ===")
    print(f"{'方案':<36}{'MB/s':>9}{'文件/秒':>10}{'ms/文件':>10}")
    for r in bench_small():
        print(
            f"{r['name']:<36}{r['mbps']:>9}{r['files_per_sec']:>10}{r['ms_per_file']:>10}"
        )
    print(f"\n（测试数据保留在 {WORK}，未执行删除）")


if __name__ == "__main__":
    main()
