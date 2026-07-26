"""数据库服务测试 - 对应 TR-6.1, TR-6.2, TR-7.1"""
import os
import sys
import sqlite3
import tempfile
import shutil
import time
import unittest
import uuid
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Models import Project, ShootingLog, MediaAsset


class TestDatabaseService(unittest.TestCase):
    """数据库服务测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        db_path = Path(self.temp_dir) / "test.db"
        self.db = DatabaseService(db_path=db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_project(self):
        """TR-6.1: 成功创建新项目"""
        project = self.db.create_project(
            name="测试项目",
            description="这是一个测试项目",
            base_path="/tmp/test_project"
        )
        self.assertIsNotNone(project.project_id)
        self.assertEqual(project.name, "测试项目")
        self.assertEqual(project.description, "这是一个测试项目")

    def test_get_projects(self):
        """获取项目列表"""
        self.db.create_project(name="项目A")
        self.db.create_project(name="项目B")

        projects = self.db.get_projects()
        self.assertEqual(len(projects), 2)

    def test_get_project_by_id(self):
        """按ID获取项目"""
        project = self.db.create_project(name="查找测试")
        found = self.db.get_project(project.project_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "查找测试")

    def test_delete_project(self):
        """删除项目"""
        project = self.db.create_project(name="待删除")
        self.db.delete_project(project.project_id)
        found = self.db.get_project(project.project_id)
        self.assertIsNone(found)

    def test_create_shooting_log(self):
        """TR-6.1: 记录拍摄日志"""
        project = self.db.create_project(name="日志测试项目")

        log = ShootingLog(
            log_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            scene="S001",
            shot="001A",
            take="03",
            description="主角入场镜头",
            camera="RED V-RAPTOR",
            lens="Sigma 50mm T1.5",
            iso=800,
            aperture="f/2.8",
            shutter_speed="1/48s",
            notes="使用斯坦尼康"
        )
        result = self.db.create_shooting_log(log)
        self.assertEqual(result.scene, "S001")
        self.assertEqual(result.shot, "001A")

    def test_get_shooting_logs(self):
        """TR-6.2: 获取项目拍摄日志"""
        project = self.db.create_project(name="日志列表测试")

        for i in range(3):
            log = ShootingLog(
                log_id=str(uuid.uuid4())[:8],
                project_id=project.project_id,
                scene=f"S{i+1:03d}",
                shot=f"{i+1:03d}A",
                take="01"
            )
            self.db.create_shooting_log(log)

        logs = self.db.get_shooting_logs(project.project_id)
        self.assertEqual(len(logs), 3)

    def test_shooting_log_file_association(self):
        """TR-6.2: 拍摄日志与素材正确关联"""
        project = self.db.create_project(name="关联测试")

        log = ShootingLog(
            log_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            scene="S001",
            shot="001A",
            take="01",
            file_paths=["/path/to/file1.cr2", "/path/to/file2.cr2"]
        )
        self.db.create_shooting_log(log)

        logs = self.db.get_shooting_logs(project.project_id)
        self.assertEqual(len(logs[0].file_paths), 2)
        self.assertIn("/path/to/file1.cr2", logs[0].file_paths)

    def test_add_media_asset(self):
        """添加素材资产"""
        project = self.db.create_project(name="素材测试")

        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/to/IMG_001.cr2",
            file_name="IMG_001.cr2",
            file_size=50 * 1024 * 1024,
            file_type=".cr2",
            checksum_value="abc123def456",
            scene="S001",
            shot="001A"
        )
        result = self.db.add_media_asset(asset)
        self.assertEqual(result.file_name, "IMG_001.cr2")

    def test_search_assets_by_scene(self):
        """TR-7.1: 按场景搜索素材"""
        project = self.db.create_project(name="搜索测试")

        for i in range(10):
            scene = "S001" if i < 5 else "S002"
            asset = MediaAsset(
                asset_id=str(uuid.uuid4())[:8],
                project_id=project.project_id,
                file_path=f"/path/IMG_{i:03d}.cr2",
                file_name=f"IMG_{i:03d}.cr2",
                file_size=1024,
                file_type=".cr2",
                scene=scene,
                shot=f"{i:03d}A"
            )
            self.db.add_media_asset(asset)

        results = self.db.search_assets(scene="S001")
        self.assertEqual(len(results), 5)

    def test_search_assets_by_keyword(self):
        """TR-7.1: 按关键词搜索"""
        project = self.db.create_project(name="关键词搜索")

        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/DSC_0001.nef",
            file_name="DSC_0001.nef",
            file_size=1024,
            file_type=".nef",
            scene="S001"
        )
        self.db.add_media_asset(asset)

        results = self.db.search_assets(keyword="DSC")
        self.assertEqual(len(results), 1)

    def test_search_assets_by_type(self):
        """TR-7.1: 按文件类型搜索"""
        project = self.db.create_project(name="类型搜索")

        for ext in [".cr2", ".jpg", ".cr2", ".nef", ".jpg"]:
            asset = MediaAsset(
                asset_id=str(uuid.uuid4())[:8],
                project_id=project.project_id,
                file_path=f"/path/file{ext}",
                file_name=f"file{ext}",
                file_size=1024,
                file_type=ext
            )
            self.db.add_media_asset(asset)

        results = self.db.search_assets(file_type=".cr2")
        self.assertEqual(len(results), 2)

        results = self.db.search_assets(file_type=".jpg")
        self.assertEqual(len(results), 2)

    def test_update_project_refreshes_updated_at(self):
        """TR: update_project 应刷新 updated_at 字段"""
        project = self.db.create_project(name="时间戳测试")
        original_updated = self.db.get_project(project.project_id).updated_at

        time.sleep(0.05)  # 确保时间戳不同
        ok = self.db.update_project(project.project_id, name="新名称")
        self.assertTrue(ok)

        updated = self.db.get_project(project.project_id)
        self.assertEqual(updated.name, "新名称")
        self.assertGreater(updated.updated_at, original_updated)

    def test_media_asset_new_columns(self):
        """TR: media_assets 新增字段应能正确读写"""
        project = self.db.create_project(name="新列测试")

        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/to/IMG_001.cr2",
            file_name="IMG_001.cr2",
            file_size=1024,
            file_type=".cr2",
            asset_type="raw",
            width=640,
            height=480,
            duration_seconds=12.5,
            lens_model="Sigma 50mm",
            focal_length="50mm",
            video_metadata='{"codec":"h264"}',
            is_working_copy=True,
            original_path="/original/path.cr2"
        )
        self.db.add_media_asset(asset)

        fetched = self.db.get_media_asset(asset.asset_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.asset_type, "raw")
        self.assertEqual(fetched.width, 640)
        self.assertEqual(fetched.height, 480)
        self.assertAlmostEqual(fetched.duration_seconds, 12.5)
        self.assertEqual(fetched.lens_model, "Sigma 50mm")
        self.assertEqual(fetched.focal_length, "50mm")
        self.assertEqual(fetched.video_metadata, '{"codec":"h264"}')
        self.assertTrue(fetched.is_working_copy)
        self.assertEqual(fetched.original_path, "/original/path.cr2")

    def test_update_media_asset_new_columns(self):
        """TR: update_media_asset 应能更新新字段"""
        project = self.db.create_project(name="更新新列测试")

        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/to/file.jpg",
            file_name="file.jpg",
            file_size=1024,
            file_type=".jpg"
        )
        self.db.add_media_asset(asset)

        ok = self.db.update_media_asset(
            asset.asset_id,
            width=1920,
            height=1080,
            lens_model="Canon 24-70",
            focal_length="35mm",
            duration_seconds=60.0,
            video_metadata='{"duration":60}'
        )
        self.assertTrue(ok)

        fetched = self.db.get_media_asset(asset.asset_id)
        self.assertEqual(fetched.width, 1920)
        self.assertEqual(fetched.height, 1080)
        self.assertEqual(fetched.lens_model, "Canon 24-70")
        self.assertEqual(fetched.focal_length, "35mm")
        self.assertAlmostEqual(fetched.duration_seconds, 60.0)
        self.assertEqual(fetched.video_metadata, '{"duration":60}')

    def test_migrate_db_adds_missing_columns(self):
        """TR: _migrate_db 应为旧表补齐新字段并保持可用"""
        # 模拟旧表（缺少新增字段）
        conn = sqlite3.connect(str(self.db.db_path))
        conn.execute("DROP TABLE media_assets")
        conn.execute("""
            CREATE TABLE media_assets (
                asset_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                file_type TEXT DEFAULT '',
                checksum_algorithm TEXT DEFAULT 'xxhash64',
                checksum_value TEXT DEFAULT '',
                scene TEXT DEFAULT '',
                shot TEXT DEFAULT '',
                take TEXT DEFAULT '',
                date_imported TEXT NOT NULL,
                date_taken TEXT,
                camera_make TEXT DEFAULT '',
                camera_model TEXT DEFAULT '',
                backup_locations TEXT DEFAULT '',
                log_id TEXT
            )
        """)
        conn.commit()
        conn.close()

        # 触发迁移
        self.db._migrate_db()

        # 验证新字段已添加
        conn = sqlite3.connect(str(self.db.db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(media_assets)").fetchall()}
        conn.close()

        for col in ["asset_type", "is_working_copy", "original_path", "width", "height",
                    "duration_seconds", "lens_model", "focal_length", "video_metadata"]:
            self.assertIn(col, cols, f"迁移未补齐字段: {col}")

        # 验证迁移后可正常写入并读取
        project = self.db.create_project(name="迁移后写入测试")
        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/file.jpg",
            file_name="file.jpg",
            file_size=1024,
            file_type=".jpg",
            asset_type="image",
            width=800
        )
        self.db.add_media_asset(asset)
        fetched = self.db.get_media_asset(asset.asset_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.width, 800)
        self.assertEqual(fetched.asset_type, "image")

    def test_migrate_db_idempotent(self):
        """TR: 迁移应可幂等重复执行"""
        # 多次执行迁移不应报错
        self.db._migrate_db()
        self.db._migrate_db()

        # 数据库仍可用
        project = self.db.create_project(name="幂等性测试")
        self.assertIsNotNone(project.project_id)

    def test_search_assets_by_log_id(self):
        """TR: search_assets 支持按 log_id 筛选"""
        project = self.db.create_project(name="log_id搜索测试")

        log = ShootingLog(
            log_id="log001",
            project_id=project.project_id,
            scene="S001",
            shot="001A",
            take="01"
        )
        self.db.create_shooting_log(log)

        for i in range(5):
            asset = MediaAsset(
                asset_id=f"asset_{i}",
                project_id=project.project_id,
                file_path=f"/path/file_{i}.cr2",
                file_name=f"file_{i}.cr2",
                file_size=1024,
                file_type=".cr2",
                log_id="log001" if i < 3 else None
            )
            self.db.add_media_asset(asset)

        results = self.db.search_assets(log_id="log001")
        self.assertEqual(len(results), 3)
        for a in results:
            self.assertEqual(a.log_id, "log001")

        results_all = self.db.search_assets(project_id=project.project_id)
        self.assertEqual(len(results_all), 5)

    def test_get_assets_by_log_id(self):
        """TR: get_assets_by_log_id 获取某日志关联的素材"""
        project = self.db.create_project(name="按日志取素材测试")

        log = ShootingLog(
            log_id="log002",
            project_id=project.project_id,
            scene="S002",
            shot="002A",
            take="01"
        )
        self.db.create_shooting_log(log)

        for i in range(3):
            asset = MediaAsset(
                asset_id=f"ast_{i}",
                project_id=project.project_id,
                file_path=f"/p/f_{i}.cr2",
                file_name=f"f_{i}.cr2",
                file_size=100,
                file_type=".cr2",
                log_id="log002"
            )
            self.db.add_media_asset(asset)

        assets = self.db.get_assets_by_log_id("log002")
        self.assertEqual(len(assets), 3)
        self.assertEqual(assets[0].log_id, "log002")

    def test_update_media_asset_log_id(self):
        """TR: update_media_asset_log_id 关联与解除关联"""
        project = self.db.create_project(name="关联测试")

        log = ShootingLog(
            log_id="log003",
            project_id=project.project_id,
            scene="S003",
            shot="003A",
            take="01"
        )
        self.db.create_shooting_log(log)

        asset = MediaAsset(
            asset_id="ast_link",
            project_id=project.project_id,
            file_path="/p/link.cr2",
            file_name="link.cr2",
            file_size=100,
            file_type=".cr2"
        )
        self.db.add_media_asset(asset)

        # 初始无关联
        fetched = self.db.get_media_asset("ast_link")
        self.assertIsNone(fetched.log_id)

        # 关联
        ok = self.db.update_media_asset_log_id("ast_link", "log003")
        self.assertTrue(ok)
        fetched = self.db.get_media_asset("ast_link")
        self.assertEqual(fetched.log_id, "log003")

        # 解除关联
        ok = self.db.update_media_asset_log_id("ast_link", None)
        self.assertTrue(ok)
        fetched = self.db.get_media_asset("ast_link")
        self.assertIsNone(fetched.log_id)

    def test_delete_shooting_log_cascades_asset_log_id(self):
        """TR: 删除拍摄日志时级联清除素材的 log_id"""
        project = self.db.create_project(name="级联删除测试")

        log = ShootingLog(
            log_id="log_del",
            project_id=project.project_id,
            scene="S001",
            shot="001A",
            take="01"
        )
        self.db.create_shooting_log(log)

        for i in range(3):
            asset = MediaAsset(
                asset_id=f"casc_{i}",
                project_id=project.project_id,
                file_path=f"/p/c_{i}.cr2",
                file_name=f"c_{i}.cr2",
                file_size=100,
                file_type=".cr2",
                log_id="log_del"
            )
            self.db.add_media_asset(asset)

        # 删除前检查
        pre = self.db.get_assets_by_log_id("log_del")
        self.assertEqual(len(pre), 3)

        # 删除日志
        self.db.delete_shooting_log("log_del")

        # 日志已删除
        self.assertIsNone(self.db.get_shooting_log("log_del"))

        # 素材仍存在，但 log_id 被清除
        all_assets = self.db.get_media_assets(project.project_id)
        self.assertEqual(len(all_assets), 3)
        for a in all_assets:
            self.assertIsNone(a.log_id)

    def test_create_log_with_assets_links_assets(self):
        """TR: create_log_with_assets 创建日志并关联多个素材"""
        project = self.db.create_project(name="批量关联测试")

        asset_ids = []
        for i in range(3):
            asset = MediaAsset(
                asset_id=f"ba_{i}",
                project_id=project.project_id,
                file_path=f"/p/ba_{i}.cr2",
                file_name=f"ba_{i}.cr2",
                file_size=100,
                file_type=".cr2"
            )
            self.db.add_media_asset(asset)
            asset_ids.append(asset.asset_id)

        log = ShootingLog(
            log_id="log_ba",
            project_id=project.project_id,
            scene="S010",
            shot="010A",
            take="01"
        )
        self.db.create_log_with_assets(log, asset_ids, sync_scene_shot=False)

        # 日志已创建
        self.assertIsNotNone(self.db.get_shooting_log("log_ba"))

        # 三个素材都被关联
        linked = self.db.get_assets_by_log_id("log_ba")
        self.assertEqual(len(linked), 3)
        for a in linked:
            self.assertEqual(a.log_id, "log_ba")

    def test_create_log_with_assets_syncs_scene_shot(self):
        """TR: sync_scene_shot=True 时把 log 的 scene/shot 写入素材"""
        project = self.db.create_project(name="同步字段测试")

        asset = MediaAsset(
            asset_id="ast_sync",
            project_id=project.project_id,
            file_path="/p/sync.cr2",
            file_name="sync.cr2",
            file_size=100,
            file_type=".cr2",
            scene="OLD_SCENE",
            shot="OLD_SHOT"
        )
        self.db.add_media_asset(asset)

        log = ShootingLog(
            log_id="log_sync",
            project_id=project.project_id,
            scene="SNEW",
            shot="NEWA",
            take="01"
        )
        self.db.create_log_with_assets(log, ["ast_sync"], sync_scene_shot=True)

        fetched = self.db.get_media_asset("ast_sync")
        self.assertEqual(fetched.log_id, "log_sync")
        self.assertEqual(fetched.scene, "SNEW")
        self.assertEqual(fetched.shot, "NEWA")

    def test_create_log_with_assets_no_sync(self):
        """TR: sync_scene_shot=False 时仅关联 log_id，不改 scene/shot"""
        project = self.db.create_project(name="不同步字段测试")

        asset = MediaAsset(
            asset_id="ast_nosync",
            project_id=project.project_id,
            file_path="/p/nosync.cr2",
            file_name="nosync.cr2",
            file_size=100,
            file_type=".cr2",
            scene="KEEP_SCENE",
            shot="KEEP_SHOT"
        )
        self.db.add_media_asset(asset)

        log = ShootingLog(
            log_id="log_nosync",
            project_id=project.project_id,
            scene="SOTHER",
            shot="OTHER",
            take="01"
        )
        self.db.create_log_with_assets(log, ["ast_nosync"], sync_scene_shot=False)

        fetched = self.db.get_media_asset("ast_nosync")
        self.assertEqual(fetched.log_id, "log_nosync")
        self.assertEqual(fetched.scene, "KEEP_SCENE")
        self.assertEqual(fetched.shot, "KEEP_SHOT")

    def test_create_log_with_assets_empty_list(self):
        """TR: asset_ids 为空时仅创建日志，不报错"""
        project = self.db.create_project(name="空列表测试")

        log = ShootingLog(
            log_id="log_empty",
            project_id=project.project_id,
            scene="S001",
            shot="001A",
            take="01"
        )
        self.db.create_log_with_assets(log, [], sync_scene_shot=True)

        self.assertIsNotNone(self.db.get_shooting_log("log_empty"))
        self.assertEqual(len(self.db.get_assets_by_log_id("log_empty")), 0)


if __name__ == "__main__":
    unittest.main()
