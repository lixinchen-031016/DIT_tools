"""媒体导入功能测试"""
import os
import tempfile
import pytest
from pathlib import Path

from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Models import AssetType, ShootingLog


@pytest.fixture
def import_service(temp_db):
    db_service, _ = temp_db
    return MediaImportService(db_service=db_service)


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test.db"
    service = DatabaseService(db_path=db_path)
    return service, service.db_path


class TestMediaImportService:
    """媒体导入服务测试"""

    def test_classify_image(self, import_service):
        """测试图片类型识别"""
        assert import_service.classify_media_type("test.jpg") == AssetType.IMAGE
        assert import_service.classify_media_type("test.JPEG") == AssetType.IMAGE
        assert import_service.classify_media_type("test.png") == AssetType.IMAGE

    def test_classify_video(self, import_service):
        """测试视频类型识别"""
        assert import_service.classify_media_type("test.mp4") == AssetType.VIDEO
        assert import_service.classify_media_type("test.MOV") == AssetType.VIDEO
        assert import_service.classify_media_type("test.mkv") == AssetType.VIDEO

    def test_classify_raw(self, import_service):
        """测试RAW类型识别"""
        assert import_service.classify_media_type("test.cr2") == AssetType.RAW
        assert import_service.classify_media_type("test.ARW") == AssetType.RAW
        assert import_service.classify_media_type("test.dng") == AssetType.RAW

    def test_classify_audio(self, import_service):
        """测试音频类型识别"""
        assert import_service.classify_media_type("test.mp3") == AssetType.AUDIO
        assert import_service.classify_media_type("test.wav") == AssetType.AUDIO

    def test_classify_other(self, import_service):
        """测试其他类型识别"""
        assert import_service.classify_media_type("test.txt") == AssetType.OTHER
        assert import_service.classify_media_type("test.pdf") == AssetType.OTHER

    def test_scan_media_folder(self, import_service, tmp_path):
        """测试扫描媒体文件夹"""
        (tmp_path / "img1.jpg").write_bytes(b"fake image")
        (tmp_path / "img2.png").write_bytes(b"fake image")
        (tmp_path / "vid.mp4").write_bytes(b"fake video")
        (tmp_path / "raw.CR2").write_bytes(b"fake raw")
        (tmp_path / "doc.txt").write_text("not media")

        files = import_service.scan_media_folder(str(tmp_path))
        file_names = [f.name for f in files]

        assert "img1.jpg" in file_names
        assert "img2.png" in file_names
        assert "vid.mp4" in file_names
        assert "raw.CR2" in file_names
        assert "doc.txt" not in file_names
        assert len(files) == 4

    def test_scan_non_recursive(self, import_service, tmp_path):
        """测试非递归扫描"""
        (tmp_path / "top.jpg").write_bytes(b"top")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.jpg").write_bytes(b"nested")

        files = import_service.scan_media_folder(str(tmp_path), recursive=False)
        assert len(files) == 1
        assert files[0].name == "top.jpg"

    def test_scan_filter_by_type(self, import_service, tmp_path):
        """测试按类型过滤扫描"""
        (tmp_path / "img.jpg").write_bytes(b"img")
        (tmp_path / "vid.mp4").write_bytes(b"vid")
        (tmp_path / "raw.cr2").write_bytes(b"raw")

        images_only = import_service.scan_media_folder(
            str(tmp_path), include_images=True, include_videos=False, include_raw=False
        )
        assert len(images_only) == 1
        assert images_only[0].name == "img.jpg"

        videos_only = import_service.scan_media_folder(
            str(tmp_path), include_images=False, include_videos=True, include_raw=False
        )
        assert len(videos_only) == 1

        raw_only = import_service.scan_media_folder(
            str(tmp_path), include_images=False, include_videos=False, include_raw=True
        )
        assert len(raw_only) == 1

    def test_get_file_info(self, import_service, tmp_path):
        """测试获取文件信息"""
        f = tmp_path / "test.jpg"
        f.write_bytes(b"hello world")

        info = import_service.get_file_info(str(f))
        assert info["name"] == "test.jpg"
        assert info["size"] == 11
        assert info["suffix"] == ".jpg"
        assert info["asset_type"] == "image"

    def test_import_assets_reference_mode(self, import_service, temp_db, tmp_path):
        """测试引用模式导入（不动原文件）"""
        db_service, _ = temp_db

        project = db_service.create_project("测试项目")

        f1 = tmp_path / "img1.jpg"
        f1.write_bytes(b"image1 data")
        f2 = tmp_path / "img2.jpg"
        f2.write_bytes(b"image2 data")

        result = import_service.import_assets(
            project.project_id,
            [str(f1), str(f2)],
            compute_checksum=False
        )

        assert result["total"] == 2
        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert result["failed"] == 0

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 2
        assert assets[0].is_working_copy is False
        assert assets[0].original_path == ""
        asset_paths = [a.file_path for a in assets]
        assert str(f1.resolve()) in asset_paths
        assert str(f2.resolve()) in asset_paths

        assert f1.exists()
        assert f2.exists()

    def test_import_assets_copy_mode(self, import_service, temp_db, tmp_path):
        """测试复制模式导入（复制到工作区）"""
        db_service, _ = temp_db

        project = db_service.create_project("测试项目")

        f1 = tmp_path / "original.jpg"
        f1.write_bytes(b"original data")
        workspace = tmp_path / "workspace"

        result = import_service.import_assets(
            project.project_id,
            [str(f1)],
            compute_checksum=False,
            copy_to_workspace=True,
            workspace_dir=str(workspace)
        )

        assert result["imported"] == 1

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1
        assert assets[0].is_working_copy is True
        assert assets[0].original_path == str(f1)
        assert str(workspace) in assets[0].file_path

        assert f1.exists()
        assert Path(assets[0].file_path).exists()

    def test_copy_to_workspace_does_not_nest_project_name(self, temp_db, tmp_path):
        """回归：复制目标目录已由调用方含项目名，服务不再追加，避免 <ws>/<项目>/<项目>/file"""
        db_service, _ = temp_db
        svc = MediaImportService(db_service=db_service)
        src = tmp_path / "src"
        src.mkdir()
        f = src / "a.cr2"
        f.write_bytes(b"x")
        # 调用方构造的「完整目标目录」（已含项目名子文件夹）
        target = tmp_path / "WS" / "项目A"
        dest = svc._copy_to_workspace(Path(f), str(target))
        assert dest == target / "a.cr2"
        assert dest.exists()
        # 不应出现 <WS>/项目A/项目A/a.cr2 的嵌套
        assert not (target / "项目A").exists()

    def test_import_assets_copy_uses_given_workspace_dir(self, temp_db, tmp_path):
        """集成：import_assets 复制到调用方指定的完整目录，不再拼接项目名"""
        db_service, _ = temp_db
        svc = MediaImportService(db_service=db_service)
        project = db_service.create_project(name="项目A")
        src = tmp_path / "src"
        src.mkdir()
        f = src / "a.cr2"
        f.write_bytes(b"x")
        # 调用方已拼接好 <ws.path>/<项目名>
        workspace_dir = str(tmp_path / "WS" / "项目A")
        result = svc.import_assets(
            project.project_id, [str(f)], compute_checksum=False,
            copy_to_workspace=True, workspace_dir=workspace_dir,
        )
        assert result["imported"] == 1
        asset = db_service.get_media_assets(project.project_id)[0]
        assert asset.file_path == str(Path(workspace_dir) / "a.cr2")
        # 不存在重复嵌套的项目名目录
        assert not (tmp_path / "WS" / "项目A" / "项目A").exists()

    def test_import_skip_duplicates(self, import_service, temp_db, tmp_path):
        """测试导入时跳过重复文件"""
        db_service, _ = temp_db

        project = db_service.create_project("测试项目")
        f1 = tmp_path / "img.jpg"
        f1.write_bytes(b"test data")

        result1 = import_service.import_assets(
            project.project_id, [str(f1)], compute_checksum=False
        )
        assert result1["imported"] == 1

        result2 = import_service.import_assets(
            project.project_id, [str(f1)], compute_checksum=False
        )
        assert result2["imported"] == 0
        assert result2["skipped"] == 1

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1

    def test_import_nonexistent_file(self, import_service, temp_db, tmp_path):
        """测试导入不存在的文件"""
        db_service, _ = temp_db
        project = db_service.create_project("测试项目")

        result = import_service.import_assets(
            project.project_id,
            ["/nonexistent/path.jpg"],
            compute_checksum=False
        )

        assert result["total"] == 1
        assert result["skipped"] == 1
        assert result["imported"] == 0

    def test_copy_asset_to_workspace(self, import_service, temp_db, tmp_path):
        """测试将已有素材复制为工作副本"""
        db_service, _ = temp_db

        project = db_service.create_project("测试项目")
        f1 = tmp_path / "original.jpg"
        f1.write_bytes(b"original data")

        import_result = import_service.import_assets(
            project.project_id, [str(f1)], compute_checksum=False
        )
        asset_id = import_result["details"][0]["asset_id"]

        workspace = tmp_path / "workspace"
        result = import_service.copy_asset_to_workspace(asset_id, str(workspace))

        assert result is not None
        assert result.is_working_copy is True
        assert result.original_path == str(f1)
        assert f1.exists()
        assert Path(result.file_path).exists()

    def test_get_supported_extensions(self, import_service):
        """测试获取支持的扩展名"""
        exts = import_service.get_supported_extensions()
        assert "image" in exts
        assert "video" in exts
        assert "raw" in exts
        assert "audio" in exts
        assert ".jpg" in exts["image"]
        assert ".mp4" in exts["video"]

    def test_import_cancel_midway(self, import_service, temp_db, tmp_path):
        """测试导入过程中取消 - 操作链不应断裂"""
        db_service, _ = temp_db
        project = db_service.create_project("取消测试")

        files = []
        for i in range(20):
            f = tmp_path / f"img_{i:02d}.jpg"
            f.write_bytes(b"data" * 100)
            files.append(str(f))

        # 在第 3 次检查后触发取消
        counter = {"n": 0}

        def cancel_check():
            counter["n"] += 1
            return counter["n"] >= 3

        result = import_service.import_assets(
            project.project_id, files,
            compute_checksum=False,
            cancel_check=cancel_check
        )

        assert result["cancelled"] is True
        assert result["imported"] < len(files)
        # 已导入的应正确落库
        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == result["imported"]

    def test_import_cancel_immediately(self, import_service, temp_db, tmp_path):
        """测试立即取消 - 不应导入任何文件"""
        db_service, _ = temp_db
        project = db_service.create_project("立即取消测试")

        files = []
        for i in range(5):
            f = tmp_path / f"img_{i}.jpg"
            f.write_bytes(b"data")
            files.append(str(f))

        result = import_service.import_assets(
            project.project_id, files,
            compute_checksum=False,
            cancel_check=lambda: True  # 立即取消
        )

        assert result["cancelled"] is True
        assert result["imported"] == 0
        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 0

    def test_import_metadata_fields_persisted(self, import_service, temp_db, tmp_path):
        """测试元数据字段落盘 - 即使读取失败也应有默认值"""
        db_service, _ = temp_db
        project = db_service.create_project("元数据落盘测试")

        f = tmp_path / "meta.jpg"
        f.write_bytes(b"fake image without exif")

        result = import_service.import_assets(
            project.project_id, [str(f)],
            compute_checksum=False,
            read_metadata=True
        )
        assert result["imported"] == 1

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1
        asset = assets[0]

        # 元数据字段必须存在（不能缺失列）
        assert hasattr(asset, "width")
        assert hasattr(asset, "height")
        assert hasattr(asset, "lens_model")
        assert hasattr(asset, "focal_length")
        assert hasattr(asset, "duration_seconds")
        assert hasattr(asset, "video_metadata")

        # 默认值应正确落盘
        assert asset.asset_type == "image"
        assert asset.duration_seconds == 0.0
        assert asset.video_metadata == ""
        assert asset.lens_model == ""
        assert asset.focal_length == ""

    def test_import_without_metadata(self, import_service, temp_db, tmp_path):
        """测试关闭元数据读取 - 字段仍应有默认值"""
        db_service, _ = temp_db
        project = db_service.create_project("关闭元数据测试")

        f = tmp_path / "no_meta.jpg"
        f.write_bytes(b"data")

        result = import_service.import_assets(
            project.project_id, [str(f)],
            compute_checksum=False,
            read_metadata=False
        )
        assert result["imported"] == 1

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1
        asset = assets[0]
        assert asset.width == 0
        assert asset.height == 0
        assert asset.lens_model == ""

    def test_import_progress_callback(self, import_service, temp_db, tmp_path):
        """测试进度回调被正确调用"""
        db_service, _ = temp_db
        project = db_service.create_project("进度回调测试")

        files = []
        for i in range(5):
            f = tmp_path / f"p_{i}.jpg"
            f.write_bytes(b"data")
            files.append(str(f))

        progress_calls = []

        def cb(target, progress, msg):
            progress_calls.append((target, progress, msg))

        result = import_service.import_assets(
            project.project_id, files,
            compute_checksum=False,
            progress_callback=cb
        )

        assert result["imported"] == 5
        assert len(progress_calls) == 5
        # 第一个回调 target 为 import
        assert progress_calls[0][0] == "import"
        # 最后一个进度应为 1.0
        assert progress_calls[-1][1] == 1.0
        # 进度应递增
        progresses = [p[1] for p in progress_calls]
        assert progresses == sorted(progresses)

    def test_import_result_has_cancelled_field(self, import_service, temp_db, tmp_path):
        """测试导入结果包含 cancelled 字段（操作链完整性）"""
        db_service, _ = temp_db
        project = db_service.create_project("结果字段测试")

        f = tmp_path / "img.jpg"
        f.write_bytes(b"data")

        result = import_service.import_assets(
            project.project_id, [str(f)],
            compute_checksum=False
        )

        # 结果应包含 cancelled 字段（即使未取消）
        assert "cancelled" in result
        assert result["cancelled"] is False
        assert "details" in result
        assert len(result["details"]) == 1
        assert result["details"][0]["status"] == "imported"
        assert "asset_id" in result["details"][0]

    def test_import_with_log_id_and_scene_shot(self, import_service, temp_db, tmp_path):
        """测试导入时关联拍摄日志并填充 scene/shot"""
        db_service, _ = temp_db
        project = db_service.create_project("关联日志导入测试")

        log = ShootingLog(
            log_id="log_import_001",
            project_id=project.project_id,
            scene="S100",
            shot="005B",
            take="02"
        )
        db_service.create_shooting_log(log)

        f = tmp_path / "img_with_log.jpg"
        f.write_bytes(b"test data")

        result = import_service.import_assets(
            project.project_id, [str(f)],
            compute_checksum=False,
            log_id="log_import_001",
            scene="S100",
            shot="005B"
        )
        assert result["imported"] == 1

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1
        asset = assets[0]
        assert asset.log_id == "log_import_001"
        assert asset.scene == "S100"
        assert asset.shot == "005B"

    def test_import_without_log_id_defaults_empty(self, import_service, temp_db, tmp_path):
        """测试不带 log_id 导入时默认值正确（向后兼容）"""
        db_service, _ = temp_db
        project = db_service.create_project("默认值测试")

        f = tmp_path / "no_log.jpg"
        f.write_bytes(b"data")

        result = import_service.import_assets(
            project.project_id, [str(f)],
            compute_checksum=False
        )
        assert result["imported"] == 1

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1
        asset = assets[0]
        assert asset.log_id is None
        assert asset.scene == ""
        assert asset.shot == ""

    def test_import_with_scene_shot_no_log_id(self, import_service, temp_db, tmp_path):
        """测试只填 scene/shot 不关联 log_id"""
        db_service, _ = temp_db
        project = db_service.create_project("仅场景测试")

        f = tmp_path / "scene_only.jpg"
        f.write_bytes(b"data")

        result = import_service.import_assets(
            project.project_id, [str(f)],
            compute_checksum=False,
            scene="S200",
            shot="010C"
        )
        assert result["imported"] == 1

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1
        asset = assets[0]
        assert asset.log_id is None
        assert asset.scene == "S200"
        assert asset.shot == "010C"

    def test_import_then_search_by_log_id(self, import_service, temp_db, tmp_path):
        """测试导入关联日志后，可通过 log_id 搜索到素材（操作链条端验证）"""
        db_service, _ = temp_db
        project = db_service.create_project("端到端测试")

        log = ShootingLog(
            log_id="log_e2e_001",
            project_id=project.project_id,
            scene="S001",
            shot="001A",
            take="01"
        )
        db_service.create_shooting_log(log)

        files = []
        for i in range(3):
            f = tmp_path / f"e2e_{i}.jpg"
            f.write_bytes(b"data")
            files.append(str(f))

        # 2 个关联日志，1 个不关联
        import_service.import_assets(
            project.project_id, files[:2],
            compute_checksum=False,
            log_id="log_e2e_001",
            scene="S001",
            shot="001A"
        )
        import_service.import_assets(
            project.project_id, files[2:],
            compute_checksum=False
        )

        # 按 log_id 搜索
        log_assets = db_service.search_assets(project_id=project.project_id, log_id="log_e2e_001")
        assert len(log_assets) == 2
        for a in log_assets:
            assert a.log_id == "log_e2e_001"
            assert a.scene == "S001"
            assert a.shot == "001A"

        # 按 scene 搜索
        scene_assets = db_service.search_assets(project_id=project.project_id, scene="S001")
        assert len(scene_assets) == 2

        # 全部素材
        all_assets = db_service.get_media_assets(project.project_id)
        assert len(all_assets) == 3

    def test_import_duplicate_paths_in_batch(self, import_service, temp_db, tmp_path):
        """同批重复路径只导入一次（并发占位去重）"""
        db_service, _ = temp_db
        project = db_service.create_project("批内去重测试")
        f = tmp_path / "dup.jpg"
        f.write_bytes(b"data")

        result = import_service.import_assets(
            project.project_id, [str(f), str(f)],
            compute_checksum=False, read_metadata=False,
        )
        assert result["imported"] == 1
        assert result["skipped"] == 1
        assert len(db_service.get_media_assets(project.project_id)) == 1

    def test_import_parallel_many_files(self, import_service, temp_db, tmp_path):
        """并行导入：多文件全部入库且校验和已计算"""
        db_service, _ = temp_db
        project = db_service.create_project("并行导入测试")
        files = []
        for i in range(20):
            f = tmp_path / f"f_{i:02d}.jpg"
            f.write_bytes(os.urandom(1024))
            files.append(str(f))

        result = import_service.import_assets(
            project.project_id, files,
            compute_checksum=True, read_metadata=False,
        )
        assert result["imported"] == 20
        assert result["failed"] == 0

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 20
        assert all(a.checksum_value for a in assets)

    def test_import_copy_mode_unique_names(self, import_service, temp_db, tmp_path):
        """复制模式：不同目录下同名文件并发导入应生成唯一文件名"""
        db_service, _ = temp_db
        project = db_service.create_project("唯一命名测试")
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        f1 = d1 / "same.jpg"
        f2 = d2 / "same.jpg"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        ws = tmp_path / "ws"

        result = import_service.import_assets(
            project.project_id,
            [str(f1), str(f2)],
            compute_checksum=False, read_metadata=False,
            copy_to_workspace=True, workspace_dir=str(ws),
        )
        assert result["imported"] == 2
        names = {a.file_name for a in db_service.get_media_assets(project.project_id)}
        assert "same.jpg" in names
        assert len(names) == 2  # 第二个同名文件自动唯一命名
