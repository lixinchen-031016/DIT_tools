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
from DITWorkstation.Models import Project, ShootingLog, MediaAsset, Workspace


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
        # 新迁移机制按 PRAGMA user_version 门控；模拟"旧版本数据库"需把版本重置为 0
        conn.execute("PRAGMA user_version = 0")
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

    def test_delete_shooting_log_also_clears_scene_and_shot(self):
        """TR: 删除拍摄日志时同步清空关联素材的 scene/shot

        背景：create_log_with_assets(sync_scene_shot=True) 会把 log.scene/shot
        冗余写入 media_assets；删除日志若不清空，会留下"无主"字段。
        """
        project = self.db.create_project(name="场景镜头级联测试")

        log = ShootingLog(
            log_id="log_ss",
            project_id=project.project_id,
            scene="S099",
            shot="099A",
            take="02"
        )
        # 用 create_log_with_assets 关联素材并同步 scene/shot
        asset_ids = ["ast_ss_1", "ast_ss_2"]
        for aid in asset_ids:
            asset = MediaAsset(
                asset_id=aid,
                project_id=project.project_id,
                file_path=f"/p/{aid}.cr2",
                file_name=f"{aid}.cr2",
                file_size=100,
                file_type=".cr2"
            )
            self.db.add_media_asset(asset)

        self.db.create_log_with_assets(log, asset_ids, sync_scene_shot=True)

        # 删除前：scene/shot 应为 S099/099A
        before = self.db.get_media_assets(project.project_id)
        self.assertEqual(len(before), 2)
        for a in before:
            self.assertEqual(a.scene, "S099")
            self.assertEqual(a.shot, "099A")
            self.assertEqual(a.log_id, "log_ss")

        # 删除日志
        self.db.delete_shooting_log("log_ss")

        # 删除后：log_id 和 scene/shot 都应被清空
        after = self.db.get_media_assets(project.project_id)
        self.assertEqual(len(after), 2)
        for a in after:
            self.assertIsNone(a.log_id)
            self.assertEqual(a.scene, "")
            self.assertEqual(a.shot, "")

    def test_unlink_asset_clears_scene_and_shot(self):
        """TR: update_media_asset_log_id(log_id=None) 解除关联时清空 scene/shot"""
        project = self.db.create_project(name="解除关联测试")

        log = ShootingLog(
            log_id="log_unlink",
            project_id=project.project_id,
            scene="S042",
            shot="042A",
            take="01"
        )
        asset = MediaAsset(
            asset_id="ast_unlink",
            project_id=project.project_id,
            file_path="/p/unlink.cr2",
            file_name="unlink.cr2",
            file_size=100,
            file_type=".cr2"
        )
        self.db.add_media_asset(asset)
        self.db.create_log_with_assets(log, ["ast_unlink"], sync_scene_shot=True)

        # 解除关联前：scene/shot 有值
        before = self.db.get_media_asset("ast_unlink")
        self.assertEqual(before.log_id, "log_unlink")
        self.assertEqual(before.scene, "S042")
        self.assertEqual(before.shot, "042A")

        # 解除关联：log_id=None
        ok = self.db.update_media_asset_log_id("ast_unlink", None)
        self.assertTrue(ok)

        # 解除关联后：log_id 与 scene/shot 都被清空
        after = self.db.get_media_asset("ast_unlink")
        self.assertIsNone(after.log_id)
        self.assertEqual(after.scene, "")
        self.assertEqual(after.shot, "")

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

    def _seed_assets(self, count: int) -> str:
        """辅助：在单个项目下生成 count 个素材，返回 project_id"""
        project = self.db.create_project(name=f"分页测试{count}")
        for i in range(count):
            asset = MediaAsset(
                asset_id=f"pg_{count}_{i}",
                project_id=project.project_id,
                file_path=f"/p/f_{i}.cr2",
                file_name=f"f_{i}.cr2",
                file_size=100,
                file_type=".cr2"
            )
            self.db.add_media_asset(asset)
        return project.project_id

    def test_search_assets_limit_caps_results(self):
        """TR: search_assets limit 参数应限制返回数量"""
        self._seed_assets(10)
        # limit 小于总数
        results = self.db.search_assets(limit=5)
        self.assertEqual(len(results), 5)

    def test_search_assets_limit_exceeds_total(self):
        """TR: limit 大于结果数时返回全部"""
        self._seed_assets(3)
        results = self.db.search_assets(limit=100)
        self.assertEqual(len(results), 3)

    def test_search_assets_limit_none_no_cap(self):
        """TR: limit=None 时不限制返回"""
        self._seed_assets(8)
        results = self.db.search_assets(limit=None)
        self.assertEqual(len(results), 8)

    def test_search_assets_limit_zero_treated_as_none(self):
        """TR: limit=0 视为不限制（实现约定：limit > 0 才生效）"""
        self._seed_assets(4)
        results = self.db.search_assets(limit=0)
        self.assertEqual(len(results), 4)

    def test_search_assets_limit_with_other_filters(self):
        """TR: limit 与其他过滤条件组合使用"""
        project = self.db.create_project(name="limit+过滤测试")
        for i in range(10):
            scene = "S001" if i < 6 else "S002"
            asset = MediaAsset(
                asset_id=f"lf_{i}",
                project_id=project.project_id,
                file_path=f"/p/lf_{i}.cr2",
                file_name=f"lf_{i}.cr2",
                file_size=100,
                file_type=".cr2",
                scene=scene
            )
            self.db.add_media_asset(asset)

        # S001 有 6 条，limit=3 应只返回 3 条
        results = self.db.search_assets(scene="S001", limit=3)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.scene, "S001")

    def test_media_asset_rating_default(self):
        """TR: 新建素材 rating 默认为 0（未评级）"""
        project = self.db.create_project(name="评级默认测试")
        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/IMG_R001.cr2",
            file_name="IMG_R001.cr2",
            file_size=1024,
            file_type=".cr2",
        )
        self.db.add_media_asset(asset)
        fetched = self.db.get_media_asset(asset.asset_id)
        self.assertEqual(fetched.rating, 0)

    def test_media_asset_rating_save_and_retrieve(self):
        """TR: rating 字段能正确保存和读取"""
        project = self.db.create_project(name="评级读写测试")
        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/IMG_R002.cr2",
            file_name="IMG_R002.cr2",
            file_size=1024,
            file_type=".cr2",
            rating=3,
        )
        self.db.add_media_asset(asset)
        fetched = self.db.get_media_asset(asset.asset_id)
        self.assertEqual(fetched.rating, 3)

    def test_update_media_asset_rating(self):
        """TR: update_media_asset 能更新 rating"""
        project = self.db.create_project(name="评级更新测试")
        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path="/path/IMG_R003.cr2",
            file_name="IMG_R003.cr2",
            file_size=1024,
            file_type=".cr2",
        )
        self.db.add_media_asset(asset)

        ok = self.db.update_media_asset(asset.asset_id, rating=2)
        self.assertTrue(ok)
        fetched = self.db.get_media_asset(asset.asset_id)
        self.assertEqual(fetched.rating, 2)

    def test_search_assets_by_rating(self):
        """TR: search_assets 按 rating 过滤（>=rating）"""
        project = self.db.create_project(name="评级过滤测试")
        ratings = [0, 0, 1, 1, 2, 3]
        for i, r in enumerate(ratings):
            asset = MediaAsset(
                asset_id=f"rt_{i}",
                project_id=project.project_id,
                file_path=f"/p/rt_{i}.cr2",
                file_name=f"rt_{i}.cr2",
                file_size=100,
                file_type=".cr2",
                rating=r,
            )
            self.db.add_media_asset(asset)

        # rating>=1 应返回 4 条（1,1,2,3）
        results = self.db.search_assets(rating=1)
        self.assertEqual(len(results), 4)
        # rating>=3 应返回 1 条
        results = self.db.search_assets(rating=3)
        self.assertEqual(len(results), 1)
        # rating>=0 不过滤（约定：rating>0 才生效）
        results = self.db.search_assets(rating=0)
        self.assertEqual(len(results), 6)

    def test_search_assets_rating_with_other_filters(self):
        """TR: rating 过滤与其他条件组合"""
        project = self.db.create_project(name="评级组合测试")
        for i in range(6):
            scene = "S001" if i < 3 else "S002"
            asset = MediaAsset(
                asset_id=f"rc_{i}",
                project_id=project.project_id,
                file_path=f"/p/rc_{i}.cr2",
                file_name=f"rc_{i}.cr2",
                file_size=100,
                file_type=".cr2",
                scene=scene,
                rating=i % 3 + 1,  # S001: 1,2,3  S002: 1,2,3
            )
            self.db.add_media_asset(asset)

        # S001 + rating>=2 应返回 2 条（rating=2 和 rating=3）
        results = self.db.search_assets(scene="S001", rating=2)
        self.assertEqual(len(results), 2)

    # ===== 路径规范化回归测试（Finding 3）=====
    # 回归：DB 中 file_path 必须以 normalize_path() 规范化形式存储，
    # 且 asset_exists_by_path / get_asset_log_id_by_path /
    # update_asset_path_by_old_path / add_backup_location_to_assets
    # 的查询键必须同样规范化，否则在符号链接 / 相对路径 / 含 .. 场景下匹配失败。

    def _make_asset(self, project_id, file_path, name=None):
        return MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project_id,
            file_path=file_path,
            file_name=name or Path(file_path).name,
            file_size=1024,
            file_type=Path(file_path).suffix.lower(),
        )

    def test_asset_exists_by_path_with_relative_path(self):
        """相对路径查重应与入库的规范化路径匹配"""
        project = self.db.create_project(name="查重测试")
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "IMG_001.cr2"
            fp.write_bytes(b"x")
            # 入库用绝对路径
            asset = self._make_asset(project.project_id, str(fp.resolve()))
            self.db.add_media_asset(asset)
            # 查重用相对路径（切到 tmp 后）
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                self.assertTrue(self.db.asset_exists_by_path(project.project_id, "IMG_001.cr2"))
            finally:
                os.chdir(old_cwd)

    def test_asset_exists_by_path_with_dotdot(self):
        """含 .. 的路径查重应折叠后匹配"""
        project = self.db.create_project(name="查重测试2")
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            fp = sub / "IMG_002.cr2"
            fp.write_bytes(b"x")
            asset = self._make_asset(project.project_id, str(fp.resolve()))
            self.db.add_media_asset(asset)
            # 通过 tmp/sub/../sub/IMG_002.cr2 查重
            messy = str(Path(tmp) / "sub" / ".." / "sub" / "IMG_002.cr2")
            self.assertTrue(self.db.asset_exists_by_path(project.project_id, messy))

    def test_get_asset_log_id_by_path_normalizes_query(self):
        """get_asset_log_id_by_path 应规范化查询键"""
        project = self.db.create_project(name="log关联测试")
        log = ShootingLog(
            log_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            scene="S001",
            shot="001A",
            take="01",
        )
        self.db.create_shooting_log(log)
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "IMG_003.cr2"
            fp.write_bytes(b"x")
            asset = self._make_asset(project.project_id, str(fp.resolve()))
            asset.log_id = log.log_id
            self.db.add_media_asset(asset)

            # 用相对路径查询应能命中
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                found_log_id = self.db.get_asset_log_id_by_path("IMG_003.cr2")
                self.assertEqual(found_log_id, log.log_id)
            finally:
                os.chdir(old_cwd)

    def test_update_asset_path_by_old_path_normalizes_both(self):
        """重命名同步：old_path 和 new_path 都应规范化后匹配/写入"""
        project = self.db.create_project(name="重命名同步测试")
        with tempfile.TemporaryDirectory() as tmp:
            old_fp = Path(tmp) / "old.cr2"
            old_fp.write_bytes(b"x")
            new_fp = Path(tmp) / "new.cr2"

            asset = self._make_asset(project.project_id, str(old_fp.resolve()))
            self.db.add_media_asset(asset)

            # 用含 .. 的路径作为 old_path，应仍能匹配
            messy_old = str(Path(tmp) / "sub" / ".." / "old.cr2")
            ok = self.db.update_asset_path_by_old_path(
                messy_old, str(new_fp), new_name="new.cr2"
            )
            self.assertTrue(ok)

            # 验证 DB 中 file_path 已更新为规范化的 new_fp
            updated = self.db.asset_exists_by_path(project.project_id, str(new_fp))
            self.assertTrue(updated)

    def test_add_backup_location_normalizes_query(self):
        """add_backup_location_to_assets 应规范化 file_paths 查询键"""
        project = self.db.create_project(name="备份回写测试")
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "IMG_004.cr2"
            fp.write_bytes(b"x")
            asset = self._make_asset(project.project_id, str(fp.resolve()))
            self.db.add_media_asset(asset)

            # 用相对路径作为 file_paths 传入，应仍能匹配并回写
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                updated = self.db.add_backup_location_to_assets(
                    ["IMG_004.cr2"], "/backup/target", project_id=project.project_id
                )
                self.assertEqual(updated, 1)
            finally:
                os.chdir(old_cwd)

            # 验证 backup_locations 已写入
            assets = self.db.get_media_assets(project.project_id)
            self.assertIn("/backup/target", assets[0].backup_locations)

    def test_existing_asset_paths_batch(self):
        """批量查重：一次查询返回已存在路径集合（含规范化匹配）"""
        project = self.db.create_project(name="批量查重测试")
        with tempfile.TemporaryDirectory() as tmp:
            fp1 = Path(tmp) / "IMG_A.cr2"
            fp1.write_bytes(b"a")
            fp2 = Path(tmp) / "IMG_B.cr2"
            fp2.write_bytes(b"b")
            self.db.add_media_asset(self._make_asset(project.project_id, str(fp1.resolve())))
            self.db.add_media_asset(self._make_asset(project.project_id, str(fp2.resolve())))

            new_fp = Path(tmp) / "IMG_C.cr2"
            new_fp.write_bytes(b"c")
            # 混入相对路径与 .. 路径，验证规范化
            messy_fp2 = str(Path(tmp) / "sub" / ".." / "IMG_B.cr2")
            existing = self.db.existing_asset_paths(
                project.project_id,
                [str(fp1), messy_fp2, str(new_fp)]
            )
            self.assertIn(str(fp1.resolve()), existing)
            self.assertIn(str(fp2.resolve()), existing)
            self.assertNotIn(str(new_fp.resolve()), existing)

    def test_add_backup_location_batch_idempotent(self):
        """批量回写 backup_locations：重复路径去重、目标幂等"""
        project = self.db.create_project(name="批量回写测试")
        paths = []
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                fp = Path(tmp) / f"IMG_{i}.cr2"
                fp.write_bytes(b"x")
                # 生产流程（媒体导入）存的是 normalize_path() 规范化路径，
                # 这里用 resolve() 后的形式保持一致
                paths.append(str(fp.resolve()))
                self.db.add_media_asset(self._make_asset(project.project_id, str(fp.resolve())))

            target = "/backup/drive1"
            # 首次回写：3 个 asset 更新
            updated = self.db.add_backup_location_to_assets(
                [paths[0], paths[0], paths[1], paths[2]], target,
                project_id=project.project_id
            )
            self.assertEqual(updated, 3)

            assets = self.db.search_assets(project_id=project.project_id)
            for a in assets:
                self.assertEqual(a.backup_locations, [target])

            # 再次回写：全部幂等跳过
            updated_again = self.db.add_backup_location_to_assets(
                paths, target, project_id=project.project_id
            )
            self.assertEqual(updated_again, 0)

            # 追加第二个目标
            target2 = "/backup/drive2"
            updated2 = self.db.add_backup_location_to_assets(
                paths, target2, project_id=project.project_id
            )
            self.assertEqual(updated2, 3)
            assets = self.db.search_assets(project_id=project.project_id)
            for a in assets:
                self.assertEqual(sorted(a.backup_locations), sorted([target, target2]))

    def test_close_all_then_reuse(self):
        """连接池关闭后应能自动重建连接，服务继续可用"""
        project = self.db.create_project(name="连接池复用测试")
        self.db.close_all()
        project2 = self.db.create_project(name="连接池复用测试2")
        self.assertIsNotNone(project2.project_id)
        self.assertEqual(len(self.db.get_projects()), 2)

    def test_concurrent_thread_access(self):
        """连接池并发安全：多线程同时读写不报错且数据完整"""
        import threading as _threading
        project = self.db.create_project(name="并发访问测试")
        errors = []

        def worker(idx):
            try:
                for j in range(20):
                    asset = self._make_asset(
                        project.project_id, f"/tmp/conc_{idx}_{j}.cr2"
                    )
                    self.db.add_media_asset(asset)
                    self.db.search_assets(project_id=project.project_id, keyword="conc")
            except Exception as e:  # pragma: no cover - 仅记录异常供断言
                errors.append(e)

        threads = [_threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.db.get_media_assets(project.project_id)), 80)


class TestWorkspaceManagement(unittest.TestCase):
    """工作区管理测试 - 验证 Workspace→Project 两级层级"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        db_path = Path(self.temp_dir) / "test.db"
        self.db = DatabaseService(db_path=db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_workspace(self):
        """TR: 创建工作区"""
        ws = self.db.create_workspace(name="2026春拍", path="/Volumes/Work/2026Spring", description="春季广告片")
        self.assertIsNotNone(ws.workspace_id)
        self.assertEqual(ws.name, "2026春拍")
        self.assertEqual(ws.path, "/Volumes/Work/2026Spring")
        self.assertEqual(ws.description, "春季广告片")

        # 可读回
        loaded = self.db.get_workspace(ws.workspace_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "2026春拍")

    def test_get_workspaces_returns_all(self):
        """TR: get_workspaces 返回所有工作区"""
        self.db.create_workspace(name="WS1")
        self.db.create_workspace(name="WS2")
        workspaces = self.db.get_workspaces()
        self.assertEqual(len(workspaces), 2)

    def test_update_workspace(self):
        """TR: 更新工作区名称与路径"""
        ws = self.db.create_workspace(name="旧名", path="/old")
        ok = self.db.update_workspace(ws.workspace_id, name="新名", path="/new")
        self.assertTrue(ok)
        loaded = self.db.get_workspace(ws.workspace_id)
        self.assertEqual(loaded.name, "新名")
        self.assertEqual(loaded.path, "/new")

    def test_delete_workspace_reassigns_projects_to_default(self):
        """TR: 删除工作区时，其下项目自动归入默认工作区"""
        ws = self.db.create_workspace(name="待删除", path="/tmp")
        proj = self.db.create_project(name="P1", workspace_id=ws.workspace_id)

        ok = self.db.delete_workspace(ws.workspace_id)
        self.assertTrue(ok)

        # 工作区已删除
        self.assertIsNone(self.db.get_workspace(ws.workspace_id))

        # 项目仍在，workspace_id 变为 default
        loaded = self.db.get_project(proj.project_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.workspace_id, "default")

    def test_delete_default_workspace_rejected(self):
        """TR: 默认工作区不可删除（防止误操作）"""
        # 先创建一个项目触发 default 工作区创建（迁移逻辑仅在有孤儿项目时建 default）
        self.db.create_project(name="P1")
        self.assertIsNotNone(self.db.get_workspace("default"))

        ok = self.db.delete_workspace("default")
        self.assertFalse(ok)
        # 默认工作区仍存在
        self.assertIsNotNone(self.db.get_workspace("default"))

    def test_create_project_with_workspace(self):
        """TR: 在指定工作区内创建项目"""
        ws = self.db.create_workspace(name="WS", path="/tmp")
        proj = self.db.create_project(name="P", workspace_id=ws.workspace_id)
        self.assertEqual(proj.workspace_id, ws.workspace_id)

        # 读回验证
        loaded = self.db.get_project(proj.project_id)
        self.assertEqual(loaded.workspace_id, ws.workspace_id)

    def test_create_project_without_workspace_falls_to_default(self):
        """TR: 不指定 workspace_id 时项目归入默认工作区"""
        proj = self.db.create_project(name="孤立项目")
        self.assertEqual(proj.workspace_id, "default")

    def test_get_projects_filtered_by_workspace(self):
        """TR: get_projects 按 workspace_id 过滤"""
        ws1 = self.db.create_workspace(name="WS1")
        ws2 = self.db.create_workspace(name="WS2")
        p1 = self.db.create_project(name="P1", workspace_id=ws1.workspace_id)
        p2 = self.db.create_project(name="P2", workspace_id=ws1.workspace_id)
        p3 = self.db.create_project(name="P3", workspace_id=ws2.workspace_id)

        # ws1 下有 2 个项目
        ws1_projects = self.db.get_projects(workspace_id=ws1.workspace_id)
        self.assertEqual(len(ws1_projects), 2)
        ws1_ids = {p.project_id for p in ws1_projects}
        self.assertEqual(ws1_ids, {p1.project_id, p2.project_id})

        # ws2 下有 1 个项目
        ws2_projects = self.db.get_projects(workspace_id=ws2.workspace_id)
        self.assertEqual(len(ws2_projects), 1)
        self.assertEqual(ws2_projects[0].project_id, p3.project_id)

        # 不传 workspace_id 返回全部
        all_projects = self.db.get_projects()
        self.assertEqual(len(all_projects), 3)

    def test_update_project_workspace_id(self):
        """TR: 可通过 update_project 变更项目所属工作区"""
        ws1 = self.db.create_workspace(name="WS1")
        ws2 = self.db.create_workspace(name="WS2")
        proj = self.db.create_project(name="P", workspace_id=ws1.workspace_id)

        ok = self.db.update_project(proj.project_id, workspace_id=ws2.workspace_id)
        self.assertTrue(ok)
        loaded = self.db.get_project(proj.project_id)
        self.assertEqual(loaded.workspace_id, ws2.workspace_id)

    def test_legacy_projects_migrated_to_default(self):
        """TR: 旧项目（workspace_id NULL）在迁移时自动归入默认工作区

        模拟旧数据库：手动插入 workspace_id 为 NULL 的项目，重建 DatabaseService
        触发迁移，验证其被归入 default 工作区。
        """
        # 用原生 sqlite3 直接插入一条无 workspace_id 的项目（模拟旧数据）
        conn = sqlite3.connect(str(self.db.db_path))
        try:
            conn.execute(
                "INSERT INTO projects (project_id, name, description, base_path, created_at, updated_at) "
                "VALUES ('legacy1', '旧项目', '', '', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
            )
            conn.commit()
        finally:
            conn.close()

        # 重新打开数据库触发迁移
        db2 = DatabaseService(db_path=self.db.db_path)

        # 旧项目应被归入 default 工作区
        loaded = db2.get_project("legacy1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.workspace_id, "default")

        # default 工作区应存在
        default_ws = db2.get_workspace("default")
        self.assertIsNotNone(default_ws)
        self.assertEqual(default_ws.name, "默认工作区")

    def test_migration_is_idempotent(self):
        """TR: 多次启动迁移不会重复创建默认工作区或重复归集"""
        # 首次启动已有项目（已被归入 default）
        self.db.create_project(name="P1")

        # 再次实例化 DatabaseService（模拟重启）
        db2 = DatabaseService(db_path=self.db.db_path)
        default_ws_list = [w for w in db2.get_workspaces() if w.workspace_id == "default"]
        self.assertEqual(len(default_ws_list), 1)  # 不会重复创建

        # 第三次启动
        db3 = DatabaseService(db_path=self.db.db_path)
        default_ws_list = [w for w in db3.get_workspaces() if w.workspace_id == "default"]
        self.assertEqual(len(default_ws_list), 1)

    def test_find_duplicate_assets_cross_project(self):
        """跨项目查重：相同校验和的素材跨项目聚合，无重复时不返回"""
        p1 = self.db.create_project(name="项目A")
        p2 = self.db.create_project(name="项目B")

        def _asset(project_id, name, checksum):
            return MediaAsset(
                asset_id=str(uuid.uuid4())[:8],
                project_id=project_id,
                file_path=f"/path/{name}",
                file_name=name,
                file_size=1024,
                file_type=".jpg",
                asset_type="image",
                checksum_algorithm="xxhash64",
                checksum_value=checksum,
            )

        # 同一校验和跨两个项目出现（重复）
        self.db.add_media_asset(_asset(p1.project_id, "A_001.jpg", "dup1"))
        self.db.add_media_asset(_asset(p2.project_id, "B_001.jpg", "dup1"))
        # 另一校验和仅出现一次（非重复）
        self.db.add_media_asset(_asset(p1.project_id, "A_002.jpg", "unique"))
        # 空校验和不参与查重
        self.db.add_media_asset(_asset(p2.project_id, "B_002.jpg", ""))

        duplicates = self.db.find_duplicate_assets()
        self.assertEqual(len(duplicates), 1)
        group = duplicates[0]
        self.assertEqual(group["checksum_value"], "dup1")
        self.assertEqual(group["count"], 2)
        self.assertEqual(len(group["assets"]), 2)
        self.assertEqual(
            {a.project_id for a in group["assets"]},
            {p1.project_id, p2.project_id},
        )

    def test_find_duplicate_assets_none(self):
        """无重复校验和时返回空列表"""
        p1 = self.db.create_project(name="项目C")
        self.db.add_media_asset(MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=p1.project_id,
            file_path="/path/x.jpg",
            file_name="x.jpg",
            checksum_algorithm="xxhash64",
            checksum_value="only",
        ))
        self.assertEqual(self.db.find_duplicate_assets(), [])

    def test_tags_notes_roundtrip(self):
        """标签/备注写入后可读回，且可通过 update_media_asset 修改"""
        p = self.db.create_project(name="标签项目")
        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=p.project_id,
            file_path="/path/tag.jpg",
            file_name="tag.jpg",
            tags="日戏,主镜头",
            notes="需要精修",
        )
        self.db.add_media_asset(asset)
        loaded = self.db.get_media_asset(asset.asset_id)
        self.assertEqual(loaded.tags, "日戏,主镜头")
        self.assertEqual(loaded.notes, "需要精修")

        self.assertTrue(self.db.update_media_asset(
            asset.asset_id, tags="夜戏", notes="新备注"
        ))
        updated = self.db.get_media_asset(asset.asset_id)
        self.assertEqual(updated.tags, "夜戏")
        self.assertEqual(updated.notes, "新备注")

    def test_search_assets_by_tag(self):
        """按标签模糊搜索只返回匹配素材"""
        p = self.db.create_project(name="检索项目")
        a1 = MediaAsset(
            asset_id=str(uuid.uuid4())[:8], project_id=p.project_id,
            file_path="/a1.jpg", file_name="a1.jpg", tags="日戏,主镜头",
        )
        a2 = MediaAsset(
            asset_id=str(uuid.uuid4())[:8], project_id=p.project_id,
            file_path="/a2.jpg", file_name="a2.jpg", tags="夜戏",
        )
        self.db.add_media_assets_batch([a1, a2])

        hits = self.db.search_assets(project_id=p.project_id, tag="日戏")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].asset_id, a1.asset_id)

        hits = self.db.search_assets(project_id=p.project_id, tag="不存在")
        self.assertEqual(hits, [])

    def test_search_assets_fts_and_index_sync(self):
        """FTS 关键词搜索以及新增、更新、删除时的索引同步。"""
        if not self.db._fts_available:
            self.skipTest("当前 SQLite 未启用 FTS5")
        p = self.db.create_project(name="全文检索项目")
        asset = MediaAsset(
            asset_id="fts_001", project_id=p.project_id,
            file_path="/media/take_001.cr2", file_name="take_001.cr2",
            notes="closeup hero", tags="hero,day",
        )
        self.db.add_media_asset(asset)

        self.assertEqual(
            [a.asset_id for a in self.db.search_assets(keyword="take")],
            [asset.asset_id],
        )
        self.assertEqual(self.db.count_assets(keyword="hero"), 1)

        self.assertTrue(self.db.update_media_asset(
            asset.asset_id, file_name="wide_002.cr2", notes="wide shot"
        ))
        self.assertEqual(self.db.search_assets(keyword="take"), [])
        self.assertEqual(
            [a.asset_id for a in self.db.search_assets(keyword="wide")],
            [asset.asset_id],
        )

        self.assertTrue(self.db.delete_media_asset(asset.asset_id))
        self.assertEqual(self.db.search_assets(keyword="wide"), [])

    def test_asset_path_rename_syncs_fts_filename(self):
        """专用重命名更新入口也必须同步全文索引中的文件名。"""
        if not self.db._fts_available:
            self.skipTest("当前 SQLite 未启用 FTS5")
        p = self.db.create_project(name="重命名全文检索项目")
        asset = MediaAsset(
            asset_id="fts_rename", project_id=p.project_id,
            file_path="/media/old_name.cr2", file_name="old_name.cr2",
        )
        self.db.add_media_asset(asset)
        self.assertTrue(self.db.update_asset_path(
            asset.asset_id, "/media/new_name.cr2", "new_name.cr2"
        ))
        self.assertEqual(self.db.search_assets(keyword="old_name"), [])
        self.assertEqual(
            [a.asset_id for a in self.db.search_assets(keyword="new_name")],
            [asset.asset_id],
        )

    def test_search_assets_falls_back_to_like_without_fts(self):
        """FTS 不可用时仍保留原 LIKE 搜索行为。"""
        p = self.db.create_project(name="LIKE 回退项目")
        asset = MediaAsset(
            asset_id="like_001", project_id=p.project_id,
            file_path="/media/中文镜头.cr2", file_name="中文镜头.cr2",
        )
        self.db.add_media_asset(asset)
        original = self.db._fts_available
        self.db._fts_available = False
        try:
            hits = self.db.search_assets(keyword="中文镜头")
        finally:
            self.db._fts_available = original
        self.assertEqual([a.asset_id for a in hits], [asset.asset_id])

    def test_iter_search_assets_uses_batch_and_stable_order(self):
        """迭代查询按小批次返回，并使用 asset_id 作为同时间的稳定排序键。"""
        p = self.db.create_project(name="迭代查询项目")
        imported_at = datetime(2026, 1, 1, 12, 0, 0)
        for asset_id in ("iter_a", "iter_c", "iter_b"):
            self.db.add_media_asset(MediaAsset(
                asset_id=asset_id, project_id=p.project_id,
                file_path=f"/media/{asset_id}.cr2", file_name=f"{asset_id}.cr2",
                date_imported=imported_at,
            ))

        rows = list(self.db.iter_search_assets(
            project_id=p.project_id, batch_size=1
        ))
        self.assertEqual([a.asset_id for a in rows], ["iter_c", "iter_b", "iter_a"])
        page = self.db.search_assets(project_id=p.project_id, limit=2, offset=1)
        self.assertEqual([a.asset_id for a in page], ["iter_b", "iter_a"])

    def test_operation_log_record_and_query(self):
        """操作审计日志可写入并按时间倒序查询"""
        p = self.db.create_project(name="审计项目")
        self.assertTrue(self.db.record_operation(
            "导入素材", "成功 3 个", project_id=p.project_id
        ))
        self.assertTrue(self.db.record_operation(
            "数据备份", "completed：3 个文件", project_id=p.project_id
        ))
        self.assertTrue(self.db.record_operation("文件重命名", "成功 2 个"))

        recent = self.db.get_recent_operations(limit=2)
        self.assertEqual(len(recent), 2)
        # 倒序：最新的是「文件重命名」
        self.assertEqual(recent[0]["event"], "文件重命名")
        self.assertEqual(recent[1]["event"], "数据备份")
        self.assertIsNotNone(recent[0]["created_at"])

        by_project = self.db.get_recent_operations(limit=10, project_id=p.project_id)
        self.assertEqual(len(by_project), 2)
        self.assertTrue(all(o["project_id"] == p.project_id for o in by_project))


class TestMissingFileDetection(unittest.TestCase):
    """文件存在性验证与批量删除失效记录（对应素材信息模块的丢失检测功能）"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        db_path = Path(self.temp_dir) / "test.db"
        self.db = DatabaseService(db_path=db_path)
        self.project = self.db.create_project(name="丢失检测测试")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_asset(self, asset_id, file_path):
        return MediaAsset(
            asset_id=asset_id, project_id=self.project.project_id,
            file_path=file_path, file_name=Path(file_path).name,
            file_size=1024, file_type=".cr2",
        )

    def test_get_missing_file_asset_ids_detects_missing_and_present(self):
        """存在的文件不计入丢失，不存在/空路径计入丢失"""
        real = Path(self.temp_dir) / "real.cr2"
        real.write_text("data", encoding="utf-8")
        present = self.db.add_media_asset(self._make_asset("a_present", str(real)))
        missing = self.db.add_media_asset(self._make_asset("a_missing", "/no/such/file.cr2"))
        empty = self.db.add_media_asset(self._make_asset("a_empty", ""))

        missing_ids = self.db.get_missing_file_asset_ids(self.project.project_id)
        self.assertIn(missing.asset_id, missing_ids)
        self.assertIn(empty.asset_id, missing_ids)
        self.assertNotIn(present.asset_id, missing_ids)
        self.assertEqual(len(missing_ids), 2)

    def test_get_missing_file_asset_ids_empty_project(self):
        proj2 = self.db.create_project(name="空项目")
        self.assertEqual(self.db.get_missing_file_asset_ids(proj2.project_id), [])

    def test_delete_media_assets_batch(self):
        """批量删除仅移除数据库记录，返回成功条数，不影响磁盘文件"""
        real = Path(self.temp_dir) / "real.cr2"
        real.write_text("data", encoding="utf-8")
        missing = self.db.add_media_asset(self._make_asset("a_missing", "/no/such/file.cr2"))
        present = self.db.add_media_asset(self._make_asset("a_present", str(real)))

        deleted = self.db.delete_media_assets([missing.asset_id, present.asset_id])
        self.assertEqual(deleted, 2)
        self.assertIsNone(self.db.get_media_asset(missing.asset_id))
        self.assertIsNone(self.db.get_media_asset(present.asset_id))
        # 磁盘文件不受影响
        self.assertTrue(real.exists())

    def test_delete_media_assets_tolerates_unknown_ids(self):
        """传入不存在的 id 不抛异常，已存在的记录仍被正确删除"""
        ok = self.db.add_media_asset(self._make_asset("a_ok", "/no/such/file.cr2"))
        deleted = self.db.delete_media_assets([ok.asset_id, "nonexistent", ok.asset_id])
        self.assertEqual(deleted, 1)
        self.assertIsNone(self.db.get_media_asset(ok.asset_id))


if __name__ == "__main__":
    unittest.main()
