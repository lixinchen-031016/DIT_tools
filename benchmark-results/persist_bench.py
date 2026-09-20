"""备份快照持久化的 O(N²) 成本量化。

BackupService._copy_to_target 每完成一个文件就调用一次 persist_callback，
→ _persist_snapshot → db_service.save_backup_job(job)，
而 save_backup_job 每次都会把 job._files_cache（全部文件的快照列表）
整体 json.dumps 后写库。设文件数为 N，则总序列化量为 O(N²)。

本脚本用与 BackupJobRepository.save 相同结构的快照字典，测量：
1. 单个文件耗时随 N 的变化；
2. 一次备份全程的快照持久化累计耗时（估算）。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path

MB = 1024 * 1024


def make_snapshots(n: int) -> list[dict]:
    """构造与 scan_source 输出同结构的文件快照。"""
    return [
        {
            "path": f"/Volumes/CARD_A/PRIVATE/M4ROOT/CLIP/SC{i // 400:03d}/A{ i:05d}_C001.braw",
            "size": 12_884_901,
            "name": f"A{i:05d}_C001.braw",
            "relative": f"CLIP/SC{i // 400:03d}/A{i:05d}_C001.braw",
            "mtime_ns": 1757000000000000000,
            "checksum": "b4c9f6e3a1d2508f",
        }
        for i in range(n)
    ]


def bench_serialize() -> list[dict]:
    rows = []
    for n in (200, 500, 1000, 2000, 4000):
        snaps = make_snapshots(n)
        payload = json.dumps(snaps, ensure_ascii=False, default=str)
        size = len(payload.encode())
        reps = 20 if n <= 1000 else 5
        t0 = time.perf_counter()
        for _ in range(reps):
            json.dumps(snaps, ensure_ascii=False, default=str)
        per_call = (time.perf_counter() - t0) / reps
        rows.append(
            {
                "files": n,
                "payload_kb": round(size / 1024, 1),
                "per_snapshot_ms": round(per_call * 1000, 3),
                "total_for_whole_job_s": round(per_call * n, 2),
                "total_json_mb": round(size * n / MB, 1),
            }
        )
    return rows


def bench_sqlite_write() -> list[dict]:
    """测量把整个快照写入 SQLite 的成本（每文件一次）。"""
    rows = []
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "jobs.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE backup_jobs (job_id TEXT PRIMARY KEY, targets_json TEXT,"
            " failed_files_json TEXT, file_snapshots TEXT)"
        )
        conn.commit()
        for n in (200, 1000, 2000):
            snaps = make_snapshots(n)
            payload = json.dumps(snaps, ensure_ascii=False, default=str)
            write_reps = 10
            t0 = time.perf_counter()
            for i in range(write_reps):
                conn.execute(
                    "INSERT OR REPLACE INTO backup_jobs VALUES (?,?,?,?)",
                    ("job1", "[]", "[]", payload),
                )
                conn.commit()
            per_write = (time.perf_counter() - t0) / write_reps
            rows.append(
                {
                    "files": n,
                    "payload_kb": round(len(payload.encode()) / 1024, 1),
                    "per_write_ms": round(per_write * 1000, 3),
                    "total_for_whole_job_s": round(per_write * n, 2),
                    "total_db_mb": round(len(payload.encode()) * n / MB, 1),
                }
            )
        conn.close()
    return rows


def main():
    print("=== 快照 JSON 序列化（save_backup_job 每次调用的固定成本）===")
    print(
        f"{'文件数N':>8}{'单次载荷KB':>12}{'单次耗时ms':>12}"
        f"{'全程累计s':>12}{'累计JSON MB':>14}"
    )
    for r in bench_serialize():
        print(
            f"{r['files']:>8}{r['payload_kb']:>12}{r['per_snapshot_ms']:>12}"
            f"{r['total_for_whole_job_s']:>12}{r['total_json_mb']:>14}"
        )

    print("\n=== 快照整体写库（每文件一次 INSERT OR REPLACE + commit）===")
    print(
        f"{'文件数N':>8}{'单次载荷KB':>12}{'单次写库ms':>12}"
        f"{'全程累计s':>12}{'累计写库MB':>14}"
    )
    for r in bench_sqlite_write():
        print(
            f"{r['files']:>8}{r['payload_kb']:>12}{r['per_write_ms']:>12}"
            f"{r['total_for_whole_job_s']:>12}{r['total_db_mb']:>14}"
        )
    print(
        "\n注：这是持久化机制本身的开销（不含文件拷贝），"
        "随文件数呈二次增长，且在 _persist_lock 下由所有目标线程串行承担。"
    )


if __name__ == "__main__":
    main()
