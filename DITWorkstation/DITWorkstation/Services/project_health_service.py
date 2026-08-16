"""项目健康汇总服务。"""
import shutil
from pathlib import Path


class ProjectHealthService:
    """聚合备份、任务、路径和容量风险，供看板及自动策略复用。"""

    def __init__(self, db_service):
        self.db_service = db_service

    def get_health_report(self, project_id: str, capture_capacity: bool = True) -> dict:
        stats = self.db_service.get_project_stats(project_id)
        jobs = self.db_service.get_backup_jobs(project_id)
        missing_asset_ids = self.db_service.get_missing_file_asset_ids(project_id)
        tasks = self.db_service.get_task_history(project_id, limit=100)
        recycle_items = self.db_service.get_recycle_bin_items(project_id)

        failed_tasks = [task for task in tasks if task.get("state") in {"failed", "recoverable", "cancelled"}]
        retry_files = sum(
            len(target.get("failed_files", []))
            for job in jobs for target in job.get("targets", [])
        )
        target_paths = sorted({
            target.get("path", "") for job in jobs for target in job.get("targets", [])
            if target.get("path")
        })
        capacities = []
        for target_path in target_paths:
            try:
                usage = shutil.disk_usage(Path(target_path))
                entry = {
                    "path": target_path, "total_bytes": usage.total, "free_bytes": usage.free,
                    "used_percent": round((usage.used / usage.total * 100) if usage.total else 0, 1),
                    "available": True,
                }
                capacities.append(entry)
                if capture_capacity:
                    self.db_service.record_storage_health_snapshot(
                        project_id, target_path, usage.total, usage.free,
                    )
            except OSError as exc:
                capacities.append({
                    "path": target_path, "total_bytes": 0, "free_bytes": 0,
                    "used_percent": None, "available": False, "error": str(exc),
                })

        recent_checks = self.db_service.get_recent_operations(
            limit=1, project_id=project_id, event="备份校验",
        )
        issues = {
            "unbacked_assets": max(0, stats["asset_count"] - stats["backed_up_count"]),
            "missing_assets": len(missing_asset_ids),
            "failed_tasks": len(failed_tasks),
            "retry_files": retry_files,
            "unavailable_targets": sum(1 for item in capacities if not item["available"]),
        }
        severity = "healthy" if not any(issues.values()) else "attention"
        if issues["missing_assets"] or issues["retry_files"] or issues["failed_tasks"]:
            severity = "warning"
        return {
            "project_id": project_id,
            "severity": severity,
            "stats": stats,
            "issues": issues,
            "failed_task_records": failed_tasks,
            "recycle_items": len(recycle_items),
            "last_integrity_check": recent_checks[0] if recent_checks else None,
            "capacities": capacities,
            "capacity_history": self.db_service.get_storage_health_snapshots(project_id),
        }
