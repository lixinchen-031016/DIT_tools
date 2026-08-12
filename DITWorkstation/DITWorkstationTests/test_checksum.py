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

    def test_cache_invalidated_by_content_change_same_size(self):
        """同尺寸内容变更必须重新计算（mtime 参与缓存键，修复过期哈希）"""
        path = Path(self.test_file)
        original_size = path.stat().st_size
        h1 = self.service.compute_file_checksum(str(path)).hash_value

        # 覆盖为同尺寸但不同内容：旧实现（仅按 size 判缓存）会错误命中 h1
        with open(path, "wb") as f:
            f.write(b"X" * original_size)
        # 确保 mtime 有变化（部分文件系统纳秒精度可能相同）
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        h2 = self.service.compute_file_checksum(str(path)).hash_value
        self.assertNotEqual(h1, h2)

    def test_cache_recompute_after_mtime_bump_content_same(self):
        """仅修改时间（内容不变）时重新计算但哈希值一致"""
        path = Path(self.test_file)
        h1 = self.service.compute_file_checksum(str(path)).hash_value
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        h2 = self.service.compute_file_checksum(str(path)).hash_value
        self.assertEqual(h1, h2)

    def test_cancel_check_raises_interrupted(self):
        """取消回调返回 True 时中断哈希计算"""
        service = ChecksumService(buffer_size=1024)
        with self.assertRaises(InterruptedError):
            service.compute_file_checksum(
                self.test_file, cancel_check=lambda: True
            )

    def test_copy_file_with_checksum(self):
        """边拷贝边哈希：目标内容一致且哈希等于源文件校验和"""
        dest = os.path.join(self.temp_dir, "copy.bin")
        result = self.service.copy_file_with_checksum(self.test_file, dest)

        self.assertEqual(
            result.hash_value,
            self.service.compute_file_checksum(self.test_file).hash_value
        )
        with open(self.test_file, "rb") as a, open(dest, "rb") as b:
            self.assertEqual(a.read(), b.read())


if __name__ == "__main__":
    unittest.main()
