"""项目归档/恢复服务测试"""
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.archive_service import ArchiveService
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Models import MediaAsset, ShootingLog


def _make_project_with_data(db_service, src_dir):
    """造一个含 2 素材 + 1 日志的项目，素材文件真实存在"""
    project = db_service.create_project(name="归档项目", description="归档测试")

    log = ShootingLog(
        log_id=str(uuid.uuid4())[:8],
        project_id=project.project_id,
        scene="S01",
        shot="001A",
        take="01",
        camera="RED V-RAPTOR",
    )
    db_service.create_shooting_log(log)

    assets = []
    checksum_svc = ChecksumService()
    for i in range(2):
        p = src_dir / f"IMG_{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(1024))
        cached = checksum_svc.compute_file_checksum(str(p))
        assets.append(MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project.project_id,
            file_path=str(p),
            file_name=p.name,
            file_size=p.stat().st_size,
            file_type=".jpg",
            asset_type="image",
            checksum_algorithm=cached.algorithm.value,
            checksum_value=cached.hash_value,
            scene="S01",
            shot="001A",
            log_id=log.log_id,
            backup_locations=["/tmp/backup"],
            tags="日戏,主镜头",
            notes="归档测试备注",
        ))
    db_service.add_media_assets_batch(assets)
    return project, log, assets


def test_archive_metadata_only(db_service, tmp_dir):
    """仅归档元数据：zip 内含 manifest/assets/logs，不含 files/"""
    src = tmp_dir / "media"
    src.mkdir()
    project, _, _ = _make_project_with_data(db_service, src)
    out = tmp_dir / "proj.zip"

    ArchiveService(db_service=db_service).archive_project(
        project.project_id, str(out), include_files=False
    )

    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "assets.json" in names
        assert "logs.json" in names
        assert not any(n.startswith("files/") for n in names)
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["version"] == 1
        assert manifest["project"]["name"] == "归档项目"
        assert manifest["stats"]["assets"] == 2
        assert manifest["stats"]["logs"] == 1


def test_archive_with_files(db_service, tmp_dir):
    """包含文件归档：files/ 下应有 2 个文件，checksums.txt 记录校验和"""
    src = tmp_dir / "media"
    src.mkdir()
    project, _, _ = _make_project_with_data(db_service, src)
    out = tmp_dir / "proj_full.zip"

    ArchiveService(db_service=db_service).archive_project(
        project.project_id, str(out), include_files=True
    )

    with zipfile.ZipFile(out) as zf:
        file_members = [n for n in zf.namelist() if n.startswith("files/")]
        assert len(file_members) == 2
        assert "checksums.txt" in zf.namelist()
        checksums = zf.read("checksums.txt").decode()
        assert "IMG_000.jpg" in checksums


def test_restore_project_metadata_only(db_service, tmp_dir):
    """恢复（不含文件）：新项目重建，素材/日志记录齐全"""
    src = tmp_dir / "media"
    src.mkdir()
    project, log, _ = _make_project_with_data(db_service, src)
    out = tmp_dir / "proj.zip"
    service = ArchiveService(db_service=db_service)
    service.archive_project(project.project_id, str(out), include_files=False)
    db_service.delete_project(project.project_id)  # 模拟原项目已清理后恢复

    result = service.restore_project(str(out), restore_files=False)
    new_project = result["project"]

    assert new_project.project_id != project.project_id
    assert new_project.name == "归档项目"
    assert result["restored_assets"] == 2
    assert result["restored_logs"] == 1

    logs = db_service.get_shooting_logs(new_project.project_id)
    assert len(logs) == 1
    assert logs[0].camera == "RED V-RAPTOR"
    assets = db_service.get_media_assets(new_project.project_id)
    assert len(assets) == 2
    # 日志-素材关联被保留（scene/shot 同步写入素材）
    assert all(a.log_id == logs[0].log_id for a in assets)
    assert all(a.scene == "S01" for a in assets)
    # 标签/备注随归档保留
    assert all(a.tags == "日戏,主镜头" for a in assets)
    assert all(a.notes == "归档测试备注" for a in assets)


def test_restore_project_with_files(db_service, tmp_dir):
    """恢复含文件归档：文件还原到指定目录，素材 file_path 指向还原文件"""
    src = tmp_dir / "media"
    src.mkdir()
    project, _, _ = _make_project_with_data(db_service, src)
    out = tmp_dir / "proj_full.zip"
    service = ArchiveService(db_service=db_service)
    service.archive_project(project.project_id, str(out), include_files=True)
    db_service.delete_project(project.project_id)

    restore_dir = tmp_dir / "restored"
    result = service.restore_project(
        str(out), restore_files=True, files_dest=str(restore_dir), verify=True
    )

    assert result["restored_files"] == 2
    assert result["missing_files"] == 0
    assert result["mismatches"] == []
    assets = db_service.get_media_assets(result["project"].project_id)
    assert len(assets) == 2
    for a in assets:
        assert a.file_path.startswith(str(restore_dir))
        assert Path(a.file_path).exists()


def test_restore_project_duplicate_name(db_service, tmp_dir):
    """目标工作区已有同名项目时，恢复项目自动追加时间戳后缀"""
    src = tmp_dir / "media"
    src.mkdir()
    project, _, _ = _make_project_with_data(db_service, src)
    out = tmp_dir / "proj.zip"
    service = ArchiveService(db_service=db_service)
    service.archive_project(project.project_id, str(out), include_files=False)

    # 先在目标工作区造一个同名项目
    db_service.create_project(name="归档项目")
    result = service.restore_project(str(out), restore_files=False)
    assert result["project"].name != "归档项目"
    assert "恢复" in result["project"].name


def test_restore_invalid_archive(tmp_dir):
    """无效归档包抛出 ValueError"""
    bogus = tmp_dir / "bogus.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("random.txt", "not an archive")
    with pytest.raises(ValueError):
        ArchiveService(db_service=object()).restore_project(str(bogus))


def test_restore_rejects_path_traversal_member(db_service, tmp_dir):
    """恢复归档拒绝 files/ 下的父目录跳转路径。"""
    archive = tmp_dir / "malicious.zip"
    manifest = {
        "version": 1,
        "project": {"name": "恶意归档", "created_at": "2026-01-01T00:00:00"},
    }
    assets = [{
        "asset_id": "asset-1", "project_id": "old", "file_path": "x.txt",
        "file_name": "x.txt", "file_size": 4, "file_type": ".txt",
        "checksum_algorithm": "xxhash64", "checksum_value": "",
        "date_imported": "2026-01-01T00:00:00", "archive_file": "files/../outside.txt",
    }]
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("assets.json", json.dumps(assets))
        zf.writestr("logs.json", "[]")
        zf.writestr("files/../outside.txt", b"evil")

    with pytest.raises(ValueError, match="非法归档成员路径"):
        ArchiveService(db_service=db_service).restore_project(
            str(archive), restore_files=True, files_dest=str(tmp_dir / "restore")
        )
    assert not (tmp_dir / "outside.txt").exists()


@pytest.mark.parametrize("member", [
    "files/C:/outside.txt",
    "files/C:\\outside.txt",
    "files/\\\\server\\share\\outside.txt",
])
def test_archive_path_validation_rejects_windows_absolute_paths(member):
    """在非 Windows 测试机上也拒绝 Windows 绝对路径语义。"""
    with pytest.raises(ValueError, match="非法归档成员路径"):
        ArchiveService._safe_archive_relative_path(member)


def test_restore_rejects_symlinked_destination_parent(tmp_dir):
    """还原目标目录已有外部符号链接时拒绝越界落盘。"""
    root = tmp_dir / "restore"
    root.mkdir()
    outside = tmp_dir / "outside"
    outside.mkdir()
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不支持创建符号链接")
    with pytest.raises(ValueError, match="越出目标目录"):
        ArchiveService._safe_restore_target(root, Path("linked/file.txt"))
