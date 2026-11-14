"""EXIF 解析真实素材回归（仅限本地运行）。

分类：``local_only`` + ``requires_local_media`` —— 依赖本机私有图片/RAW 素材、
本机目录结构或人工准备条件，**不纳入云端流水线执行范围**。

- 云端环境（CI / GITHUB_ACTIONS）由 ``conftest.py`` 在收集阶段忽略整个
  ``local/`` 目录（连模块导入与 fixture 都不会发生），CI workflow 与打包脚本
  另加 ``--ignore=DITWorkstationTests/local`` 与 ``-m "not local_only"`` 双保险。
- 本地运行：``pytest DITWorkstationTests/local -q``。
- 全量扫描需显式开启：``DIT_EXIF_FULL_SCAN=1`` / ``DIT_RAW_FULL_SCAN=1``。
- 素材目录可用 ``DIT_EXIF_FIXTURE_ROOT`` / ``DIT_EXIF_WORK_ROOT`` /
  ``DIT_RAW_ROOT`` / ``DIT_RAW_WORK_ROOT`` 覆盖；素材缺失时相关用例自动 skip。
"""

# EXIF DateTimeOriginal 本身不含时区，期望值必须保持 naive 本地时间。
# ruff: noqa: DTZ001

import os
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest
from DITWorkstation.Models import MediaMetadata
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Services.metadata_service import MetadataService
from PIL import Image


def _ci_environment() -> bool:
    """与 conftest 相同的云端判定，作为最后一道模块级守卫。"""
    ci_flag = os.environ.get("CI", "").strip().lower()
    actions_flag = os.environ.get("GITHUB_ACTIONS", "").strip().lower()
    return ci_flag in {"1", "true", "yes"} or actions_flag == "true"


# 极端兜底：即便调用方绕过 --ignore / collect_ignore，显式把本文件作为
# pytest 参数传入，云端环境也在模块导入阶段整体跳过，不执行任何用例或 fixture。
if _ci_environment():
    pytest.skip(
        "本地专属测试（local_only）：云端流水线不执行；请在未设置 "
        "CI / GITHUB_ACTIONS 的本地环境单独运行",
        allow_module_level=True,
    )

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.requires_local_media,
]

_JPG_ROOTS = (
    Path(os.environ.get("DIT_EXIF_FIXTURE_ROOT", str(Path.home() / "Pictures/CR"))),
    Path(
        os.environ.get("DIT_EXIF_WORK_ROOT", str(Path.home() / "Pictures/CR（工作）"))
    ),
)
_RAW_ROOTS = (
    Path(os.environ.get("DIT_RAW_ROOT", str(Path.home() / "Pictures/raw"))),
    Path(
        os.environ.get("DIT_RAW_WORK_ROOT", str(Path.home() / "Pictures/raw（工作）"))
    ),
)
_JPG_EXTS = {".jpg", ".jpeg", ".dng", ".tif", ".tiff"}
_RAW_EXTS = {".arw", ".nef", ".cr2", ".cr3", ".dng"}


def _index(root_paths, extensions):
    """建立真实素材索引（文件名 -> 路径）与全量文件列表。"""
    index: dict[str, Path] = {}
    files: list[Path] = []
    for root in root_paths:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in extensions:
                index.setdefault(path.name, path)
                files.append(path)
    return index, files


@pytest.fixture(scope="module")
def real_images():
    return _index(_JPG_ROOTS, _JPG_EXTS)


@pytest.fixture(scope="module")
def real_raws():
    return _index(_RAW_ROOTS, _RAW_EXTS)


def _require(index: dict[str, Path], name: str) -> Path:
    path = index.get(name)
    if path is None:
        pytest.skip(f"本机缺少真实素材 {name}")
    return path


def _assert_metadata(meta: MediaMetadata, expected: dict, label: str):
    for field, value in expected.items():
        assert getattr(meta, field) == value, f"{label} 的 {field} 解析错误"


# ---- 用户点名的三张非索尼素材：修复前后均应正确，作为不变性基线 ----

_POINTED_IMAGES = {
    "20261025-GL3A1500.jpg": {
        "camera_make": "Canon",
        "camera_model": "Canon EOS R6m2",
        "lens_model": "EF24-105mm f/4L IS USM",
        "iso": 500,
        "aperture": "f/4.0",
        "shutter_speed": "1/125",
        "focal_length": "50",
        "date_taken": datetime(2026, 10, 25, 19, 53, 35),
        "width": 6000,
        "height": 4000,
    },
    "Z62_6858.JPG": {
        "camera_make": "NIKON CORPORATION",
        "camera_model": "NIKON Z 6_2",
        "lens_model": "NIKKOR Z 24-120mm f/4 S",
        "iso": 2500,
        "aperture": "f/4.0",
        "shutter_speed": "1/100",
        "focal_length": "120",
        "date_taken": datetime(2026, 11, 3, 15, 7, 33),
        "width": 6048,
        "height": 4024,
    },
    "20261107-IMG_0226.jpg": {
        "camera_make": "Canon",
        "camera_model": "Canon EOS 5D Mark II",
        "lens_model": "EF24-70mm f/2.8L USM",
        "iso": 100,
        "aperture": "f/5.0",
        "shutter_speed": "1/200",
        "focal_length": "50",
        "date_taken": datetime(2026, 11, 7, 15, 41, 0),
        "width": 5616,
        "height": 3744,
    },
}


@pytest.mark.parametrize("name,expected", sorted(_POINTED_IMAGES.items()))
def test_pointed_non_sony_images(real_images, name, expected):
    """三张点名素材：EXIF 与原始标签逐字段一致。"""
    path = _require(real_images[0], name)
    _assert_metadata(MetadataService().read_metadata(str(path)), expected, name)


# 非索尼素材中原本以裸分数输出的样本（尼康 JPG / DNG）
_FRACTION_SAMPLES = {
    "Z62_6859.JPG": "30.5",  # 修复前 61/2
    "20260929Z62_2087.jpg": "27.5",  # 修复前 55/2
    "20261111-LXG_3492.jpg": "24.5",  # 修复前 49/2
    "20260918LXG_2077.dng": "30.5",  # 修复前 61/2
}


@pytest.mark.parametrize("name,focal", sorted(_FRACTION_SAMPLES.items()))
def test_non_sony_fraction_focal_length_fixed(real_images, name, focal):
    """Nikon 有理数焦距必须显示为小数，而非 '61/2' 这类裸分数。"""
    path = _require(real_images[0], name)
    meta = MetadataService().read_metadata(str(path))
    assert meta.camera_make == "NIKON CORPORATION"
    assert meta.focal_length == focal
    assert "/" not in meta.focal_length
    assert meta.aperture.startswith("f/")


# 后期软件导出（XMP/ICC 段在前导致 exifread 定位失败）的真实样本
_EXPORT_SAMPLES = (
    "DSC08072.jpg",
    "DSC08079.jpg",
    "LXC07201.jpg",
    "LXC07210.jpg",
    "LXC06659.jpg",
)


@pytest.mark.parametrize("name", _EXPORT_SAMPLES)
def test_exported_jpeg_exif_recovered(real_images, name):
    """导出型 JPEG：主路径失败但程序仍能读到完整 EXIF。"""
    path = _require(real_images[0], name)
    assert not MetadataService._load_exif_tags(path), "素材不再复现主路径失败"
    meta = MetadataService().read_metadata(str(path))
    assert meta.camera_make == "SONY"
    assert meta.camera_model in {"ILCE-7M4", "ILCE-6700"}
    assert meta.lens_model
    assert meta.iso > 0
    assert meta.aperture.startswith("f/")
    assert meta.shutter_speed.startswith("1/")
    assert meta.focal_length and "/" not in meta.focal_length
    assert meta.date_taken is not None
    assert meta.width > 0 and meta.height > 0


# 索尼直出素材基线：主路径解析不受影响
_SONY_BASELINE = (
    (
        "20260324LXC06130.JPG",
        250,
        "85",
        "1/160",
        "f/4.5",
        "9/2",
        datetime(2026, 3, 24, 16, 7, 19),
    ),
    (
        "20260324LXC06133.JPG",
        640,
        "161",
        "1/250",
        "f/5.6",
        "28/5",
        datetime(2026, 3, 24, 16, 8, 13),
    ),
    (
        "20260324LXC06140.JPG",
        400,
        "143",
        "1/250",
        "f/5.0",
        "5",
        datetime(2026, 3, 24, 16, 14, 38),
    ),
)


@pytest.mark.parametrize(
    "name,iso,focal,shutter,aperture,raw_aperture,date_taken", _SONY_BASELINE
)
def test_sony_baseline_unchanged(
    real_images, name, iso, focal, shutter, aperture, raw_aperture, date_taken
):
    """索尼素材：相机/镜头/ISO/焦距/快门/日期/尺寸不变，光圈仅做等价格式修正。"""
    path = _require(real_images[0], name)
    meta = MetadataService().read_metadata(str(path))
    assert meta.camera_make == "SONY"
    assert meta.camera_model == "ILCE-6700"
    assert meta.lens_model == "E 28-200mm F2.8-5.6 A071"
    assert meta.iso == iso
    assert meta.focal_length == focal
    assert meta.shutter_speed == shutter
    assert meta.date_taken == date_taken
    assert (meta.width, meta.height) == (4128, 6192)
    assert meta.aperture == aperture
    assert float(meta.aperture[2:]) == float(Fraction(raw_aperture))


@pytest.fixture
def import_service(db_service):
    return MediaImportService(db_service=db_service)


def test_real_images_end_to_end_import(import_service, project, real_images):
    """真实图片走完整导入链路：EXIF 字段正确落库。"""
    index = real_images[0]
    paths = [_require(index, name) for name in sorted(_POINTED_IMAGES)]
    result = import_service.import_assets(
        project.project_id,
        [str(p) for p in paths],
        compute_checksum=False,
        read_metadata=True,
    )
    assert result["imported"] == len(paths)
    assets = {
        asset.file_name: asset
        for asset in import_service.db_service.get_media_assets(project.project_id)
    }
    for name, expected in _POINTED_IMAGES.items():
        asset = assets[name]
        for field in (
            "camera_make",
            "camera_model",
            "lens_model",
            "focal_length",
            "date_taken",
            "width",
            "height",
        ):
            assert getattr(asset, field) == expected[field], f"{name} 入库 {field} 错误"


@pytest.mark.full_library_scan
@pytest.mark.skipif(
    os.environ.get("DIT_EXIF_FULL_SCAN") != "1",
    reason="全量 JPG 扫描需显式设置 DIT_EXIF_FULL_SCAN=1",
)
def test_full_real_library_scan(real_images):
    """全量真实 JPG：无裸分数、无整组 EXIF 丢失（803 张，按需启用）。"""
    _, files = real_images
    if not files:
        pytest.skip("本机没有真实素材目录")
    service = MetadataService()
    for path in files:
        meta = service.read_metadata(str(path))
        assert not (
            meta.focal_length
            and "/" in meta.focal_length
            and not meta.focal_length.startswith("1/")
        ), f"{path} 焦距仍为裸分数"
        assert not meta.aperture or meta.aperture.startswith("f/"), (
            f"{path} 光圈仍为裸分数"
        )
        try:
            with Image.open(path) as img:
                exif = img.getexif()
        except (OSError, ValueError):
            continue  # RAW/损坏文件由主路径负责，跳过真值比对
        if exif.get(271) or exif.get(272):
            assert meta.camera_make or meta.camera_model, f"{path} 相机信息丢失"
            assert meta.date_taken is not None, f"{path} 拍摄时间丢失"


# ---- RAW 真实素材 ----


@pytest.mark.parametrize(
    "name,expected",
    (
        ("NIK_3316.NEF", (8288, 5520)),
        ("Z62_7280.NEF", (6064, 4040)),
    ),
)
def test_nef_real_dimensions(real_raws, name, expected):
    """Nikon NEF：尺寸为主图真实尺寸，而非 160x120 缩略图。"""
    path = _require(real_raws[0], name)
    meta = MetadataService().read_metadata(str(path))
    assert (meta.width, meta.height) == expected
    rawpy = pytest.importorskip("rawpy")
    with rawpy.imread(str(path)) as raw:
        assert (meta.width, meta.height) == (raw.sizes.width, raw.sizes.height)


def test_cr2_lens_from_makernote(real_raws):
    """Canon CR2：镜头型号只写在 MakerNote，仍须解析出来。"""
    path = _require(real_raws[0], "IMG_0226.CR2")
    meta = MetadataService().read_metadata(str(path))
    assert meta.camera_make == "Canon"
    assert meta.camera_model == "Canon EOS 5D Mark II"
    assert meta.lens_model == "EF24-70mm f/2.8L USM"
    assert (meta.width, meta.height) == (5616, 3744)
    assert meta.aperture == "f/5.0"


def test_arw_baseline_unchanged(real_raws):
    """Sony ARW：尺寸/镜头/曝光参数保持修复前结果。"""
    path = _require(real_raws[0], "LXC09770.ARW")
    meta = MetadataService().read_metadata(str(path))
    assert (meta.camera_make, meta.camera_model) == ("SONY", "ILCE-6700")
    assert meta.lens_model == "E 28-200mm F2.8-5.6 A071"
    assert (meta.iso, meta.aperture, meta.shutter_speed, meta.focal_length) == (
        100,
        "f/4.0",
        "1/400",
        "55",
    )
    assert meta.date_taken == datetime(2026, 6, 17, 13, 43, 14)
    assert (meta.width, meta.height) == (6192, 4128)


@pytest.mark.full_library_scan
@pytest.mark.skipif(
    os.environ.get("DIT_RAW_FULL_SCAN") != "1",
    reason="RAW 全量扫描需显式设置 DIT_RAW_FULL_SCAN=1",
)
def test_full_raw_library_scan(real_raws):
    """全量 RAW（2134 张）：字段完整、无裸分数、无缩略图尺寸（按需启用）。"""
    _, files = real_raws
    if not files:
        pytest.skip("本机没有 RAW 素材目录")
    service = MetadataService()
    checked = 0
    for path in files:
        meta = service.read_metadata(str(path))
        assert meta.camera_make and meta.camera_model, path
        assert meta.lens_model, path
        assert meta.iso > 0 and meta.date_taken is not None, path
        assert meta.aperture.startswith("f/"), path
        assert "/" not in meta.focal_length, path
        assert meta.width >= 1000 and meta.height >= 1000, path
        checked += 1
    assert checked > 0
