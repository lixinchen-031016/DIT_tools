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

    def _on_finished(self, value):
        self._clear_worker()
        self.finished.emit(value)

    def _on_error(self, message):
        self._clear_worker()
        self.error.emit(message)
