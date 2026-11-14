"""EXIF 解析云端回归测试：仅使用合成数据，不依赖本机素材/本机路径/人工条件。

分类：``ci`` —— 适合在云端流水线执行。
真实相机素材回归见 ``local/test_exif_real_media.py``（``local_only``），
该目录在 CI 环境由 conftest 从收集阶段排除，不会被触发。

锁死两类真实缺陷：

1. exifread 3.5.1 定位 JPEG EXIF APP1 时只读取文件前约 4KB
   （``core/jpeg.py`` 中的 ``fh.read(base + 4000)``）。后期软件导出的 JPEG
   会在 EXIF 前写入大体积 ICC Profile / XMP 段，把 EXIF APP1 推到 8KB 之后，
   exifread 返回空标签，整组 EXIF 丢失；Pillow getexif() 作为兜底补齐。
2. EXIF 有理数标签（FNumber=28/5、FocalLength=49/2）未归一化，程序直接把
   裸分数写进素材信息，且 FNumber 的 float() 转换异常时连 ``f/`` 前缀都没有。
"""

# EXIF DateTimeOriginal 本身不含时区，期望值必须保持 naive 本地时间。
# ruff: noqa: DTZ001

from datetime import datetime
from fractions import Fraction
from io import BytesIO
from pathlib import Path

import pytest
from DITWorkstation.Models import MediaMetadata
from DITWorkstation.Services.metadata_service import MetadataService
from PIL import Image, TiffImagePlugin

pytestmark = pytest.mark.ci


def _blank_meta() -> MediaMetadata:
    return MediaMetadata(file_path="dummy.jpg", file_name="dummy.jpg")


def _write_xmp_first_jpeg(path: Path) -> Path:
    """构造「XMP APP1 在前、EXIF APP1 在后且超过 exifread 4KB 窗口」的导出型 JPEG。

    Pillow 把 EXIF 写在 IFD0（标准/子 IFD 标签同处一个 IFD，足矣验证字段映射），
    再在 SOI 后插入 4.6KB 的 XMP APP1，复现 exifread 定位失败的场景。
    """
    exif = Image.Exif()
    exif[271] = "Canon"  # Make
    exif[272] = "Canon EOS R6m2"  # Model
    exif[306] = "2026:10:25 19:53:35"  # DateTime
    exif[0x8827] = 500  # ISOSpeedRatings
    exif[0x829D] = TiffImagePlugin.IFDRational(28, 5)  # FNumber
    exif[0x829A] = TiffImagePlugin.IFDRational(1, 125)  # ExposureTime
    exif[0x920A] = TiffImagePlugin.IFDRational(49, 2)  # FocalLength
    exif[0xA434] = "EF24-105mm f/4L IS USM"  # LensModel
    exif[0x9003] = "2026:10:25 19:53:35"  # DateTimeOriginal

    buffer = BytesIO()
    Image.new("RGB", (64, 48), (120, 80, 40)).save(buffer, format="JPEG", exif=exif)
    data = buffer.getvalue()
    xmp = (
        b"http://ns.adobe.com/xap/1.0/\x00"
        b"<x:xmpmeta xmlns:x='adobe:ns:meta/'></x:xmpmeta>" + b" " * 4600
    )
    segment = b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp
    path.write_bytes(data[:2] + segment + data[2:])
    return path


class TestExifValueNormalization:
    """有理数/异常值归一化，以及索尼素材解析结果的不变性。"""

    def test_rational_tags_are_normalized(self):
        """Nikon/Canon 常见的分数 FNumber/FocalLength 必须转成可读数值。"""
        tags = {
            "Image Make": "Canon\x00",
            "Image Model": "Canon EOS R6m2",
            "EXIF LensModel": "EF24-105mm f/4L IS USM\x00\x00",
            "EXIF ISOSpeedRatings": "500",
            "EXIF FNumber": "28/5",
            "EXIF ExposureTime": "1/125",
            "EXIF FocalLength": "49/2",
            "EXIF DateTimeOriginal": "2026:11:03 15:07:33+08:00",
        }
        meta = _blank_meta()
        MetadataService()._apply_exif_tags(tags, meta)

        assert meta.camera_make == "Canon"
        assert meta.camera_model == "Canon EOS R6m2"
        assert meta.lens_model == "EF24-105mm f/4L IS USM"
        assert meta.iso == 500
        assert meta.aperture == "f/5.6"
        assert meta.shutter_speed == "1/125"
        assert meta.focal_length == "24.5"
        assert meta.date_taken == datetime(2026, 11, 3, 15, 7, 33)

    def test_integer_tags_keep_original_format(self):
        """整数标签格式不变，避免影响相机直出素材的既有显示。"""
        meta = _blank_meta()
        MetadataService()._apply_exif_tags(
            {
                "EXIF FNumber": "4",
                "EXIF ExposureTime": "1/200",
                "EXIF FocalLength": "50",
                "EXIF ExifImageWidth": "6000",
                "EXIF ExifImageLength": "4000",
            },
            meta,
        )
        assert meta.aperture == "f/4.0"
        assert meta.shutter_speed == "1/200"
        assert meta.focal_length == "50"
        assert (meta.width, meta.height) == (6000, 4000)

    def test_sony_baseline_fields_unchanged(self):
        """索尼直出 JPG：除原本以裸分数显示的光圈外，所有字段保持原值。"""
        meta = _blank_meta()
        MetadataService()._apply_exif_tags(
            {
                "Image Make": "SONY",
                "Image Model": "ILCE-6700",
                "EXIF LensModel": "E 28-200mm F2.8-5.6 A071",
                "EXIF ISOSpeedRatings": "250",
                "EXIF FNumber": "9/2",
                "EXIF ExposureTime": "1/160",
                "EXIF FocalLength": "85",
                "EXIF DateTimeOriginal": "2026:03:24 16:07:19",
                "EXIF ExifImageWidth": "4128",
                "EXIF ExifImageLength": "6192",
            },
            meta,
        )
        assert meta.camera_make == "SONY"
        assert meta.camera_model == "ILCE-6700"
        assert meta.lens_model == "E 28-200mm F2.8-5.6 A071"
        assert meta.iso == 250
        assert meta.shutter_speed == "1/160"
        assert meta.focal_length == "85"
        assert meta.date_taken == datetime(2026, 3, 24, 16, 7, 19)
        assert (meta.width, meta.height) == (4128, 6192)
        # 修正前为裸分数 '9/2'，修正后数值等价且格式统一
        assert meta.aperture == "f/4.5"
        assert float(meta.aperture[2:]) == float(Fraction(9, 2))

    def test_apex_fallback(self):
        """FNumber/ExposureTime 缺失时用 APEX 值换算，不产生裸 APEX 数字。"""
        meta = _blank_meta()
        MetadataService()._apply_exif_tags(
            {
                "EXIF ApertureValue": "4",  # 2^(4/2) = f/4
                "EXIF ShutterSpeedValue": "7",  # 2^-7 = 1/128s
                "EXIF PhotographicSensitivity": "800",
            },
            meta,
        )
        assert meta.aperture == "f/4.0"
        assert meta.shutter_speed == "1/128"
        assert meta.iso == 800

    def test_invalid_values_do_not_raise(self):
        """非法/空值兜底：保留原始文本或返回空值，绝不抛异常。"""
        service = MetadataService()
        meta = _blank_meta()
        service._apply_exif_tags(
            {
                "Image Make": b"NIKON CORPORATION\x00",
                "EXIF ISOSpeedRatings": "unknown",
                "EXIF FNumber": "Unknown",
                "EXIF FocalLength": "",
                "EXIF ExposureTime": "not-a-number",
                "EXIF DateTimeOriginal": "invalid",
            },
            meta,
        )
        assert meta.camera_make == "NIKON CORPORATION"
        assert meta.iso == 0
        assert meta.aperture == "Unknown"
        assert meta.focal_length == ""
        assert meta.shutter_speed == "not-a-number"
        assert meta.date_taken is None
        assert service._parse_datetime("2026-11-07 15:41:00.123") == datetime(
            2026, 11, 7, 15, 41, 0
        )
        assert service._format_shutter("0.01") == "1/100"
        assert service._format_shutter("1/125") == "1/125"


class TestPillowExifFallback:
    """exifread 主路径失败时的 Pillow 兜底。"""

    def test_xmp_first_export_jpeg_recovers_all_fields(self, tmp_path):
        """大 XMP 段导致 exifread 返回空标签时，全部 EXIF 字段由 Pillow 补齐。"""
        path = _write_xmp_first_jpeg(tmp_path / "xmp_first_export.jpg")
        # 根因复现：主路径确实读不到任何标签
        assert not MetadataService._load_exif_tags(path)

        meta = MetadataService().read_metadata(str(path))
        assert meta.camera_make == "Canon"
        assert meta.camera_model == "Canon EOS R6m2"
        assert meta.lens_model == "EF24-105mm f/4L IS USM"
        assert meta.iso == 500
        assert meta.aperture == "f/5.6"
        assert meta.shutter_speed == "1/125"
        assert meta.focal_length == "24.5"
        assert meta.date_taken == datetime(2026, 10, 25, 19, 53, 35)
        assert (meta.width, meta.height) == (64, 48)

    def test_fallback_never_overwrites_primary_values(self, tmp_path):
        """兜底只填空：主路径已解析出的有效值不被 Pillow 覆盖。"""
        path = _write_xmp_first_jpeg(tmp_path / "fallback_keep.jpg")
        meta = _blank_meta()
        meta.camera_make = "PresetMake"
        meta.iso = 3200
        MetadataService()._apply_pillow_exif(path, meta)
        assert meta.camera_make == "PresetMake"
        assert meta.iso == 3200
        assert meta.camera_model == "Canon EOS R6m2"
        assert meta.aperture == "f/5.6"

    def test_non_image_file_is_safe(self, tmp_path):
        """无 EXIF/损坏文件不抛异常，仅返回默认元数据。"""
        path = tmp_path / "broken.jpg"
        path.write_bytes(b"not a jpeg at all")
        meta = MetadataService().read_metadata(str(path))
        assert meta.camera_make == ""
        assert meta.iso == 0
        assert meta.date_taken is None


class TestRawExifEdges:
    """RAW 专属边界：内嵌缩略图尺寸与 MakerNote 镜头信息（合成数据）。"""

    def test_thumbnail_dimensions_are_rejected(self):
        """IFD0 的 160x120 是 NEF 内嵌缩略图，不能被当作主图尺寸。"""
        meta = _blank_meta()
        MetadataService._apply_exif_dimensions(
            {"Image ImageWidth": "160", "Image ImageLength": "120"}, meta
        )
        assert (meta.width, meta.height) == (0, 0)
        MetadataService._apply_exif_dimensions(
            {"EXIF ExifImageWidth": "6192", "EXIF ExifImageLength": "4128"}, meta
        )
        assert (meta.width, meta.height) == (6192, 4128)

    def test_makernote_lens_fallback(self, monkeypatch, tmp_path):
        """标准 LensModel 缺失时，从 MakerNote 补镜头型号。"""
        import exifread

        def fake_process_file(_fh, details=False):
            return {"MakerNote LensModel": "EF24-70mm f/2.8L USM"} if details else {}

        monkeypatch.setattr(exifread, "process_file", fake_process_file)
        path = tmp_path / "fake.CR2"
        path.write_bytes(b"fake")
        meta = _blank_meta()
        MetadataService._apply_makernote_lens(path, meta)
        assert meta.lens_model == "EF24-70mm f/2.8L USM"

    def test_raw_dimensions_fallback(self, monkeypatch, tmp_path):
        """rawpy 可用时以主图尺寸覆盖缩略图尺寸（rawpy 本体用 mock 替换）。"""
        rawpy = pytest.importorskip("rawpy")

        class FakeSizes:
            width = 8288
            height = 5520
            raw_width = 8288
            raw_height = 5520

        class FakeRaw:
            sizes = FakeSizes()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(rawpy, "imread", lambda _path: FakeRaw())
        meta = _blank_meta()
        MetadataService._read_raw_dimensions(tmp_path / "fake.NEF", meta)
        assert (meta.width, meta.height) == (8288, 5520)
