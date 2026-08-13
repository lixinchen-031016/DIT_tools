"""后台任务 ViewModel。

把 QThread 的创建、状态、错误、取消和清理集中起来，视图只处理展示与用户操作。
"""
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from DITWorkstation.Utils.workers import WorkerThread, TaskState


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
        worker.state_changed.connect(self.state_changed)
        worker.progress.connect(self.progress)
        worker.file_completed.connect(self.file_completed)
        worker.finished.connect(self.finished)
        worker.error.connect(self.error)
        worker.finished.connect(self._on_done)
        worker.error.connect(self._on_done)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        worker.start()
        return True

    def cancel(self):
        """请求取消当前任务。"""
        if self.worker is not None:
            self.worker.cancel()

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _on_done(self, _value):
        # 保留 worker 引用到信号处理结束，避免 finished/error 信号触发期间悬空。
        if self.worker is not None:
            self._last_state = self.worker.state
        self.worker = None
