"""报告服务测试 - CSV 素材清单导出"""
import csv
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.report_service import ReportService
from DITWorkstation.Models import MediaAsset


def _asset(**kwargs):
    defaults = dict(
        asset_id=str(uuid.uuid4())[:8],
        project_id="p1",
        file_path="/media/IMG_0001.cr2",
        file_name="IMG_0001.cr2",
        file_size=2048,
        file_type=".cr2",
        asset_type="raw",
        checksum_algorithm="xxhash64",
        checksum_value="abc123",
        rating=2,
        scene="S01",
        shot="A",
        take="02",
        backup_locations=["/backup1", "/backup2"],
        camera_model="RED V-RAPTOR",
        width=6144,
        height=3160,
    )
    defaults.update(kwargs)
    return MediaAsset(**defaults)


def test_export_assets_csv_creates_file(tmp_dir):
    """导出 CSV 应生成带 BOM 的文件且包含表头与数据行"""
    out = tmp_dir / "assets.csv"
    result = ReportService().export_assets_csv(
        [_asset(), _asset(file_name="IMG_0002.jpg", asset_type="image")],
        str(out),
    )

    assert result == str(out)
    assert out.exists()

    # utf-8-sig：文件开头应带 BOM（Excel 兼容）
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 3  # 表头 + 2 行数据
    headers = rows[0]
    assert "文件名" in headers
    assert "校验和" in headers
    assert "评级" in headers
    assert rows[1][0] == "IMG_0001.cr2"
    assert rows[1][3] == "raw"
    assert "★★ 备选" in rows[1][8]  # rating=2 的评级标签


def test_export_assets_csv_empty(tmp_dir):
    """空素材列表仍应导出只有表头的文件"""
    out = tmp_dir / "empty.csv"
    ReportService().export_assets_csv([], str(out))
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 1


def test_export_assets_csv_parent_dir_auto_created(tmp_dir):
    """目标目录不存在时应自动创建"""
    out = tmp_dir / "nested" / "deep" / "list.csv"
    ReportService().export_assets_csv([_asset()], str(out))
    assert out.exists()
