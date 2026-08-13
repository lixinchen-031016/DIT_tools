"""后台工作线程工具"""
from PySide6.QtCore import QThread, Signal, QObject
from typing import Callable
from enum import Enum
import threading


class TaskState(str, Enum):
    """统一后台任务生命周期。"""
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerSignals(QObject):
    """工作线程信号"""
    progress = Signal(str, float, str)  # target, progress, message
    finished = Signal(object)  # result
    error = Signal(str)  # error message
    file_completed = Signal(str, object)  # target, task
    state_changed = Signal(str)


class WorkerThread(QThread):
    """通用后台工作线程

    回调注入采用显式契约：调用方通过 ``inject_progress`` /
    ``inject_file_completed`` 显式声明是否需要 worker 把自身的信号转发
    回调以关键字参数注入目标函数。相比旧版基于 ``inspect.signature`` 的
    反射推断，避免了「位置参数传 None 被误判为已覆盖」与
    "got multiple values for argument" 的坑。

    调用方若自行提供 ``progress_callback`` / ``file_completed_callback``
    （如 rename_view 用 lambda 桥接到自定义信号），只需保持两个开关为
    False（默认），传入的回调会原样透传，不会被覆盖。
    """
    progress = Signal(str, float, str)
    finished = Signal(object)
    # Keep the native QThread lifecycle signal accessible after ``finished``
    # is overridden by the result signal above.
    thread_finished = QThread.finished
    error = Signal(str)
    file_completed = Signal(str, object)
    state_changed = Signal(str)

    def __init__(
        self,
        func: Callable,
        *args,
        inject_progress: bool = False,
        inject_file_completed: bool = False,
        inject_cancel_check: bool = False,
        **kwargs,
    ):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs
        self._inject_progress = inject_progress
        self._inject_file_completed = inject_file_completed
        self._inject_cancel_check = inject_cancel_check
        self._result = None
        self._state = TaskState.IDLE
        self._cancel_event = threading.Event()

    @property
    def state(self) -> TaskState:
        return self._state

    def cancel(self):
        """请求取消；目标函数可通过 ``cancel_check`` 感知请求。"""
        if self._state in (TaskState.IDLE, TaskState.RUNNING):
            self._set_state(TaskState.CANCELLING)
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _set_state(self, state: TaskState):
        self._state = state
        self.state_changed.emit(state.value)

    def run(self):
        self._set_state(TaskState.RUNNING)
        try:
            kwargs = dict(self._kwargs)
            if self._inject_cancel_check:
                kwargs['cancel_check'] = self.is_cancelled
            if self._inject_progress:
                kwargs['progress_callback'] = self._on_progress
            if self._inject_file_completed:
                kwargs['file_completed_callback'] = self._on_file_completed
            self._result = self._func(*self._args, **kwargs)
            if self.is_cancelled():
                self._set_state(TaskState.CANCELLED)
            else:
                self._set_state(TaskState.COMPLETED)
            self.finished.emit(self._result)
        except Exception as e:
            if self.is_cancelled() or isinstance(e, InterruptedError):
                self._set_state(TaskState.CANCELLED)
            else:
                self._set_state(TaskState.FAILED)
            self.error.emit(str(e))

    def _on_progress(self, target: str, progress: float, message: str):
        self.progress.emit(target, progress, message)

    def _on_file_completed(self, target: str, task):
        self.file_completed.emit(target, task)


class SimpleWorkerThread(QThread):
    """简单后台工作线程（支持进度回调）"""
    finished = Signal(object)
    thread_finished = QThread.finished
    error = Signal(str)
    state_changed = Signal(str)

    def __init__(self, func: Callable, *args, inject_cancel_check: bool = False, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self._inject_cancel_check = inject_cancel_check
        self._state = TaskState.IDLE
        self._cancel_event = threading.Event()

    @property
    def state(self) -> TaskState:
        return self._state

    def cancel(self):
        self._cancel_event.set()
        if self._state in (TaskState.IDLE, TaskState.RUNNING):
            self._state = TaskState.CANCELLING
            self.state_changed.emit(self._state.value)

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self):
        self._state = TaskState.RUNNING
        self.state_changed.emit(self._state.value)
        try:
            kwargs = dict(self.kwargs)
            if self._inject_cancel_check:
                kwargs['cancel_check'] = self.is_cancelled
            result = self.func(*self.args, **kwargs)
            self._state = TaskState.CANCELLED if self.is_cancelled() else TaskState.COMPLETED
            self.state_changed.emit(self._state.value)
            self.finished.emit(result)
        except Exception as e:
            self._state = TaskState.CANCELLED if self.is_cancelled() or isinstance(e, InterruptedError) else TaskState.FAILED
            self.state_changed.emit(self._state.value)
            self.error.emit(str(e))
