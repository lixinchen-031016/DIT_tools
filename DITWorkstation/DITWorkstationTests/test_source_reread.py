"""源卡复读校验（verify_source_reread）测试。"""

import tempfile
import unittest
from pathlib import Path

from DITWorkstation.App import config
from DITWorkstation.Services.backup_service import BackupService


class TestSourceReread(unittest.TestCase):
    def setUp(self):
        self._orig_flag = getattr(config, "verify_source_reread", False)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "card"
        self.source.mkdir()
        (self.source / "a.txt").write_bytes(b"alpha content" * 20)
        (self.source / "b.txt").write_bytes(b"beta content" * 30)
        self.target = self.root / "backup"
        self.target.mkdir()
        config.verify_source_reread = True

    def tearDown(self):
        config.verify_source_reread = self._orig_flag
        self._tmp.cleanup()

    def test_reread_passes_on_stable_source(self):
        """源内容未变：复读一致，mismatches 为空"""
        service = BackupService()
        job = service.create_backup_job(str(self.source), [str(self.target)])
        result = service.execute_backup(job)
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(getattr(result, "source_reread_mismatches", None), [])

    def test_reread_detects_source_change(self):
        """拷贝后源文件被篡改：复读应发现该文件"""
        service = BackupService()
        job = service.create_backup_job(str(self.source), [str(self.target)])
        # 模拟拷贝完成后源卡内容变化：让复读读到不同内容
        orig_fresh = service.checksum_service.compute_file_checksum_fresh
        tampered = {"a.txt"}

        def fake_fresh(file_path, algorithm, cancel_check=None):
            record = orig_fresh(file_path, algorithm, cancel_check)
            from DITWorkstation.Models import FileChecksum

            if Path(file_path).name in tampered:
                return FileChecksum(
                    file_path=record.file_path,
                    algorithm=record.algorithm,
                    hash_value="0" * 16,  # 模拟复读结果不同
                    file_size=record.file_size,
                    mtime_ns=record.mtime_ns,
                )
            return record

        service.checksum_service.compute_file_checksum_fresh = fake_fresh
        result = service.execute_backup(job)
        self.assertEqual(result.status.value, "completed")  # 备份本身不受影响
        self.assertEqual(result.source_reread_mismatches, ["a.txt"])

    def test_reread_skipped_when_disabled(self):
        """开关关闭：不执行复读，无 mismatches 属性写入"""
        config.verify_source_reread = False
        service = BackupService()
        job = service.create_backup_job(str(self.source), [str(self.target)])
        result = service.execute_backup(job)
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(getattr(result, "source_reread_mismatches", None), [])


if __name__ == "__main__":
    unittest.main()
