"""缩略图服务测试

只测纯 Pillow 生成路径（_gen_image / _generate），不依赖 Qt 事件循环与 ffmpeg，
保证在无 GUI 环境的 CI 中可运行。
"""
import os
import sys
import tempfile
import shutil
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.App import config
from DITWorkstation.Services.thumbnail_service import ThumbnailService


class TestThumbnailService(unittest.TestCase):
    """缩略图服务测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # 重定向缓存目录到临时目录，避免污染仓库 data/ 目录
        self._orig_db_dir = config.db_dir
        config.db_dir = Path(self.temp_dir) / "appdata"
        self.service = ThumbnailService()

    def tearDown(self):
        config.db_dir = self._orig_db_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_jpeg(self, name="sample.jpg"):
        from PIL import Image
        path = Path(self.temp_dir) / name
        Image.new("RGB", (64, 64), color=(255, 0, 0)).save(path, format="JPEG")
        return path

    def test_gen_image_returns_png_bytes(self):
        """图片缩略图生成返回 PNG bytes"""
        path = self._make_jpeg()
        data = self.service._gen_image(str(path), 32)
        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_generate_image_roundtrip(self):
        """_generate 对图片类型返回非空 PNG"""
        path = self._make_jpeg()
        data = self.service._generate(str(path), "image", 32)
        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_generate_missing_file_returns_none(self):
        """文件不存在时返回 None（调用方显示占位图）"""
        data = self.service._generate(
            str(Path(self.temp_dir) / "nope.jpg"), "image", 64
        )
        self.assertIsNone(data)

    def test_cache_size_and_clear(self):
        """cache_size 反映缓存占用，clear_cache 清空缓存文件"""
        path = self._make_jpeg()
        data = self.service._generate(str(path), "image", 64)
        self.assertIsNotNone(data)
        # 手动写入一个缓存文件（模拟异步生成完成后的落盘）
        cache_file = self.service.cache_dir / "test_key_64.png"
        cache_file.write_bytes(data)

        self.assertGreater(self.service.cache_size(), 0)
        removed = self.service.clear_cache()
        self.assertEqual(removed, 1)
        self.assertEqual(self.service.cache_size(), 0)
        self.assertFalse(cache_file.exists())

    def _make_fake_raw(self, name="fake.arw"):
        """构造 Pillow 无法解码的伪 RAW 文件（模拟相机 RAW 容器）"""
        path = Path(self.temp_dir) / name
        path.write_bytes(b"\x00\x01\x02not-a-real-raw" * 64)
        return path

    def test_embedded_preview_fallback(self):
        """Pillow 无法解码的 RAW 回退到 EXIF 内嵌 JPEG 预览"""
        import io
        from PIL import Image

        raw_path = self._make_fake_raw()
        # 用 Pillow 生成一张真实 JPEG 模拟相机内嵌预览
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color=(0, 128, 255)).save(buf, format="JPEG")

        with mock.patch(
            "exifread.process_file",
            return_value={"JPEGThumbnail": buf.getvalue()},
        ):
            data = self.service._gen_image(str(raw_path), 32)

        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_raw_without_preview_returns_none(self):
        """RAW 既无内嵌预览也未安装 rawpy 时返回 None（不抛异常）"""
        raw_path = self._make_fake_raw()
        # 显式把 rawpy 标记为"未安装"：rawpy 装上后真实 import 会引入
        # numpy，在 macOS 上 PySide6 退出阶段偶发 SIGSEGV（测试已通过但
        # 进程退出码 139，会中止打包脚本）；本测试场景本就是 rawpy 缺失
        with mock.patch.dict(sys.modules, {"rawpy": None}), \
             mock.patch("exifread.process_file", return_value={}):
            data = self.service._gen_image(str(raw_path), 32)
        self.assertIsNone(data)

    def _make_fake_video(self, name="fake.mp4"):
        """构造无法直接解码的伪视频文件（模拟无 ffmpeg 场景）"""
        path = Path(self.temp_dir) / name
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42not-a-real-video" * 64)
        return path

    def _png_bytes(self):
        from PIL import Image
        return self.service._to_png(Image.new("RGB", (32, 32), color=(1, 2, 3)), 32)

    def test_gen_video_chain_uses_available_fallback(self):
        """ffmpeg 缺失时回退链能使用下一个可用抽帧方案（PyAV）"""
        video = self._make_fake_video()
        png = self._png_bytes()
        with mock.patch.object(self.service, "_ffmpeg_frame", return_value=None), \
             mock.patch.object(self.service, "_qlmanage_frame", return_value=None), \
             mock.patch.object(self.service, "_av_frame", return_value=png):
            data = self.service._gen_video(str(video), 64)
        self.assertEqual(data, png)

    def test_gen_video_chain_all_unavailable_returns_none(self):
        """所有抽帧方案均不可用时返回 None（UI 显示占位图）"""
        video = self._make_fake_video()
        with mock.patch.object(self.service, "_ffmpeg_frame", return_value=None), \
             mock.patch.object(self.service, "_qlmanage_frame", return_value=None), \
             mock.patch.object(self.service, "_av_frame", return_value=None):
            data = self.service._gen_video(str(video), 64)
        self.assertIsNone(data)

    def test_av_frame_missing_lib_or_bad_file_returns_none(self):
        """PyAV 未安装或文件不可解码时静默返回 None"""
        data = self.service._av_frame(str(Path(self.temp_dir) / "nope.mp4"), 64)
        self.assertIsNone(data)

    def test_qlmanage_frame_missing_file_returns_none(self):
        """QuickLook 抽帧对不存在文件返回 None（跨平台安全）"""
        data = self.service._qlmanage_frame(str(Path(self.temp_dir) / "nope.mp4"), 64)
        self.assertIsNone(data)

    def test_set_cache_dir_repoints_and_creates(self):
        """重设缓存目录后新缩略图写入新目录"""
        new_cache = Path(self.temp_dir) / "new_cache"
        self.service.set_cache_dir(new_cache)
        self.assertEqual(self.service.cache_dir, new_cache)
        self.assertTrue(new_cache.is_dir())
        # 原缓存目录不受影响（旧文件保留）
        old_cache = Path(self.temp_dir) / "appdata" / "thumbnails"
        self.assertNotEqual(self.service.cache_dir, old_cache)


if __name__ == "__main__":
    unittest.main()
