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
            # 仅在调用方未显式提供回调时（无论位置参数还是关键字参数）才自动注入，
            # 避免 "got multiple values for argument" 错误
            self._maybe_inject_callback('progress_callback', self._on_progress)
            self._maybe_inject_callback('file_completed_callback', self._on_file_completed)
            self._result = self.func(*self.args, **self.kwargs)
            self.finished.emit(self._result)
        except Exception as e:
            self.error.emit(str(e))

    def _maybe_inject_callback(self, name: str, callback: Callable) -> None:
        """判断是否需要自动注入回调参数。

        跳过注入的条件（任一即可）：
        1. 函数不接受该关键字参数
        2. 调用方已通过 kwargs 显式传递（即使值为 None）
        3. 调用方已通过位置参数覆盖该参数在签名中的位置
        """
        if name in self.kwargs:
            return  # 调用方已通过关键字参数显式传递
        if not self._accepts_kwarg(self.func, name):
            return  # 函数不接受该参数
        # 检查位置参数是否已覆盖该参数位置
        try:
            sig = inspect.signature(self.func)
            params = list(sig.parameters.values())
            for i, p in enumerate(params):
                if p.name == name and i < len(self.args):
                    return  # 位置参数已覆盖
        except (ValueError, TypeError):
            return  # 无法分析签名，保守起见不注入
        self.kwargs[name] = callback

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
