"""完整性校验调度：把已有备份的完整性校验包装为可配置周期的定时任务。

对应路线图 F3：定期完整性校验调度。
- ScheduledTaskService：轻量定时调度器（复用应用生命周期，单线程 trigger）
- IntegrityScheduler：把 backup_service.verify_backup 包装为定时任务并写审计日志
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from logging import getLogger

logger = getLogger(__name__)


@dataclass
class ScheduledTask:
    task_id: str
    func: Callable
    interval_seconds: int
    enabled: bool = True
    last_run: float = 0
    kwargs: dict = field(default_factory=dict)

    def is_due(self, now: float) -> bool:
        return self.enabled and (now - self.last_run) >= self.interval_seconds


class ScheduledTaskService:
    """轻量定时任务调度器。

    每个 tick 检查一次（默认 60 秒），到期的任务顺序执行（同步，短任务场景够用）。
    控件/服务生命周期由应用关闭时调用 stop() 终结。
    """

    def __init__(self, tick_seconds: float = 60.0):
        self._tasks: dict[str, ScheduledTask] = {}
        self._timer: threading.Timer | None = None
        self._running = False
        self._tick_seconds = max(1.0, float(tick_seconds))

    def register(
        self,
        task_id: str,
        func: Callable,
        interval_hours: float = 24,
        enabled: bool = True,
        **kwargs,
    ) -> None:
        self._tasks[task_id] = ScheduledTask(
            task_id=task_id,
            func=func,
            interval_seconds=max(60, int(interval_hours * 3600)),
            enabled=enabled,
            last_run=time.time(),  # 避免首次启动立即执行，等一个间隔周期
            kwargs=kwargs,
        )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _tick(self) -> None:
        if not self._running:
            return
        now = time.time()
        for task in self._tasks.values():
            if not task.enabled:
                continue
            if now - task.last_run < task.interval_seconds:
                continue
            task.last_run = now
            try:
                task.func(**task.kwargs)
            except Exception as exc:
                logger.error(f"定时任务 {task.task_id} 执行失败: {exc}")
        self._timer = threading.Timer(self._tick_seconds, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def trigger_now(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.last_run = time.time()
        try:
            task.func(**task.kwargs)
        except Exception as exc:
            logger.error(f"定时任务 {task.task_id} 手动触发失败: {exc}")
            raise

    def task_state(self, task_id: str) -> dict | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return {
            "task_id": task.task_id,
            "enabled": task.enabled,
            "interval_hours": task.interval_seconds / 3600,
            "last_run": task.last_run,
        }


class IntegrityScheduler:
    """将 verify_backup 包装为定时任务。

    校验范围按 config.integrity_check_scope 决定：
    - all：所有含备份素材的项目
    - backup：仅校验创建过备份作业的项目
    """

    def __init__(
        self, backup_service, db_service, scheduler: ScheduledTaskService | None = None
    ):
        self.backup_service = backup_service
        self.db_service = db_service
        self.scheduler = scheduler if scheduler is not None else ScheduledTaskService()

    def setup(self, interval_hours: float = 168) -> None:
        self.scheduler.register(
            "integrity_check",
            self.run_all_projects,
            interval_hours=interval_hours,
        )

    def _projects_for_scope(self) -> list:
        from DITWorkstation.App import config

        scope = getattr(config, "integrity_check_scope", "all") or "all"
        if scope == "backup":
            job_ids = {
                j.get("project_id")
                for j in self.db_service.get_backup_jobs()
                if j.get("project_id")
            }
            return [
                p for p in self.db_service.get_projects() if p.project_id in job_ids
            ]
        return self.db_service.get_projects()

    def run_all_projects(self) -> dict:
        results = {}
        for project in self._projects_for_scope():
            try:
                results[project.project_id] = self.run_verification(project.project_id)
            except Exception as exc:
                logger.error(f"项目 {project.name} 完整性校验失败: {exc}")
                results[project.project_id] = {"error": str(exc)}
        return results

    def run_verification(self, project_id: str) -> dict:
        stats = self.backup_service.verify_backup(project_id)
        ok = not (stats.get("missing") or stats.get("mismatch"))
        self.db_service.record_operation(
            "定期完整性校验",
            f"检查 {stats['checked']}，一致 {stats['matched']}，"
            f"缺失 {stats['missing']}，不一致 {stats['mismatch']}，"
            f"无校验和 {stats.get('unhashable', 0)}",
            project_id=project_id,
            status="success" if ok else "error",
            object_type="integrity_check",
            object_id=project_id,
        )
        stats["ok"] = ok
        return stats
