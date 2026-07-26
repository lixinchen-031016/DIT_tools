"""校验和服务测试 - 对应 TR-2.1, TR-2.2"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Models import ChecksumAlgorithm


class TestChecksumService(unittest.TestCase):
    """校验和服务测试"""

    def setUp(self):
        self.service = ChecksumService()
        # 创建临时测试文件
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test_file.bin")
        # 写入已知内容
        with open(self.test_file, "wb") as f:
            f.write(b"Hello DIT Workstation Test Data 1234567890" * 1000)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_xxhash64_checksum(self):
        """TR-2.1: XXHash64校验和计算"""
        checksum = self.service.compute_file_checksum(
            self.test_file, ChecksumAlgorithm.XXHASH64
        )
        self.assertIsNotNone(checksum.hash_value)
        self.assertEqual(len(checksum.hash_value), 16)  # xxhash64 hex length
        self.assertEqual(checksum.algorithm, ChecksumAlgorithm.XXHASH64)
        self.assertGreater(checksum.file_size, 0)

    def test_md5_checksum(self):
        """TR-2.1: MD5校验和计算"""
        checksum = self.service.compute_file_checksum(
            self.test_file, ChecksumAlgorithm.MD5
        )
        self.assertIsNotNone(checksum.hash_value)
        self.assertEqual(len(checksum.hash_value), 32)  # MD5 hex length
        self.assertEqual(checksum.algorithm, ChecksumAlgorithm.MD5)

    def test_checksum_consistency(self):
        """TR-2.1: 同一文件多次计算结果一致"""
        checksum1 = self.service.compute_file_checksum(self.test_file)
        checksum2 = self.service.compute_file_checksum(self.test_file)
        self.assertEqual(checksum1.hash_value, checksum2.hash_value)

    def test_verify_file_success(self):
        """TR-2.1: 文件验证通过"""
        checksum = self.service.compute_file_checksum(self.test_file)
        result = self.service.verify_file(
            self.test_file, checksum.hash_value, checksum.algorithm
        )
        self.assertTrue(result)

    def test_verify_file_corruption(self):
        """TR-2.2: 文件损坏时正确检测"""
        checksum = self.service.compute_file_checksum(self.test_file)

        # 修改文件内容（模拟损坏）
        with open(self.test_file, "r+b") as f:
            f.seek(10)
            f.write(b"CORRUPTED")

        result = self.service.verify_file(
            self.test_file, checksum.hash_value, checksum.algorithm
        )
        self.assertFalse(result)

    def test_file_not_found(self):
        """TR-2.2: 文件不存在时抛出异常"""
        with self.assertRaises(FileNotFoundError):
            self.service.compute_file_checksum("/nonexistent/path/file.bin")

    def test_progress_callback(self):
        """进度回调正常工作"""
        progress_values = []

        def callback(p):
            progress_values.append(p)

        self.service.compute_file_checksum(
            self.test_file, progress_callback=callback
        )
        self.assertGreater(len(progress_values), 0)
        self.assertLessEqual(progress_values[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
