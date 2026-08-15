"""后台任务 ViewModel。

把 QThread 的创建、状态、错误、取消和清理集中起来，视图只处理展示与用户操作。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Signal

from DITWorkstation.Utils.workers import WorkerThread, TaskState


@dataclass
class TaskRecord:
    """任务观测基线；后续可直接映射至持久化任务历史。"""
    task_name: str
    project_id: Optional[str] = None
    recovery_info: dict[str, Any] = field(default_factory=dict)
    state: TaskState = TaskState.IDLE
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_summary: str = ""


class TaskViewModel(QObject):
    """管理一个可取消的 WorkerThread 实例。"""

    state_changed = Signal(str)
    progress = Signal(str, float, str)
    file_completed = Signal(str, object)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: Optional[WorkerThread] = None
        self._last_state = TaskState.IDLE
        self.current_record: Optional[TaskRecord] = None
        self.history: list[TaskRecord] = []

    @property
    def state(self) -> TaskState:
        return self.worker.state if self.worker else self._last_state

    def start(
        self,
        func: Callable,
        *args,
        inject_progress: bool = False,
        inject_file_completed: bool = False,
        inject_cancel_check: bool = False,
        task_name: str = "",
        project_id: Optional[str] = None,
        recovery_info: Optional[dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """启动任务；已有任务运行时返回 False。"""
        if self.worker is not None and self.worker.isRunning():
            return False

        worker = WorkerThread(
            func,
            *args,
            inject_progress=inject_progress,
            inject_file_completed=inject_file_completed,
            inject_cancel_check=inject_cancel_check,
            **kwargs,
        )
        self.worker = worker
        self._last_state = TaskState.IDLE
        self.current_record = TaskRecord(
            task_name=task_name or getattr(func, "__name__", "task"),
            project_id=project_id,
            recovery_info=dict(recovery_info or {}),
        )
        worker.state_changed.connect(self._on_state_changed)
        worker.progress.connect(self.progress)
        worker.file_completed.connect(self.file_completed)
        # 先在主线程完成内部清理，再向外转发终态信号，避免调用方收到
        # finished/error 时仍观察到已结束的 worker。
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        # Delete the QThread wrapper only after the native thread has exited.
        # The result/error signals are emitted from run() before QThread has
        # completed its teardown, so using them for deleteLater() can race
        # with Qt's native thread cleanup.
        worker.thread_finished.connect(worker.deleteLater)
        worker.start()
        return True

    def cancel(self):
        """请求取消当前任务。"""
        if self.worker is not None:
            self.worker.cancel()

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _clear_worker(self):
        if self.worker is not None:
            self._last_state = self.worker.state
        self.worker = None

    def _on_state_changed(self, value: str):
        state = TaskState(value)
        if self.current_record is not None:
            self.current_record.state = state
            if state == TaskState.RUNNING and self.current_record.started_at is None:
                self.current_record.started_at = datetime.now()
            if state in (TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED):
                self.current_record.completed_at = datetime.now()
        self.state_changed.emit(value)

    def mark_recoverable(self, recovery_info: Optional[dict[str, Any]] = None):
        """把失败/取消任务标为可恢复，并保存恢复所需的最小上下文。"""
        if self.current_record is not None:
            self.current_record.state = TaskState.RECOVERABLE
            self.current_record.recovery_info.update(recovery_info or {})
            self.current_record.completed_at = datetime.now()
        self._last_state = TaskState.RECOVERABLE
        self.state_changed.emit(TaskState.RECOVERABLE.value)

    def _on_finished(self, value):
        if self.current_record is not None:
            self.current_record.completed_at = datetime.now()
            self.history.append(self.current_record)
            self.current_record = None
        self._clear_worker()
        self.finished.emit(value)

    def _on_error(self, message):
        if self.current_record is not None:
            self.current_record.error_summary = message
            self.current_record.completed_at = datetime.now()
            self.history.append(self.current_record)
            self.current_record = None
        self._clear_worker()
        self.error.emit(message)
