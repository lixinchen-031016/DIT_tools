#!/usr/bin/env python3
"""DIT Workstation 数据库工作负载基准。

此脚本为优化路线的阶段 0 提供可重复的数据库基线。它生成独立 SQLite
数据库，不会读取或修改应用实际数据。结果同时写为 JSON 和 Markdown，便于
在后续性能改动前后比较。

示例：
    python build/benchmark_database_workloads.py --assets 10000
    python build/benchmark_database_workloads.py --assets 10000,50000,100000 \
        --output-dir /tmp/dit-benchmarks
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import resource
import shutil
import sys
import tempfile
import time
import tracemalloc
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, TypeVar


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "DITWorkstation"
if str(APP_SOURCE) not in sys.path:
    sys.path.insert(0, str(APP_SOURCE))

from DITWorkstation.Models import ChecksumAlgorithm, MediaAsset  # noqa: E402
from DITWorkstation.Services.archive_service import ArchiveService  # noqa: E402
from DITWorkstation.Services.checksum_service import ChecksumService  # noqa: E402
from DITWorkstation.Services.database_service import DatabaseService  # noqa: E402
from DITWorkstation.Services.media_import_service import MediaImportService  # noqa: E402
from DITWorkstation.Services.report_service import ReportService  # noqa: E402


T = TypeVar("T")
DEFAULT_COUNTS = (10_000, 50_000, 100_000)
SEED_BATCH_SIZE = 1_000
PAGE_SIZE = 500


@dataclass
class Measurement:
    name: str
    elapsed_ms: float
    peak_python_memory_mb: float
    peak_process_memory_mb: float
    item_count: int


def _process_memory_mb() -> float:
    """Return the maximum resident set size in MiB on supported platforms."""
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux and most BSD CI environments report KiB.
    return value / (1024 * 1024) if platform.system() == "Darwin" else value / 1024


def _measure(name: str, workload: Callable[[], tuple[T, int]]) -> tuple[T, Measurement]:
    """Measure wall time and Python/process peak memory for one workload."""
    gc.collect()
    tracemalloc.reset_peak()
    before_process = _process_memory_mb()
    started = time.perf_counter()
    result, item_count = workload()
    elapsed_ms = (time.perf_counter() - started) * 1_000
    _current, python_peak = tracemalloc.get_traced_memory()
    return result, Measurement(
        name=name,
        elapsed_ms=round(elapsed_ms, 2),
        peak_python_memory_mb=round(python_peak / (1024 * 1024), 2),
        peak_process_memory_mb=round(max(before_process, _process_memory_mb()), 2),
        item_count=item_count,
    )


def _make_assets(project_id: str, start: int, count: int) -> list[MediaAsset]:
    """Create deterministic metadata spread over nested camera-card-like paths."""
    imported_at = datetime(2026, 1, 1) + timedelta(seconds=start)
    assets = []
    for offset in range(count):
        index = start + offset
        extension = (".mov", ".cr3", ".wav", ".jpg")[index % 4]
        assets.append(MediaAsset(
            asset_id=f"bench-{index:08d}",
            project_id=project_id,
            file_path=(
                f"/synthetic/CARD_{index % 12:02d}/"
                f"DAY_{index % 30 + 1:02d}/REEL_{index // 500:04d}/"
                f"CAM_{chr(65 + index % 3)}_{index:08d}{extension}"
            ),
            file_name=f"CAM_{chr(65 + index % 3)}_{index:08d}{extension}",
            file_size=4_194_304 + (index % 4096),
            file_type=extension,
            asset_type="video" if extension == ".mov" else "raw" if extension == ".cr3" else "image",
            checksum_value=f"{index:016x}",
            scene=f"S{index % 25 + 1:03d}",
            shot=f"{index % 80 + 1:03d}{chr(65 + index % 4)}",
            take=f"{index % 10 + 1:02d}",
            date_imported=imported_at + timedelta(seconds=offset),
            camera_make="Benchmark Camera",
            camera_model=f"CAM-{chr(65 + index % 3)}",
            rating=index % 4,
            tags="benchmark,day" if index % 2 else "benchmark,night",
            notes=f"synthetic benchmark asset {index}",
        ))
    return assets


def _seed_database(db: DatabaseService, asset_count: int) -> tuple[str, Measurement]:
    project, seed_measurement = _measure("seed_assets", lambda: _seed(db, asset_count))
    return project, seed_measurement


def _seed(db: DatabaseService, asset_count: int) -> tuple[str, int]:
    project = db.create_project(name=f"Benchmark {asset_count}")
    for start in range(0, asset_count, SEED_BATCH_SIZE):
        assets = _make_assets(project.project_id, start, min(SEED_BATCH_SIZE, asset_count - start))
        inserted = db.add_media_assets_batch(assets)
        if inserted != len(assets):
            raise RuntimeError(f"expected {len(assets)} inserted assets, got {inserted}")
    return project.project_id, asset_count


def _write_markdown(result: dict, output_path: Path) -> None:
    lines = [
        "# DIT Workstation Database Benchmark",
        "",
        f"- Generated: {result['generated_at']}",
        f"- Python: {result['environment']['python']}",
        f"- Platform: {result['environment']['platform']}",
        f"- Assets: {result['asset_count']}",
        "",
        "| Workload | Items | Time (ms) | Python peak (MiB) | Process peak (MiB) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for metric in result["measurements"]:
        lines.append(
            "| {name} | {item_count} | {elapsed_ms:.2f} | "
            "{peak_python_memory_mb:.2f} | {peak_process_memory_mb:.2f} |".format(**metric)
        )
    lines.extend([
        "",
        "Database workloads use synthetic records. File workloads are included only "
        "when invoked with --file-workloads.",
        "",
    ])
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _run_file_workloads(db: DatabaseService, run_dir: Path, asset_count: int) -> list[Measurement]:
    """Measure import, verification, archive and cancellation on nested sample files."""
    measurements: list[Measurement] = []
    media_root = run_dir / "media-fixture"

    def create_fixture():
        paths = []
        for index in range(asset_count):
            path = media_root / f"CARD_{index % 8:02d}" / f"REEL_{index // 250:04d}" / f"CAM_{index:08d}.cr3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"DIT benchmark {index}\n".encode("ascii"))
            paths.append(str(path))
        return paths, asset_count

    paths, measurement = _measure("create_nested_media_fixture", create_fixture)
    measurements.append(measurement)
    importer = MediaImportService(db_service=db, checksum_service=ChecksumService())
    import_project = db.create_project(name=f"File benchmark {asset_count}")
    _imported, measurement = _measure(
        "media_import_no_metadata", lambda: (
            importer.import_assets(
                import_project.project_id, paths, compute_checksum=False, read_metadata=False,
            ),
            asset_count,
        )
    )
    measurements.append(measurement)

    checksum_service = ChecksumService()
    sample_paths = paths[:min(1_000, len(paths))]

    def verify_sample():
        for path in sample_paths:
            checksum = checksum_service.compute_file_checksum(path, ChecksumAlgorithm.XXHASH64)
            if not checksum_service.verify_file(path, checksum.hash_value, ChecksumAlgorithm.XXHASH64):
                raise RuntimeError(f"integrity verification failed: {path}")
        return None, len(sample_paths)

    _verified, measurement = _measure("integrity_verify_sample", verify_sample)
    measurements.append(measurement)

    archive_path = run_dir / "files.zip"
    _archive, measurement = _measure(
        "archive_with_files", lambda: (
            ArchiveService(db_service=db).archive_project(
                import_project.project_id, str(archive_path), include_files=True,
            ),
            asset_count,
        )
    )
    measurements.append(measurement)

    cancel_project = db.create_project(name=f"Cancellation benchmark {asset_count}")
    cancel_event = threading.Event()
    requested_at: list[float] = []

    def request_cancel(_target, _progress, _message):
        if not requested_at:
            requested_at.append(time.perf_counter())
            cancel_event.set()

    result = importer.import_assets(
        cancel_project.project_id, paths,
        compute_checksum=False, read_metadata=False,
        progress_callback=request_cancel, cancel_check=cancel_event.is_set,
    )
    elapsed = (time.perf_counter() - requested_at[0]) * 1_000 if requested_at else 0.0
    measurements.append(Measurement(
        name="import_cancellation_latency", elapsed_ms=round(elapsed, 2),
        peak_python_memory_mb=0.0, peak_process_memory_mb=_process_memory_mb(),
        item_count=result["imported"],
    ))
    return measurements


def _run_one(asset_count: int, output_dir: Path, keep_database: bool, file_workloads: bool) -> dict:
    run_dir = Path(tempfile.mkdtemp(prefix=f"dit-benchmark-{asset_count}-", dir=output_dir))
    db = DatabaseService(db_path=run_dir / "benchmark.db")
    report_service = ReportService()
    measurements: list[Measurement] = []
    try:
        project_id, seed = _seed_database(db, asset_count)
        measurements.append(seed)

        _project, measurement = _measure(
            "project_open", lambda: (db.get_project(project_id), 1)
        )
        measurements.append(measurement)

        if file_workloads:
            measurements.extend(_run_file_workloads(db, run_dir, asset_count))

        assets, measurement = _measure(
            "get_media_assets_full", lambda: (db.get_media_assets(project_id), asset_count)
        )
        measurements.append(measurement)
        # Release the deliberately materialized result before measuring paged work.
        del assets
        gc.collect()

        first_page, measurement = _measure(
            "search_first_page", lambda: (
                db.search_assets(project_id=project_id, limit=PAGE_SIZE), PAGE_SIZE
            )
        )
        measurements.append(measurement)
        del first_page

        deep_offset = max(0, asset_count // 2)
        deep_page, measurement = _measure(
            "search_deep_page_offset", lambda: (
                db.search_assets(project_id=project_id, limit=PAGE_SIZE, offset=deep_offset), PAGE_SIZE
            )
        )
        measurements.append(measurement)
        del deep_page

        csv_path = run_dir / "assets.csv"
        _csv, measurement = _measure(
            "csv_export_streaming", lambda: (
                report_service.export_assets_csv_iter(
                    db.iter_search_assets(project_id=project_id, batch_size=PAGE_SIZE), csv_path
                ),
                asset_count,
            )
        )
        measurements.append(measurement)

        archive_path = run_dir / "metadata-only.zip"
        _archive, measurement = _measure(
            "archive_metadata_only", lambda: (
                ArchiveService(db_service=db).archive_project(project_id, str(archive_path)), asset_count
            )
        )
        measurements.append(measurement)

        result = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "asset_count": asset_count,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "pid": os.getpid(),
            },
            "measurements": [asdict(metric) for metric in measurements],
        }
        json_path = output_dir / f"benchmark-{asset_count}.json"
        markdown_path = output_dir / f"benchmark-{asset_count}.md"
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_markdown(result, markdown_path)
        print(f"{asset_count:>7,} assets: {json_path}")
        return result
    finally:
        db.close_all()
        if not keep_database:
            shutil.rmtree(run_dir, ignore_errors=True)


def _parse_counts(value: str) -> tuple[int, ...]:
    try:
        counts = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--assets must be comma-separated positive integers") from exc
    if not counts or any(count <= 0 for count in counts):
        raise argparse.ArgumentTypeError("--assets must contain at least one positive integer")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reproducible DIT database benchmarks.")
    parser.add_argument(
        "--assets", type=_parse_counts, default=DEFAULT_COUNTS,
        help="comma-separated asset counts (default: 10000,50000,100000)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "benchmark-results",
        help="directory for JSON and Markdown results",
    )
    parser.add_argument(
        "--keep-database", action="store_true",
        help="keep each generated database, CSV and archive under the output directory",
    )
    parser.add_argument(
        "--file-workloads", action="store_true",
        help="also benchmark nested file creation, import, integrity verification, archive and cancellation",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tracemalloc.start()
    for count in args.assets:
        _run_one(count, args.output_dir, args.keep_database, args.file_workloads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
