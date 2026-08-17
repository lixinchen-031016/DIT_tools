"""Repository integration tests sharing the database facade's storage."""
from datetime import datetime

from DITWorkstation.Models import BackupJob, MediaAsset, ShootingLog


def test_workspace_repository_and_database_facade_share_storage(db_service):
    workspace = db_service.workspaces.create("仓储工作区", "/workspace")

    assert db_service.get_workspace(workspace.workspace_id) == workspace
    assert db_service.workspaces.update(workspace.workspace_id, description="已更新")
    assert db_service.get_workspace(workspace.workspace_id).description == "已更新"


def test_workspace_repository_default_workspace_is_idempotent(db_service):
    first = db_service.workspaces.get_or_create_default()
    second = db_service.get_or_create_default_workspace()

    assert first.workspace_id == "default"
    assert second == first


def test_template_repository_and_database_facade_share_storage(db_service):
    template = db_service.templates.create_project("仓储模板", base_path="work")

    assert db_service.get_project_template(template.template_id) == template
    assert db_service.templates.update_project(template.template_id, notes="更新")
    assert db_service.get_project_template(template.template_id).notes == "更新"


def test_project_repository_and_database_facade_share_storage(db_service):
    project = db_service.projects.create("仓储项目", description="待更新")

    assert db_service.get_project(project.project_id) == project
    assert db_service.projects.update_result(project.project_id, name="已更新")
    assert db_service.get_project(project.project_id).name == "已更新"


def test_log_repository_and_database_facade_share_storage(db_service):
    project = db_service.create_project("日志仓储项目")
    log = ShootingLog("repo-log", project.project_id, "S001", "001A", "01")

    assert db_service.logs.create_shooting(log) == log
    assert db_service.get_shooting_log(log.log_id) == log
    assert db_service.logs.record("仓储测试", project_id=project.project_id)
    assert db_service.get_recent_operations(project_id=project.project_id)[0]["event"] == "仓储测试"


def test_asset_repository_read_paths_and_facade_share_storage(db_service):
    project = db_service.create_project("素材仓储项目")
    imported_at = datetime(2026, 1, 1, 12, 0, 0)
    assets = [
        MediaAsset(
            asset_id=asset_id,
            project_id=project.project_id,
            file_path=f"/media/{asset_id}.mov",
            file_name=f"{asset_id}.mov",
            date_imported=imported_at,
        )
        for asset_id in ("asset-a", "asset-b", "asset-c")
    ]
    db_service.assets.create(assets[0])
    db_service.add_media_asset(assets[1])
    assert db_service.assets.create_batch([assets[2]]) == 1
    assert db_service.assets.update_result("asset-b", tags="夜戏")

    assert db_service.assets.get("asset-b") == db_service.get_media_asset("asset-b")
    assert db_service.get_media_asset("asset-b").tags == "夜戏"
    assert db_service.assets.count_project(project.project_id) == 3
    assert [asset.asset_id for asset in db_service.assets.iter_project(project.project_id, 1)] == [
        "asset-c", "asset-b", "asset-a",
    ]
    first, cursor = db_service.assets.get_page(project.project_id, page_size=2)
    second, final_cursor = db_service.get_project_asset_page(
        project.project_id, page_size=2, cursor=cursor,
    )
    assert [asset.asset_id for asset in first + second] == ["asset-c", "asset-b", "asset-a"]
    assert final_cursor is None


def test_lifecycle_and_operation_repositories_share_facade_storage(db_service):
    project = db_service.create_project("生命周期仓储项目")
    asset = MediaAsset("repo-lifecycle", project.project_id, "/media/clip.mov", "clip.mov")
    db_service.assets.create(asset)

    deleted = db_service.assets.delete_result([asset.asset_id])
    assert deleted
    assert db_service.recycle.list_items(project.project_id)[0]["recycle_id"] == deleted.recovery_id
    assert db_service.recycle.restore_item(deleted.recovery_id)
    assert db_service.get_media_asset(asset.asset_id) is not None

    rename = db_service.renames.create([("/media/clip.mov", "/media/clip-v2.mov")], project.project_id)
    assert db_service.get_rename_history(rename.recovery_id)

    task_id = db_service.tasks.create("仓储任务", project.project_id)
    assert db_service.update_task_history(task_id, "completed")
    assert db_service.get_task_history(project.project_id)[0]["task_id"] == task_id

    job = BackupJob("repo-job", "/media")
    assert db_service.backup_jobs.save(job, project.project_id)
    assert db_service.get_backup_job(job.job_id)["project_id"] == project.project_id
