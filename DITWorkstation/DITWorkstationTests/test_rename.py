"""重命名服务测试 - 对应 TR-4.1, TR-4.2"""
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.rename_service import RenameService
from DITWorkstation.Services.metadata_service import MetadataService
from DITWorkstation.Models import RenameRule


class TestRenameService(unittest.TestCase):
    """重命名服务测试"""

    def setUp(self):
        self.service = RenameService()
        self.temp_dir = tempfile.mkdtemp()

        # 创建测试文件
        self.test_files = []
        for i in range(5):
            file_path = os.path.join(self.temp_dir, f"DSC_{1000 + i}.jpg")
            with open(file_path, "wb") as f:
                f.write(os.urandom(1024))
            self.test_files.append(file_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_preview_rename(self):
        """预览重命名"""
        rule = RenameRule(
            pattern="{scene}_{shot}_{take}_{number}",
            scene="S001",
            shot="001A",
            take="01",
            start_number=1,
            padding=3
        )
        pairs = self.service.preview_rename(self.test_files, rule)

        self.assertEqual(len(pairs), 5)
        # 检查第一个文件的新名称
        new_name = Path(pairs[0][1]).name
        self.assertIn("S001", new_name)
        self.assertIn("001A", new_name)
        self.assertIn("001", new_name)

    def test_execute_rename(self):
        """TR-4.1: 应用命名规则后文件正确重命名"""
        rule = RenameRule(
            pattern="{scene}_{shot}_{number}",
            scene="S002",
            shot="003B",
            start_number=1,
            padding=4
        )
        results = self.service.execute_rename(self.test_files, rule)

        self.assertEqual(len(results), 5)

        # 验证文件确实被重命名
        for old_path, new_path in results:
            self.assertFalse(Path(old_path).exists())
            self.assertTrue(Path(new_path).exists())
            self.assertIn("S002", Path(new_path).name)
            self.assertIn("003B", Path(new_path).name)

    def test_rename_preserves_extension(self):
        """重命名保持文件扩展名"""
        rule = RenameRule(pattern="{scene}_{number}", scene="S001")
        pairs = self.service.preview_rename(self.test_files, rule)

        for _, new_path in pairs:
            self.assertEqual(Path(new_path).suffix, ".jpg")

    def test_rename_avoids_overwrite(self):
        """重命名避免覆盖已有文件"""
        # 创建一个目标名称已存在的文件
        rule = RenameRule(pattern="{scene}_{number}", scene="S001", start_number=1)
        pairs = self.service.preview_rename(self.test_files[:1], rule)
        target_name = Path(pairs[0][1]).name

        # 先创建同名文件
        with open(os.path.join(self.temp_dir, target_name), "wb") as f:
            f.write(b"existing")

        # 执行重命名不应覆盖
        results = self.service.execute_rename(self.test_files[:1], rule)
        self.assertEqual(len(results), 1)
        # 新文件应该有后缀避免冲突
        new_path = results[0][1]
        self.assertTrue(Path(new_path).exists())

    def test_custom_pattern_variables(self):
        """自定义模板变量"""
        rule = RenameRule(
            pattern="{prefix}_{date}_{scene}_{number}",
            scene="A001",
            prefix="CAM_A",
            start_number=10,
            padding=4
        )
        pairs = self.service.preview_rename(self.test_files[:1], rule)
        new_name = Path(pairs[0][1]).name

        self.assertIn("CAM_A", new_name)
        self.assertIn("A001", new_name)
        self.assertIn("0010", new_name)


class TestMetadataService(unittest.TestCase):
    """元数据服务测试"""

    def setUp(self):
        self.service = MetadataService()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_read_basic_metadata(self):
        """TR-4.2: 读取基本文件元数据"""
        file_path = os.path.join(self.temp_dir, "test_file.cr2")
        with open(file_path, "wb") as f:
            f.write(os.urandom(2048))

        metadata = self.service.read_metadata(file_path)

        self.assertEqual(metadata.file_name, "test_file.cr2")
        self.assertEqual(metadata.file_type, ".cr2")
        self.assertEqual(metadata.file_size, 2048)
        self.assertEqual(metadata.file_path, file_path)

    def test_batch_read_metadata(self):
        """批量读取元数据"""
        files = []
        for i in range(3):
            fp = os.path.join(self.temp_dir, f"file_{i}.nef")
            with open(fp, "wb") as f:
                f.write(os.urandom(1024 * (i + 1)))
            files.append(fp)

        results = self.service.batch_read_metadata(files)
        self.assertEqual(len(results), 3)
        for i, meta in enumerate(results):
            self.assertEqual(meta.file_size, 1024 * (i + 1))


if __name__ == "__main__":
    unittest.main()
