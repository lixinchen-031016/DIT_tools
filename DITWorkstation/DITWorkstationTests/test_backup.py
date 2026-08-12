"""备份服务测试 - 对应 TR-3.1, TR-3.2"""
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Models import ChecksumAlgorithm, BackupStatus, CopyStatus


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


if __name__ == "__main__":
    unittest.main()
