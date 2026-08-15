"""阶段 1：可恢复性服务测试。"""
from datetime import datetime, timedelta
from pathlib import Path

from DITWorkstation.Models import MediaAsset, OperationStatus, RenameRule, ShootingLog
from DITWorkstation.Services.asset_relink_service import AssetRelinkService
from DITWorkstation.Services.rename_service import RenameService
from DITWorkstation.Utils import normalize_path


def _asset(project_id, asset_id, path, size=4, tags="日戏,主镜头", log_id=None):
    return MediaAsset(
        asset_id=asset_id,
        project_id=project_id,
        file_path=normalize_path(str(path)),
        file_name=Path(path).name,
        file_size=size,
        file_type=Path(path).suffix or ".cr3",
        tags=tags,
        log_id=log_id,
    )


def test_operation_result_distinguishes_invalid_and_not_found(db_service):
    invalid = db_service.update_media_asset_result("missing", unknown_field="value")
    missing = db_service.delete_media_asset_result("missing")

    assert invalid.status is OperationStatus.INVALID
    assert missing.status is OperationStatus.NOT_FOUND
    assert not invalid and not missing


def test_asset_soft_delete_and_restore_preserves_tags_and_audit(db_service, project, tmp_path):
    path = tmp_path / "asset.cr3"
    path.write_bytes(b"data")
    db_service.add_media_asset(_asset(project.project_id, "asset-1", path))

    deleted = db_service.delete_media_asset_result("asset-1")
    assert deleted.status is OperationStatus.SUCCESS
    assert db_service.get_media_asset("asset-1") is None
    assert len(db_service.get_recycle_bin_items(project.project_id)) == 1

    restored = db_service.restore_recycle_item(deleted.recovery_id)
    asset = db_service.get_media_asset("asset-1")
    assert restored.status is OperationStatus.SUCCESS
    assert asset is not None and asset.tags == "日戏,主镜头"
    operations = db_service.get_recent_operations(project_id=project.project_id)
    assert any(item["event"] == "恢复回收站项目" for item in operations)


def test_project_soft_delete_and_restore_preserves_assets_and_logs(db_service, project, tmp_path):
    log = ShootingLog("log-1", project.project_id, "S001", "001A", "01")
    db_service.create_shooting_log(log)
    path = tmp_path / "project_asset.cr3"
    path.write_bytes(b"data")
    db_service.add_media_asset(_asset(project.project_id, "asset-2", path, log_id=log.log_id))

    deleted = db_service.delete_project_result(project.project_id)
    assert deleted.status is OperationStatus.SUCCESS
    assert db_service.get_project(project.project_id) is None

    restored = db_service.restore_recycle_item(deleted.recovery_id)
    assert restored.status is OperationStatus.SUCCESS
    assert db_service.get_project(project.project_id) is not None
    assert db_service.get_media_asset("asset-2").log_id == log.log_id
    assert len(db_service.get_shooting_logs(project.project_id)) == 1


def test_recycle_bin_expiration_cleanup(db_service, project, tmp_path):
    path = tmp_path / "expired.cr3"
    path.write_bytes(b"data")
    db_service.add_media_asset(_asset(project.project_id, "asset-3", path))
    deleted = db_service.delete_media_asset_result("asset-3", retention_days=0)
    cleaned = db_service.cleanup_expired_recycle_bin(datetime.now() + timedelta(seconds=1))

    assert deleted
    assert cleaned.affected_count == 1
    assert db_service.restore_recycle_item(deleted.recovery_id).status is OperationStatus.NOT_FOUND


def test_restore_all_recycle_items_restores_project_before_assets(db_service, project, tmp_path):
    asset = _asset(project.project_id, "restore-all-asset", tmp_path / "restore-all.cr3")
    db_service.add_media_asset(asset)
    assert db_service.delete_media_asset_result(asset.asset_id)
    assert db_service.delete_project_result(project.project_id)

    restored = db_service.restore_all_recycle_items()

    assert restored.status is OperationStatus.SUCCESS
    assert restored.affected_count == 2
    assert db_service.get_project(project.project_id) is not None
    assert db_service.get_media_asset(asset.asset_id) is not None
    assert db_service.get_recycle_bin_items() == []


def test_empty_recycle_bin_permanently_removes_all_snapshots(db_service, project, tmp_path):
    asset = _asset(project.project_id, "empty-bin-asset", tmp_path / "empty-bin.cr3")
    db_service.add_media_asset(asset)
    assert db_service.delete_media_asset_result(asset.asset_id)

    cleared = db_service.empty_recycle_bin()

    assert cleared.status is OperationStatus.SUCCESS
    assert cleared.affected_count == 1
    assert db_service.get_recycle_bin_items() == []


def test_relink_preview_uses_relative_path_and_keeps_conflicts_unapplied(db_service, project, tmp_path):
    new_root = tmp_path / "new-root"
    matched = new_root / "A" / "clip.cr3"
    matched.parent.mkdir(parents=True)
    matched.write_bytes(b"data")
    duplicate_a = new_root / "one" / "duplicate.cr3"
    duplicate_b = new_root / "two" / "duplicate.cr3"
    duplicate_a.parent.mkdir(parents=True)
    duplicate_b.parent.mkdir(parents=True)
    duplicate_a.write_bytes(b"data")
    duplicate_b.write_bytes(b"data")
    db_service.add_media_assets_batch([
        _asset(project.project_id, "match", "/legacy/A/clip.cr3"),
        _asset(project.project_id, "conflict", "/legacy/B/duplicate.cr3"),
    ])

    service = AssetRelinkService(db_service)
    preview = service.preview(project.project_id, str(new_root), old_root="/legacy")
    results = {item.asset_id: item for item in preview.matches}
    assert results["match"].match_method == "relative_path"
    assert results["match"].selected_path == normalize_path(str(matched))
    assert results["conflict"].is_conflict

    applied = service.apply(preview)
    assert applied.status is OperationStatus.SUCCESS
    assert db_service.get_media_asset("match").file_path == normalize_path(str(matched))
    assert db_service.get_media_asset("conflict").file_path == normalize_path("/legacy/B/duplicate.cr3")


def test_rename_rollback_requires_recorded_unmodified_files(db_service, project, tmp_path):
    old = tmp_path / "original.cr3"
    old.write_bytes(b"data")
    db_service.add_media_asset(_asset(project.project_id, "rename-asset", old))
    service = RenameService()
    renamed = service.execute_rename([str(old)], RenameRule(pattern="renamed_{number}"))
    old_path, new_path = renamed[0]
    assert db_service.update_asset_path_by_old_path(old_path, new_path, Path(new_path).name)
    history = db_service.create_rename_history(renamed, project.project_id)

    result = service.rollback_rename(db_service, history.recovery_id)
    assert result.status is OperationStatus.SUCCESS
    assert Path(old_path).is_file() and not Path(new_path).exists()
    assert db_service.get_media_asset("rename-asset").file_path == normalize_path(old_path)
    assert service.rollback_rename(db_service, history.recovery_id).status is OperationStatus.CONFLICT
