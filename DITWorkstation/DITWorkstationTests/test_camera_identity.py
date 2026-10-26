"""相机品牌机型识别测试（基于 EXIF，不依赖真实卡文件）。"""

import tempfile
import unittest
from pathlib import Path

from DITWorkstation.Services.camera_identity_service import (
    identify_camera,
    identify_camera_for_card,
    normalize_make,
)


def _make_jpeg_with_exif(path: Path, make: str, model: str):
    from PIL import Image

    img = Image.new("RGB", (16, 16), (120, 130, 140))
    exif = img.getexif()
    exif[271] = make  # Make
    exif[272] = model  # Model
    img.save(path, format="JPEG", exif=exif.tobytes())


class TestCameraIdentity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_identify_jpeg_exif(self):
        """从 JPEG EXIF 读取品牌机型并规范化 Make"""
        p = self.root / "DSC_0001.JPG"
        _make_jpeg_with_exif(p, "NIKON CORPORATION", "NIKON Z 8")
        info = identify_camera(p)
        self.assertIsNotNone(info)
        self.assertEqual(info["make"], "Nikon")
        self.assertEqual(info["model"], "NIKON Z 8")

    def test_identify_canon(self):
        p = self.root / "IMG_0001.JPG"
        _make_jpeg_with_exif(p, "Canon", "Canon EOS R5")
        info = identify_camera(p)
        self.assertEqual(info["make"], "Canon")
        self.assertEqual(info["model"], "Canon EOS R5")

    def test_identify_file_without_exif(self):
        """无 EXIF 的图片返回 None"""
        from PIL import Image

        p = self.root / "plain.jpg"
        Image.new("RGB", (8, 8)).save(p)
        self.assertIsNone(identify_camera(p))

    def test_identify_missing_file(self):
        self.assertIsNone(identify_camera(self.root / "not_exists.jpg"))

    def test_identify_card_samples_subdirs(self):
        """卡级识别：候选文件在 DCIM 子目录内也能找到"""
        dcim = self.root / "DCIM" / "100NIKON"
        dcim.mkdir(parents=True)
        _make_jpeg_with_exif(dcim / "DSC_0001.JPG", "SONY", "ILCE-7M4")
        info = identify_camera_for_card(self.root)
        self.assertIsNotNone(info)
        self.assertEqual(info["make"], "Sony")
        self.assertEqual(info["model"], "ILCE-7M4")

    def test_identify_card_empty(self):
        self.assertIsNone(identify_camera_for_card(self.root))
        self.assertIsNone(identify_camera_for_card(self.root / "nope"))

    def test_normalize_make(self):
        self.assertEqual(normalize_make("NIKON CORPORATION"), "Nikon")
        self.assertEqual(normalize_make("Canon"), "Canon")
        self.assertEqual(normalize_make("RED DIGITAL CINEMA"), "RED")
        self.assertEqual(normalize_make("Unknown Corp"), "Unknown Corp")


if __name__ == "__main__":
    unittest.main()
