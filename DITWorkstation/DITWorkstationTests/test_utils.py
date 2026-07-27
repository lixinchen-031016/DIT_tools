"""Utils 层纯函数单元测试 - Phase 1.5"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Utils.common import (
    format_size, sanitize_filename, get_file_extension,
    get_file_stem, calculate_speed, generate_timestamp, generate_log_message,
)


class TestFormatSize(unittest.TestCase):
    """format_size 边界测试"""

    def test_bytes(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(512), "512 B")
        self.assertEqual(format_size(1023), "1023 B")

    def test_kb(self):
        self.assertEqual(format_size(1024), "1.0 KB")
        self.assertEqual(format_size(2048), "2.0 KB")

    def test_mb(self):
        self.assertEqual(format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(format_size(10 * 1024 * 1024), "10.0 MB")

    def test_gb(self):
        self.assertEqual(format_size(1024 ** 3), "1.00 GB")
        self.assertEqual(format_size(2.5 * 1024 ** 3), "2.50 GB")


class TestSanitizeFilename(unittest.TestCase):
    """sanitize_filename 非法字符清理"""

    def test_clean(self):
        self.assertEqual(sanitize_filename("normal_file.txt"), "normal_file.txt")

    def test_illegal_chars(self):
        # 所有 Windows 非法字符都应被替换为 _
        result = sanitize_filename('a\\b/c:d*e?"f<g>h|i')
        self.assertNotIn("\\", result)
        self.assertNotIn("/", result)
        self.assertNotIn(":", result)
        self.assertNotIn("*", result)
        self.assertNotIn("?", result)
        self.assertNotIn('"', result)
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertNotIn("|", result)

    def test_multiple_illegal(self):
        self.assertEqual(sanitize_filename("a:b:c"), "a_b_c")


class TestFileExtension(unittest.TestCase):
    """get_file_extension / get_file_stem"""

    def test_extension_with_dot(self):
        self.assertEqual(get_file_extension("/path/IMG.CR2"), ".cr2")
        self.assertEqual(get_file_extension("file.tar.gz"), ".gz")

    def test_extension_without_dot(self):
        self.assertEqual(get_file_extension("/path/IMG.CR2", include_dot=False), "cr2")

    def test_no_extension(self):
        self.assertEqual(get_file_extension("/path/README"), "")

    def test_stem(self):
        self.assertEqual(get_file_stem("/path/IMG_001.cr2"), "IMG_001")
        self.assertEqual(get_file_stem("archive.tar.gz"), "archive.tar")


class TestCalculateSpeed(unittest.TestCase):
    """calculate_speed 边界"""

    def test_zero_time(self):
        self.assertEqual(calculate_speed(0, 1024), 0.0)

    def test_negative_time(self):
        self.assertEqual(calculate_speed(-1, 1024), 0.0)

    def test_normal(self):
        # 1MB in 1s = 1 MB/s
        speed = calculate_speed(1.0, 1024 * 1024)
        self.assertAlmostEqual(speed, 1.0, places=2)


class TestTimestampHelpers(unittest.TestCase):
    """generate_timestamp / generate_log_message 格式"""

    def test_timestamp_format(self):
        ts = generate_timestamp()
        # YYYYMMDD_HHMMSS
        self.assertEqual(len(ts), 15)
        self.assertEqual(ts[8], "_")

    def test_log_message_format(self):
        msg = generate_log_message("hello")
        # [HH:MM:SS] hello
        self.assertTrue(msg.startswith("["))
        self.assertIn("] hello", msg)
        # 时间戳部分 [HH:MM:SS] 共 10 字符
        self.assertEqual(len(msg.split("] ")[0] + "]"), 10)


if __name__ == "__main__":
    unittest.main()
