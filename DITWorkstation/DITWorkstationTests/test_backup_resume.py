"""备份断点续传与失败重试测试"""
import os
import sqlite3

from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Models import BackupStatus, CopyStatus


def _make_sources(tmp_path, count=3, size=10000):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(count):
        (src / f"f{i}.dat").write_bytes(os.urandom(size))
    return src


def test_resume_skips_existing_files(tmp_path):
    """目标已存在且校验一致的文件在重新备份时跳过（mtime 不变）"""
    src = _make_sources(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    svc = BackupService(checksum_service=ChecksumService())

    job = svc.create_backup_job(str(src), [str(dst)])
    result = svc.execute_backup(job)
    assert result.status == BackupStatus.COMPLETED

    mtimes = {p.name: p.stat().st_mtime_ns for p in dst.iterdir() if p.is_file()}
    assert len(mtimes) == 3

    # 再次执行同一备份：文件应被跳过而非重拷
    job2 = svc.create_backup_job(str(src), [str(dst)])
    result2 = svc.execute_backup(job2)
    assert result2.status == BackupStatus.COMPLETED
    for p in dst.iterdir():
        if p.is_file():
            assert p.stat().st_mtime_ns == mtimes[p.name]


def test_resume_repairs_corrupted_target(tmp_path):
    """目标存在但内容不一致（同大小）时重新拷贝修复"""
    src = _make_sources(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    # 预置一个大小相同但内容全零的损坏文件
    (dst / "f0.dat").write_bytes(b"\x00" * 10000)

    svc = BackupService(checksum_service=ChecksumService())
    job = svc.create_backup_job(str(src), [str(dst)])
    result = svc.execute_backup(job)
    assert result.status == BackupStatus.COMPLETED
    assert (dst / "f0.dat").read_bytes() == (src / "f0.dat").read_bytes()


def _backup_with_one_failure(tmp_path, monkeypatch):
    """构造「f1.dat 拷贝失败」的备份并返回 (svc, db, job, dst, original_copy)"""
    src = _make_sources(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    db = DatabaseService(db_path=tmp_path / "test.db")
    svc = BackupService(db_service=db, checksum_service=ChecksumService())
    original = svc.checksum_service.copy_file_with_checksum

    def failing_copy(src_path, dest_path, algorithm=None,
                     progress_callback=None, **kwargs):
        if "f1.dat" in str(src_path):
            raise IOError("模拟拷贝失败")
        return original(src_path, dest_path, algorithm=algorithm,
                        progress_callback=progress_callback, **kwargs)

    monkeypatch.setattr(svc.checksum_service, "copy_file_with_checksum", failing_copy)
    job = svc.create_backup_job(str(src), [str(dst)])
    result = svc.execute_backup(job, project_id="proj1")
    assert result.status == BackupStatus.FAILED
    assert result.targets[0].status == CopyStatus.FAILED
    return svc, db, job, dst, original


def test_failed_files_collected_and_persisted(tmp_path, monkeypatch):
    """失败文件写入 BackupTarget.failed_files 并持久化到 DB"""
    svc, db, job, dst, _ = _backup_with_one_failure(tmp_path, monkeypatch)
    target = job.targets[0]
    assert "f1.dat" in target.failed_files
    assert "f0.dat" not in target.failed_files

    # DB 持久化
    raw = db.get_backup_job(job.job_id)
    assert raw is not None
    assert raw["status"] == "failed"
    assert "f1.dat" in raw["targets"][0]["failed_files"]
    assert "f1.dat" in raw["failed_files_by_target"][str(dst)]

    # load_job 恢复
    loaded = svc.load_job(job.job_id)
    assert loaded is not None
    assert loaded.targets[0].failed_files == ["f1.dat"]
    assert loaded.targets[0].status == CopyStatus.FAILED


def test_retry_failed_files_only_retries_failures(tmp_path, monkeypatch):
    """重试只拷贝失败文件，且成功后状态更新为 completed"""
    svc, db, job, dst, original = _backup_with_one_failure(tmp_path, monkeypatch)
    # 解除失败模拟
    monkeypatch.setattr(
        svc.checksum_service,
        "copy_file_with_checksum",
        original,
    )

    # 记录成功文件的 mtime，验证重试时不会被重拷
    m0 = (dst / "f0.dat").stat().st_mtime_ns
    m2 = (dst / "f2.dat").stat().st_mtime_ns

    result = svc.retry_failed_files(job.job_id, project_id="proj1")
    assert result is not None
    assert result.status == BackupStatus.COMPLETED
    assert result.targets[0].failed_files == []
    assert (dst / "f1.dat").exists()
    assert (dst / "f0.dat").stat().st_mtime_ns == m0
    assert (dst / "f2.dat").stat().st_mtime_ns == m2

    raw = db.get_backup_job(job.job_id)
    assert raw["status"] == "completed"
    assert raw["targets"][0]["failed_files"] == []


def test_retry_missing_job_returns_none(tmp_path):
    """重试不存在的作业返回 None"""
    db = DatabaseService(db_path=tmp_path / "test.db")
    svc = BackupService(db_service=db, checksum_service=ChecksumService())
    assert svc.retry_failed_files("nope") is None


def test_retry_without_failures_is_noop(tmp_path):
    """作业无失败文件时重试直接返回原作业"""
    src = _make_sources(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    db = DatabaseService(db_path=tmp_path / "test.db")
    svc = BackupService(db_service=db, checksum_service=ChecksumService())
    job = svc.create_backup_job(str(src), [str(dst)])
    svc.execute_backup(job, project_id="proj1")
    result = svc.retry_failed_files(job.job_id)
    assert result is not None
    assert result.status == BackupStatus.COMPLETED


def test_migration_adds_failed_files_column(tmp_path):
    """旧数据库自动补齐 failed_files_json 列"""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE backup_jobs (
            job_id TEXT PRIMARY KEY,
            project_id TEXT,
            source_path TEXT NOT NULL,
            algorithm TEXT DEFAULT 'xxhash64',
            total_files INTEGER DEFAULT 0,
            total_bytes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'idle',
            targets_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
    """)
    conn.commit()
    conn.close()

    db = DatabaseService(db_path=db_path)
    with db._connection() as conn:
        cols = [r["name"] for r in
                conn.execute("PRAGMA table_info(backup_jobs)").fetchall()]
    assert "failed_files_json" in cols


def test_backup_history_ui_shows_failed_files(tmp_path, monkeypatch):
    """备份历史表展示失败作业，选中后显示失败文件并启用重试按钮"""
    svc, db, job, dst, _ = _backup_with_one_failure(tmp_path, monkeypatch)

    from DITWorkstation.Views.backup_view import BackupView
    view = BackupView(db_service=db)
    view.show()
    view._load_backup_history()
    assert view.history_table.rowCount() == 1

    view.history_table.selectRow(0)
    assert "f1.dat" in view.history_failed_label.text()
    assert view.retry_btn.isEnabled()
