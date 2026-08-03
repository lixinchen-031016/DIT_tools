"""后台工作线程工具"""
from PySide6.QtCore import QThread, Signal, QObject
from typing import Callable


class WorkerSignals(QObject):
    """工作线程信号"""
    progress = Signal(str, float, str)  # target, progress, message
    finished = Signal(object)  # result
    error = Signal(str)  # error message
    file_completed = Signal(str, object)  # target, task


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
    error = Signal(str)
    file_completed = Signal(str, object)

    def __init__(
        self,
        func: Callable,
        *args,
        inject_progress: bool = False,
        inject_file_completed: bool = False,
        **kwargs,
    ):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs
        self._inject_progress = inject_progress
        self._inject_file_completed = inject_file_completed
        self._result = None

    def run(self):
        try:
            kwargs = dict(self._kwargs)
            if self._inject_progress:
                kwargs['progress_callback'] = self._on_progress
            if self._inject_file_completed:
                kwargs['file_completed_callback'] = self._on_file_completed
            self._result = self._func(*self._args, **kwargs)
            self.finished.emit(self._result)
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, target: str, progress: float, message: str):
        self.progress.emit(target, progress, message)

    def _on_file_completed(self, target: str, task):
        self.file_completed.emit(target, task)


class SimpleWorkerThread(QThread):
    """简单后台工作线程（支持进度回调）"""
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, func: Callable, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
