"""阶段 4 项目健康汇总测试。"""
from datetime import timedelta

from DITWorkstation.Models import MediaAsset
from DITWorkstation.Services.project_health_service import ProjectHealthService
from DITWorkstation.Utils import now_local


def test_health_report_aggregates_missing_tasks_and_capacity(db_service, tmp_dir):
    project = db_service.create_project(name="健康项目")
    db_service.add_media_asset(MediaAsset(
        asset_id="missing", project_id=project.project_id, file_path="/not/available.mov",
        file_name="missing.mov", file_size=10, file_type=".mov",
    ))
    task_id = db_service.create_task_history("导入素材", project.project_id)
    db_service.update_task_history(task_id, "recoverable", error_summary="介质已移除")

    report = ProjectHealthService(db_service).get_health_report(project.project_id)
    assert report["issues"]["missing_assets"] == 1
    assert report["issues"]["unbacked_assets"] == 1
    assert report["issues"]["failed_tasks"] == 1
    assert report["severity"] == "warning"


def test_health_report_records_target_capacity(db_service, tmp_dir):
    project = db_service.create_project(name="容量项目")
    from DITWorkstation.Services.backup_service import BackupService
    src = tmp_dir / "src"
    target = tmp_dir / "target"
    src.mkdir()
    target.mkdir()
    (src / "a.dat").write_bytes(b"content")
    service = BackupService(db_service=db_service)
    service.execute_backup(service.create_backup_job(str(src), [str(target)]), project_id=project.project_id)

    report = ProjectHealthService(db_service).get_health_report(project.project_id)
    assert report["capacities"][0]["available"] is True
    assert report["capacity_history"]


def test_capacity_forecast_detects_low_space_and_depletion():
    start = now_local() - timedelta(days=2)
    snapshots = [
        {
            "target_path": "/backup-a", "total_bytes": 1000, "free_bytes": 500,
            "captured_at": start,
        },
        {
            "target_path": "/backup-a", "total_bytes": 1000, "free_bytes": 100,
            "captured_at": start + timedelta(days=2),
        },
    ]

    forecast = ProjectHealthService.estimate_capacity_forecast(snapshots)

    assert forecast["warning"] is True
    assert forecast["days_remaining"] == 1
    assert forecast["targets"][0]["free_ratio"] == 0.1
