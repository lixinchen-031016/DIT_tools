"""同条件对照：哈希开销 / 回读验证开销 / 流水线收益（单次会话内可比）。

同一源文件、同一轮会话、每个变体 5 次取中位数，消除跨轮次页缓存差异。
"""

from __future__ import annotations

import os
import queue
import shutil
import statistics
import tempfile
import threading
import time
from pathlib import Path

import xxhash

MB = 1024 * 1024
BUF = 4 * MB
SIZE = 2048 * MB
REPS = 5
WORK = Path(tempfile.mkdtemp(prefix="dit-copybench3-"))
SRC = WORK / "src.bin"


def build_src():
    block = os.urandom(4 * MB)
    with open(SRC, "wb", buffering=0) as fh:
        written = 0
        while written < SIZE:
            n = min(4 * MB, SIZE - written)
            fh.write(block[:n])
            written += n


def _hash(path: Path) -> str:
    h = xxhash.xxh64()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(BUF)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def v_no_hash(dst: Path):
    with open(SRC, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            b = fsrc.read(BUF)
            if not b:
                break
            fdst.write(b)


def v_hash(dst: Path):
    h = xxhash.xxh64()
    with open(SRC, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            b = fsrc.read(BUF)
            if not b:
                break
            h.update(b)
            fdst.write(b)
    return h.hexdigest()


def v_hash_readback(dst: Path):
    v_hash(dst)
    return _hash(dst)


def v_pipeline(dst: Path):
    q1: queue.Queue = queue.Queue(maxsize=8)
    q2: queue.Queue = queue.Queue(maxsize=8)

    def reader():
        with open(SRC, "rb") as fh:
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

    ts = [
        threading.Thread(target=reader),
        threading.Thread(target=writer),
        threading.Thread(target=hasher),
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()


def v_copy2(dst: Path):
    shutil.copy2(str(SRC), str(dst))


VARIANTS = [
    ("E 分块4MiB 无哈希（IO 上限）", v_no_hash),
    ("D 分块4MiB + 哈希（现状备份搬运）", v_hash),
    ("V 分块 + 哈希 + 回读验证（备份默认）", v_hash_readback),
    ("P 读/写/哈希 三线程流水线", v_pipeline),
    ("A copy2 fcopyfile（现状导入）", v_copy2),
]


def main():
    build_src()
    print(f"源文件 {SIZE // MB} MiB，每变体 {REPS} 次取中位数，同一会话内可比\n")
    print(f"{'方案':<34}{'MB/s':>10}{'耗时(s)':>10}{'相对IO上限':>12}")
    base = None
    for name, fn in VARIANTS:
        samples = []
        for i in range(REPS):
            dst = WORK / f"out.{abs(hash(name)) % 9999}.{i}.bin"
            t0 = time.perf_counter()
            fn(dst)
            samples.append(time.perf_counter() - t0)
        med = statistics.median(samples)
        mbps = SIZE / MB / med
        if base is None:
            base = mbps
        print(f"{name:<34}{mbps:>10.1f}{med:>10.3f}{mbps / base * 100:>11.1f}%")
    print(f"\n临时数据保留在 {WORK}")


if __name__ == "__main__":
    main()
