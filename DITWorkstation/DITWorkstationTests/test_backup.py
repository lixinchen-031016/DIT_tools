"""备份服务测试 - 对应 TR-3.1, TR-3.2"""
import os
import sys
import tempfile
import shutil
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Models import ChecksumAlgorithm, BackupStatus, CopyStatus, MediaAsset
from DITWorkstation.Utils import normalize_path


class TestBackupService(unittest.TestCase):
    """备份服务测试"""

    def setUp(self):
        self.service = BackupService()
        # 创建临时源目录和文件
        self.temp_dir = tempfile.mkdtemp()
        self.source_dir = os.path.join(self.temp_dir, "source")
        self.target1_dir = os.path.join(self.temp_dir, "target1")
        self.target2_dir = os.path.join(self.temp_dir, "target2")
        os.makedirs(self.source_dir)
        os.makedirs(self.target1_dir)
        os.makedirs(self.target2_dir)

        # 创建测试文件
        for i in range(5):
            file_path = os.path.join(self.source_dir, f"test_file_{i}.dat")
            with open(file_path, "wb") as f:
                f.write(os.urandom(1024 * 100))  # 100KB random data

        # 创建子目录
        sub_dir = os.path.join(self.source_dir, "subfolder")
        os.makedirs(sub_dir)
        with open(os.path.join(sub_dir, "nested_file.dat"), "wb") as f:
            f.write(os.urandom(1024 * 50))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scan_source(self):
        """扫描源目录"""
        files = self.service.scan_source(self.source_dir)
        self.assertEqual(len(files), 6)  # 5 files + 1 nested
        total_size = sum(f["size"] for f in files)
        self.assertGreater(total_size, 0)

    def test_create_backup_job(self):
        """创建备份作业"""
        job = self.service.create_backup_job(
            self.source_dir,
            [self.target1_dir, self.target2_dir]
        )
        self.assertIsNotNone(job.job_id)
        self.assertEqual(len(job.targets), 2)
        self.assertEqual(job.total_files, 6)
        self.assertGreater(job.total_bytes, 0)

    def test_single_target_backup(self):
        """TR-3.1: 单目标备份成功"""
        job = self.service.create_backup_job(
            self.source_dir, [self.target1_dir]
        )
        result = self.service.execute_backup(job)

        self.assertEqual(result.status, BackupStatus.COMPLETED)
        self.assertEqual(result.targets[0].status, CopyStatus.COMPLETED)
        self.assertTrue(result.targets[0].verified)
        self.assertEqual(result.targets[0].completed_files, 6)

        # 验证文件确实被拷贝
        copied_files = list(Path(self.target1_dir).rglob("*"))
        copied_files = [f for f in copied_files if f.is_file()]
        self.assertEqual(len(copied_files), 6)

    def test_multi_target_backup(self):
        """TR-3.1: 同时向2个以上目标位置备份成功"""
        job = self.service.create_backup_job(
            self.source_dir,
            [self.target1_dir, self.target2_dir]
        )
        result = self.service.execute_backup(job)

        self.assertEqual(result.status, BackupStatus.COMPLETED)
        for target in result.targets:
            self.assertEqual(target.status, CopyStatus.COMPLETED)
            self.assertTrue(target.verified)
            self.assertEqual(target.completed_files, 6)

    def test_backup_checksum_verification(self):
        """TR-3.2: 所有备份的校验和验证都通过"""
        job = self.service.create_backup_job(
            self.source_dir,
            [self.target1_dir, self.target2_dir],
            algorithm=ChecksumAlgorithm.XXHASH64
        )
        result = self.service.execute_backup(job)

        # 验证每个目标的文件校验和
        from DITWorkstation.Services.checksum_service import ChecksumService
        checksum_svc = ChecksumService()

        source_files = sorted(Path(self.source_dir).rglob("*"))
        source_files = [f for f in source_files if f.is_file()]

        for target in result.targets:
            self.assertTrue(target.verified)
            for src_file in source_files:
                relative = src_file.relative_to(self.source_dir)
                dest_file = Path(target.path) / relative
                self.assertTrue(dest_file.exists())

                src_checksum = checksum_svc.compute_file_checksum(str(src_file))
                dest_checksum = checksum_svc.compute_file_checksum(str(dest_file))
                self.assertEqual(src_checksum.hash_value, dest_checksum.hash_value)

    def test_backup_progress_callback(self):
        """进度回调正常"""
        progress_data = []

        def callback(target, progress, msg):
            progress_data.append((target, progress, msg))

        job = self.service.create_backup_job(self.source_dir, [self.target1_dir])
        self.service.execute_backup(job, progress_callback=callback)

        self.assertGreater(len(progress_data), 0)

    def test_mhl_report_generation(self):
        """MHL报告生成"""
        job = self.service.create_backup_job(self.source_dir, [self.target1_dir])
        mhl = self.service.generate_mhl_report(job)

        self.assertIn('<?xml version="1.0"', mhl)
        self.assertIn('<hashlist', mhl)
        self.assertIn('<hashvalue>', mhl)
        self.assertIn('xxhash64', mhl)

    def test_backup_continues_after_file_error(self):
        """单文件失败不中断整个目标：其余文件继续拷贝，目标标记 FAILED"""
        service = BackupService()
        checksum_svc = service.checksum_service
        orig = checksum_svc.copy_file_with_checksum

        def fake_copy(src_path, dest_path, algorithm=ChecksumAlgorithm.XXHASH64,
                      progress_callback=None, cancel_check=None):
            # 仅 target1 中名为 test_file_1 的源文件模拟损坏
            if "test_file_1" in str(src_path) and "target1" in str(dest_path):
                raise IOError("模拟源文件损坏")
            return orig(src_path, dest_path, algorithm, progress_callback, cancel_check)

        checksum_svc.copy_file_with_checksum = fake_copy

        job = service.create_backup_job(
            self.source_dir, [self.target1_dir, self.target2_dir]
        )
        result = service.execute_backup(job)

        self.assertEqual(result.status, BackupStatus.PARTIAL)
        failed_target = next(t for t in result.targets if t.status == CopyStatus.FAILED)
        ok_target = next(t for t in result.targets if t.status == CopyStatus.COMPLETED)

        # 失败目标仍完成其余 5 个文件；正常目标完成全部 6 个
        self.assertEqual(failed_target.completed_files, 5)
        self.assertEqual(ok_target.completed_files, 6)
        self.assertIn("test_file_1", failed_target.error_message)

        # 失败文件不残留半成品
        self.assertFalse((Path(self.target1_dir) / "test_file_1.dat").exists())
        # 其余文件已成功拷贝
        self.assertTrue((Path(self.target1_dir) / "test_file_0.dat").exists())
        self.assertTrue((Path(self.target1_dir) / "test_file_2.dat").exists())


# ===== 备份前空间预检 =====

def test_check_target_space_sufficient(tmp_dir):
    """目标剩余空间足够时标记 sufficient=True"""
    service = BackupService()
    target = tmp_dir / "target"
    target.mkdir()
    results = service.check_target_space([str(target)], total_bytes=1024)
    assert len(results) == 1
    assert results[0]["sufficient"] is True
    assert results[0]["free"] > 0
    assert results[0]["error"] == ""


def test_check_target_space_insufficient(tmp_dir):
    """所需空间超过剩余空间时标记 sufficient=False"""
    service = BackupService()
    target = tmp_dir / "target"
    target.mkdir()
    huge = 10 ** 30  # 远超任何磁盘容量
    results = service.check_target_space([str(target)], total_bytes=huge)
    assert len(results) == 1
    assert results[0]["sufficient"] is False
    assert results[0]["needed"] == huge


def test_check_target_space_missing_dir(tmp_dir):
    """目标目录不可达时标记 sufficient=False 且带 error"""
    service = BackupService()
    missing = tmp_dir / "not_exist"
    results = service.check_target_space([str(missing)], total_bytes=1024)
    assert len(results) == 1
    assert results[0]["sufficient"] is False
    assert results[0]["error"]


# ===== 备份完整性独立再校验 =====

def _make_src_files(src_dir, count=3):
    """在 src 目录创建 count 个随机媒体文件，返回文件信息列表"""
    import os
    src_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(count):
        p = src_dir / f"IMG_{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(1024))
        files.append({
            "path": str(p),
            "size": p.stat().st_size,
            "name": p.name,
            "relative": p.name,
        })
    return files


def test_verify_backup_all_matched(db_service, tmp_dir):
    """校验已有备份：全部文件存在且校验和一致 -> matched=总数"""
    from DITWorkstation.Services.checksum_service import ChecksumService

    src = tmp_dir / "source"
    tgt = tmp_dir / "target"
    files = _make_src_files(src)
    project = db_service.create_project(name="校验项目")

    checksum_svc = ChecksumService()
    for f in files:
        cached = checksum_svc.compute_file_checksum(f["path"])
        db_service.add_media_asset(MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path=normalize_path(f["path"]),
            file_name=f["name"],
            file_size=f["size"],
            file_type=".jpg",
            asset_type="image",
            checksum_algorithm=cached.algorithm.value,
            checksum_value=cached.hash_value,
        ))

    service = BackupService(db_service=db_service)
    job = service.create_backup_job(str(src), [str(tgt)])
    service.execute_backup(job, project_id=project.project_id)

    stats = service.verify_backup(project.project_id)
    assert stats["checked"] == 3
    assert stats["matched"] == 3
    assert stats["missing"] == 0
    assert stats["mismatch"] == 0


def test_verify_backup_detects_mismatch(db_service, tmp_dir):
    """校验已有备份：目标文件被篡改 -> mismatch 检出"""
    from DITWorkstation.Services.checksum_service import ChecksumService

    src = tmp_dir / "source"
    tgt = tmp_dir / "target"
    files = _make_src_files(src)
    project = db_service.create_project(name="篡改项目")

    checksum_svc = ChecksumService()
    for f in files:
        cached = checksum_svc.compute_file_checksum(f["path"])
        db_service.add_media_asset(MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path=normalize_path(f["path"]),
            file_name=f["name"],
            file_size=f["size"],
            file_type=".jpg",
            asset_type="image",
            checksum_algorithm=cached.algorithm.value,
            checksum_value=cached.hash_value,
        ))

    service = BackupService(db_service=db_service)
    job = service.create_backup_job(str(src), [str(tgt)])
    service.execute_backup(job, project_id=project.project_id)

    # 篡改 target 中的第一个文件
    corrupted = next(tgt.rglob("*.jpg"))
    corrupted.write_bytes(b"corrupted" * 100)

    stats = service.verify_backup(project.project_id)
    assert stats["mismatch"] == 1
    assert stats["matched"] == 2


def test_verify_backup_detects_missing(db_service, tmp_dir):
    """校验已有备份：目标文件被删除 -> missing 检出"""
    src = tmp_dir / "source"
    tgt = tmp_dir / "target"
    files = _make_src_files(src)
    project = db_service.create_project(name="缺失项目")

    for f in files:
        db_service.add_media_asset(MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path=normalize_path(f["path"]),
            file_name=f["name"],
            file_size=f["size"],
            file_type=".jpg",
            asset_type="image",
            checksum_algorithm="xxhash64",
            checksum_value="deadbeef",
        ))

    service = BackupService(db_service=db_service)
    job = service.create_backup_job(str(src), [str(tgt)])
    service.execute_backup(job, project_id=project.project_id)

    # 删除 target 中的第一个文件
    victim = next(tgt.rglob("*.jpg"))
    victim.unlink()

    stats = service.verify_backup(project.project_id)
    assert stats["missing"] == 1
    assert stats["checked"] == 3


def test_verify_backup_requires_db():
    """未注入 db_service 时抛出 ValueError"""
    import pytest
    service = BackupService(db_service=None)
    with pytest.raises(ValueError):
        service.verify_backup("p1")


def test_backup_destination_uses_persisted_snapshot_when_source_root_unavailable(tmp_dir):
    """源根不可用时优先使用快照 relative，避免递归扫描备份盘。"""
    from DITWorkstation.Services.backup_service import BackupService

    source_file = tmp_dir / "removed-source" / "reel" / "clip.mov"
    target = tmp_dir / "backup"
    asset = MediaAsset(
        asset_id="snapshot-asset", project_id="p1", file_path=str(source_file),
        file_name="clip.mov", file_size=10, file_type=".mov",
    )
    service = BackupService()

    result = service._backup_dest_for_asset(
        asset,
        str(target),
        {},
        {str(target): {str(source_file): "reel/clip.mov"}},
    )

    assert result == str(target / "reel" / "clip.mov")


if __name__ == "__main__":
    unittest.main()
