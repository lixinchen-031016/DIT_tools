"""Models 层单元测试 - Phase 4.5

覆盖：
- AssetRating 枚举值与数据库存储约定（INTEGER 0/1/2/3）一致
- RATING_LABELS 覆盖所有 AssetRating 成员，无悬空键值
- MediaAsset.rating 默认值与 AssetRating.NONE.value 一致
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Models import (
    AssetRating, RATING_LABELS, MediaAsset, AssetType,
)


class TestAssetRating(unittest.TestCase):
    """AssetRating 枚举值约定"""

    def test_values_match_db_integer(self):
        """枚举值必须与数据库 INTEGER 列保持一致"""
        self.assertEqual(AssetRating.NONE.value, 0)
        self.assertEqual(AssetRating.USABLE.value, 1)
        self.assertEqual(AssetRating.BACKUP.value, 2)
        self.assertEqual(AssetRating.PREFERRED.value, 3)

    def test_no_duplicate_values(self):
        """枚举值不可重复"""
        values = [m.value for m in AssetRating]
        self.assertEqual(len(values), len(set(values)))


class TestRatingLabels(unittest.TestCase):
    """RATING_LABELS 完整性"""

    def test_labels_cover_all_ratings(self):
        """RATING_LABELS 必须覆盖所有 AssetRating 成员"""
        for member in AssetRating:
            self.assertIn(member.value, RATING_LABELS,
                          f"RATING_LABELS 缺少 {member.name} 的标签")

    def test_labels_no_extra_keys(self):
        """RATING_LABELS 不应包含非枚举值的键"""
        valid_values = {m.value for m in AssetRating}
        for key in RATING_LABELS:
            self.assertIn(key, valid_values,
                          f"RATING_LABELS 包含非法键: {key}")

    def test_labels_non_empty(self):
        """每个评级标签必须非空字符串"""
        for value, label in RATING_LABELS.items():
            self.assertIsInstance(label, str)
            self.assertTrue(label.strip(), f"评级 {value} 的标签为空")


class TestMediaAssetRatingDefault(unittest.TestCase):
    """MediaAsset.rating 默认值约定"""

    def test_default_rating_is_none(self):
        """新建 MediaAsset 默认 rating=0（未评级），与 AssetRating.NONE.value 一致"""
        asset = MediaAsset(
            asset_id="test_1",
            project_id="proj_1",
            file_path="/tmp/x.cr2",
            file_name="x.cr2",
        )
        self.assertEqual(asset.rating, AssetRating.NONE.value)

    def test_rating_accepts_all_enum_values(self):
        """MediaAsset.rating 应能接受所有 AssetRating 枚举值"""
        for member in AssetRating:
            asset = MediaAsset(
                asset_id=f"test_{member.name}",
                project_id="proj_1",
                file_path="/tmp/x.cr2",
                file_name="x.cr2",
                rating=member.value,
            )
            self.assertEqual(asset.rating, member.value)


class TestAssetType(unittest.TestCase):
    """AssetType 枚举完整性（已有，确保不回归）"""

    def test_values_are_strings(self):
        """AssetType 枚举值必须是字符串（数据库存储为 TEXT）"""
        for member in AssetType:
            self.assertIsInstance(member.value, str)


if __name__ == "__main__":
    unittest.main()
