"""项目健康汇总服务。"""
import math
import shutil
from datetime import datetime
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

        capacity_history = self.db_service.get_storage_health_snapshots(project_id)
        capacity_forecast = self.estimate_capacity_forecast(capacity_history)

        recent_checks = self.db_service.get_recent_operations(
            limit=1, project_id=project_id, event="备份校验",
        )
        issues = {
            "unbacked_assets": max(0, stats["asset_count"] - stats["backed_up_count"]),
            "missing_assets": len(missing_asset_ids),
            "failed_tasks": len(failed_tasks),
            "retry_files": retry_files,
            "unavailable_targets": sum(1 for item in capacities if not item["available"]),
            "capacity_warning": bool(capacity_forecast["warning"]),
        }
        severity = "healthy" if not any(issues.values()) else "attention"
        if (
            issues["missing_assets"] or issues["retry_files"] or issues["failed_tasks"]
            or issues["capacity_warning"]
        ):
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
            "capacity_history": capacity_history,
            "capacity_forecast": capacity_forecast,
        }

    @staticmethod
    def estimate_capacity_forecast(
        snapshots: list[dict], *, warning_ratio: float = 0.15,
    ) -> dict:
        """根据容量快照估算最先耗尽的目标，供 UI 和自动策略复用。

        仅使用同一目标的首尾快照；快照不足两条或容量没有下降时不做天数猜测。
        ``warning`` 同时覆盖当前低于阈值和预计在 7 天内耗尽两种情况。
        """
        warning_ratio = min(0.95, max(0.01, float(warning_ratio)))
        by_target: dict[str, list[dict]] = {}
        for item in snapshots:
            path = str(item.get("target_path") or "")
            if path:
                by_target.setdefault(path, []).append(item)

        forecasts = []
        for path, entries in by_target.items():
            entries = sorted(entries, key=lambda item: item.get("captured_at") or datetime.min)
            latest = entries[-1]
            total = max(0, int(latest.get("total_bytes") or 0))
            free = max(0, int(latest.get("free_bytes") or 0))
            days_remaining = None
            if len(entries) >= 2:
                first = entries[0]
                first_free = int(first.get("free_bytes") or 0)
                first_time = first.get("captured_at")
                latest_time = latest.get("captured_at")
                if isinstance(first_time, datetime) and isinstance(latest_time, datetime):
                    elapsed_days = (latest_time - first_time).total_seconds() / 86400
                    daily_loss = (first_free - free) / elapsed_days if elapsed_days > 0 else 0
                    if daily_loss > 0:
                        days_remaining = max(0, math.ceil(free / daily_loss))
            forecasts.append({
                "path": path,
                "free_bytes": free,
                "total_bytes": total,
                "free_ratio": (free / total) if total else None,
                "days_remaining": days_remaining,
            })

        forecasts.sort(key=lambda item: (
            item["days_remaining"] is None,
            item["days_remaining"] if item["days_remaining"] is not None else float("inf"),
        ))
        urgent = [item for item in forecasts if (
            item["free_ratio"] is not None and item["free_ratio"] <= warning_ratio
        ) or (
            item["days_remaining"] is not None and item["days_remaining"] <= 7
        )]
        return {
            "targets": forecasts,
            "warning": bool(urgent),
            "warning_ratio": warning_ratio,
            "days_remaining": urgent[0]["days_remaining"] if urgent else None,
        }
