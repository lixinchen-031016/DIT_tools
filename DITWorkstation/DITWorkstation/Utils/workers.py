"""后台工作线程工具"""
import inspect
from PySide6.QtCore import QThread, Signal, QObject
from typing import Callable


class WorkerSignals(QObject):
    """工作线程信号"""
    progress = Signal(str, float, str)  # target, progress, message
    finished = Signal(object)  # result
    error = Signal(str)  # error message
    file_completed = Signal(str, object)  # target, task


class WorkerThread(QThread):
    """通用后台工作线程"""
    progress = Signal(str, float, str)
    finished = Signal(object)
    error = Signal(str)
    file_completed = Signal(str, object)

    def __init__(self, func: Callable, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self._result = None

    @staticmethod
    def _accepts_kwarg(func: Callable, name: str) -> bool:
        """判断函数是否接受指定关键字参数（含 **kwargs）"""
        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            return False
        params = sig.parameters
        if name in params:
            return True
        return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

    def run(self):
        try:
            # 仅在函数接受时注入回调，避免 unexpected keyword argument
            if 'progress_callback' not in self.kwargs and self._accepts_kwarg(self.func, 'progress_callback'):
                self.kwargs['progress_callback'] = self._on_progress
            if 'file_completed_callback' not in self.kwargs and self._accepts_kwarg(self.func, 'file_completed_callback'):
                self.kwargs['file_completed_callback'] = self._on_file_completed
            self._result = self.func(*self.args, **self.kwargs)
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
