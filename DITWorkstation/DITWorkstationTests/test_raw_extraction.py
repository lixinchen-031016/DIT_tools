"""RAW提取服务测试 - 对应 TR-5.1, TR-5.2"""
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.raw_extraction_service import RawExtractionService


class TestRawExtractionService(unittest.TestCase):
    """RAW提取服务测试"""

    def setUp(self):
        self.service = RawExtractionService()
        self.temp_dir = tempfile.mkdtemp()

        # 创建JPG文件夹（模拟客户筛选后的结果）
        self.jpg_dir = os.path.join(self.temp_dir, "jpg_selected")
        os.makedirs(self.jpg_dir)

        # 创建RAW源文件夹（模拟存储卡原始文件）
        self.raw_dir = os.path.join(self.temp_dir, "raw_source")
        os.makedirs(self.raw_dir)

        # 输出文件夹
        self.output_dir = os.path.join(self.temp_dir, "output")

        # 创建测试文件：JPG和对应的RAW
        self.test_files = [
            ("IMG_001", ".jpg", ".cr2"),
            ("IMG_002", ".jpg", ".cr2"),
            ("IMG_003", ".jpg", ".nef"),
            ("IMG_004", ".jpg", ".arw"),
            ("IMG_005", ".jpg", ".dng"),
        ]

        for stem, jpg_ext, raw_ext in self.test_files:
            # 创建JPG文件（筛选后的）
            jpg_path = os.path.join(self.jpg_dir, f"{stem}{jpg_ext}")
            with open(jpg_path, "wb") as f:
                f.write(os.urandom(1024 * 10))

            # 创建RAW文件（源）
            raw_path = os.path.join(self.raw_dir, f"{stem}{raw_ext}")
            with open(raw_path, "wb") as f:
                f.write(os.urandom(1024 * 50))

        # 额外创建一个没有对应JPG的RAW文件
        with open(os.path.join(self.raw_dir, "IMG_999.cr2"), "wb") as f:
            f.write(os.urandom(1024 * 50))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scan_jpg_folder(self):
        """扫描JPG文件夹"""
        jpg_files = self.service.scan_jpg_folder(self.jpg_dir)
        self.assertEqual(len(jpg_files), 5)

    def test_scan_raw_folder(self):
        """扫描RAW文件夹建立索引"""
        raw_index = self.service.scan_raw_folder(self.raw_dir)
        self.assertEqual(len(raw_index), 6)  # 5 matching + 1 extra
        self.assertIn("img_001", raw_index)
        self.assertIn("img_999", raw_index)

    def test_match_raw_files(self):
        """TR-5.1: 正确匹配JPG对应RAW"""
        jpg_files = self.service.scan_jpg_folder(self.jpg_dir)
        raw_index = self.service.scan_raw_folder(self.raw_dir)
        matches = self.service.match_raw_files(jpg_files, raw_index)

        self.assertEqual(len(matches), 5)
        matched_count = sum(1 for _, raw in matches if raw is not None)
        self.assertEqual(matched_count, 5)

    def test_extract_raw_files(self):
        """TR-5.1: 选择JPG文件夹后正确提取对应RAW"""
        result = self.service.extract_raw_files(
            self.jpg_dir, self.raw_dir, self.output_dir
        )

        self.assertEqual(result["total_jpg"], 5)
        self.assertEqual(result["extracted"], 5)
        self.assertEqual(result["not_found"], 0)
        self.assertEqual(result["failed"], 0)

        # 验证输出文件存在
        output_files = list(Path(self.output_dir).iterdir())
        self.assertEqual(len(output_files), 5)

    def test_multiple_raw_formats(self):
        """TR-5.2: 支持多种RAW格式"""
        result = self.service.extract_raw_files(
            self.jpg_dir, self.raw_dir, self.output_dir
        )

        # 验证各种格式都被提取
        output_files = {f.suffix.lower() for f in Path(self.output_dir).iterdir()}
        self.assertIn(".cr2", output_files)
        self.assertIn(".nef", output_files)
        self.assertIn(".arw", output_files)
        self.assertIn(".dng", output_files)

    def test_missing_raw_file(self):
        """JPG没有对应RAW时正确报告"""
        # 添加一个没有RAW对应的JPG
        with open(os.path.join(self.jpg_dir, "IMG_NO_RAW.jpg"), "wb") as f:
            f.write(os.urandom(1024))

        result = self.service.extract_raw_files(
            self.jpg_dir, self.raw_dir, self.output_dir
        )

        self.assertEqual(result["total_jpg"], 6)
        self.assertEqual(result["extracted"], 5)
        self.assertEqual(result["not_found"], 1)

    def test_checksum_verification(self):
        """提取后校验和验证"""
        result = self.service.extract_raw_files(
            self.jpg_dir, self.raw_dir, self.output_dir, verify=True
        )
        self.assertEqual(result["failed"], 0)

        # 手动验证一个文件
        from DITWorkstation.Services.checksum_service import ChecksumService
        checksum_svc = ChecksumService()

        src_raw = os.path.join(self.raw_dir, "IMG_001.cr2")
        dest_raw = os.path.join(self.output_dir, "IMG_001.cr2")

        src_hash = checksum_svc.compute_file_checksum(src_raw)
        dest_hash = checksum_svc.compute_file_checksum(dest_raw)
        self.assertEqual(src_hash.hash_value, dest_hash.hash_value)


if __name__ == "__main__":
    unittest.main()
