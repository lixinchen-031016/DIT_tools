"""Utils 层纯函数单元测试 - Phase 1.5"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Utils.common import (
    format_size, sanitize_filename, get_file_extension,
    get_file_stem, calculate_speed, generate_timestamp, generate_log_message,
    normalize_path, find_overwrite_conflicts,
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


class TestNormalizePath(unittest.TestCase):
    """normalize_path 路径规范化测试

    回归：DB 入库时 file_path 必须与查询时的表示形式一致，
    否则在符号链接 / 相对路径场景下会匹配失败。
    """

    def test_empty_string(self):
        """空字符串原样返回"""
        self.assertEqual(normalize_path(""), "")

    def test_relative_path_resolved_to_absolute(self):
        """相对路径解析为绝对路径"""
        with tempfile.TemporaryDirectory() as tmp:
            # 在 tmp 下创建一个文件，用相对路径引用
            fp = Path(tmp) / "img.cr2"
            fp.write_bytes(b"x")
            # 切到 tmp 目录，用相对路径引用
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                result = normalize_path("img.cr2")
                # 应解析为 tmp 下的绝对路径
                self.assertEqual(Path(result), fp.resolve())
                self.assertTrue(Path(result).is_absolute())
            finally:
                os.chdir(old_cwd)

    def test_dotdot_collapsed(self):
        """含 .. 的路径被折叠"""
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            fp = sub / "img.cr2"
            fp.write_bytes(b"x")
            # tmp/sub/../sub/img.cr2 应折叠为 tmp/sub/img.cr2
            messy = str(Path(tmp) / "sub" / ".." / "sub" / "img.cr2")
            result = normalize_path(messy)
            self.assertEqual(Path(result), fp.resolve())

    def test_symlink_resolved(self):
        """符号链接被解析到真实路径（macOS /var -> /private/var 场景）"""
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            fp = real / "img.cr2"
            fp.write_bytes(b"x")
            link = Path(tmp) / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest("当前环境不支持创建符号链接")
            # 通过符号链接引用文件，应解析到真实路径
            via_link = str(link / "img.cr2")
            result = normalize_path(via_link)
            self.assertEqual(Path(result), fp.resolve())
            # 结果不应包含 link 路径
            self.assertNotIn("link", result)

    def test_nonexistent_path_returns_resolved_form(self):
        """不存在的路径仍返回 resolve 后的形式（不抛异常）"""
        result = normalize_path("/definitely/not/exist/img.cr2")
        # 应是绝对路径
        self.assertTrue(Path(result).is_absolute())
        # 不抛异常
        self.assertIsInstance(result, str)

    def test_idempotent(self):
        """对已规范化的路径再次规范化应无变化"""
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "img.cr2"
            fp.write_bytes(b"x")
            once = normalize_path(str(fp))
            twice = normalize_path(once)
            self.assertEqual(once, twice)


class TestFindOverwriteConflicts(unittest.TestCase):
    """find_overwrite_conflicts 跨平台冲突检测"""

    def test_case_insensitive_conflict(self):
        """大小写不同但同名（Windows/APFS 不区分大小写）应判定为冲突"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            # 目标盘已有小写扩展名文件，源文件为大写扩展名
            (target / "IMG_001.cr2").write_bytes(b"x")
            conflicts = find_overwrite_conflicts(
                ["/src/IMG_001.CR2"], [str(target)]
            )
            self.assertEqual(conflicts[str(target)], ["IMG_001.CR2"])

    def test_no_conflict(self):
        """文件名完全不同的文件不应判定为冲突"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            (target / "other.CR2").write_bytes(b"x")
            conflicts = find_overwrite_conflicts(
                ["/src/IMG_001.CR2"], [str(target)]
            )
            self.assertEqual(conflicts, {})


if __name__ == "__main__":
    unittest.main()
