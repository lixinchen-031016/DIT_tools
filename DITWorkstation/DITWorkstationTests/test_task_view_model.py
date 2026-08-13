"""统一后台任务协议测试。"""
import time

from PySide6.QtCore import QEventLoop, QTimer

from DITWorkstation.Utils.workers import TaskState, WorkerThread
from DITWorkstation.ViewModels import TaskViewModel


def _wait_for_signal(signal, start=None, timeout=3000):
    loop = QEventLoop()
    captured = []
    signal.connect(lambda value: (captured.append(value), loop.quit()))
    if start is not None:
        start()
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    return captured


def test_worker_state_changes_and_completion():
    worker = WorkerThread(lambda: "done")
    states = []
    worker.state_changed.connect(states.append)
    result = []
    worker.finished.connect(result.append)
    worker.run()

    assert result == ["done"]
    assert worker.state is TaskState.COMPLETED
    assert states == [TaskState.RUNNING.value, TaskState.COMPLETED.value]


def test_worker_cancelled_by_cancel_check():
    def cancellable(cancel_check):
        while not cancel_check():
            time.sleep(0.001)
        raise InterruptedError("cancelled")

    worker = WorkerThread(cancellable, inject_cancel_check=True)
    states = []
    worker.state_changed.connect(states.append)
    worker._cancel_event.set()
    worker.run()

    assert worker.state is TaskState.CANCELLED
    assert states[-1] == TaskState.CANCELLED.value


def test_task_view_model_retains_terminal_state_after_completion():
    vm = TaskViewModel()
    finished = _wait_for_signal(
        vm.finished, start=lambda: vm.start(lambda: {"ok": True})
    )
    assert finished == [{"ok": True}]
    assert vm.worker is None
    assert vm.state is TaskState.COMPLETED


def test_task_view_model_retains_failed_state():
    vm = TaskViewModel()
    errors = _wait_for_signal(
        vm.error,
        start=lambda: vm.start(lambda: (_ for _ in ()).throw(ValueError("bad task"))),
    )
    assert errors == ["bad task"]
    assert vm.state is TaskState.FAILED


def test_task_view_model_cancel():
    def cancellable(cancel_check):
        while not cancel_check():
            time.sleep(0.001)
        raise InterruptedError("cancelled")

    vm = TaskViewModel()
    finished = []
    vm.finished.connect(finished.append)
    vm.start(cancellable, inject_cancel_check=True)
    errors = _wait_for_signal(vm.error, start=lambda: QTimer.singleShot(10, vm.cancel))

    assert vm.state is TaskState.CANCELLED
    assert finished == []
    assert errors == ["cancelled"]
