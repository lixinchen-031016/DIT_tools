"""统一后台任务协议测试。"""
import time

from PySide6.QtCore import QEventLoop, QTimer

from DITWorkstation.Utils.workers import SimpleWorkerThread, TaskState, WorkerThread
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


def test_cancel_after_terminal_state_does_not_set_cancel_event():
    worker = WorkerThread(lambda: "done")
    worker.run()
    worker.cancel()
    assert worker.state is TaskState.COMPLETED
    assert not worker.is_cancelled()


def test_simple_worker_uses_same_state_protocol():
    worker = SimpleWorkerThread(lambda: "done")
    states = []
    worker.state_changed.connect(states.append)
    worker.run()
    worker.cancel()
    assert worker.state is TaskState.COMPLETED
    assert states == [TaskState.RUNNING.value, TaskState.COMPLETED.value]


def test_task_view_model_retains_terminal_state_after_completion():
    vm = TaskViewModel()
    finished = _wait_for_signal(
        vm.finished, start=lambda: vm.start(lambda: {"ok": True})
    )
    assert finished == [{"ok": True}]
    assert vm.worker is None
    assert vm.state is TaskState.COMPLETED


def test_task_view_model_records_observability_baseline():
    vm = TaskViewModel()
    _wait_for_signal(
        vm.finished,
        start=lambda: vm.start(
            lambda: "done", task_name="csv_export", project_id="project-1",
            recovery_info={"output_path": "/tmp/assets.csv"},
        ),
    )
    record = vm.history[-1]
    assert record.task_name == "csv_export"
    assert record.project_id == "project-1"
    assert record.recovery_info["output_path"] == "/tmp/assets.csv"
    assert record.started_at is not None and record.completed_at is not None
    assert record.state is TaskState.COMPLETED


def test_task_view_model_persists_history(db_service, project):
    """统一任务模型会把终态、输出和恢复上下文写入任务历史表。"""
    vm = TaskViewModel(task_store=db_service)
    _wait_for_signal(
        vm.finished,
        start=lambda: vm.start(
            lambda: {"written": 2}, task_name="test_export", project_id=project.project_id,
            recovery_info={"output_path": "/tmp/test.csv"},
        ),
    )
    rows = db_service.get_task_history(project.project_id)
    assert rows[0]["task_name"] == "test_export"
    assert rows[0]["state"] == TaskState.COMPLETED.value
    assert rows[0]["output"] == {"result": {"written": 2}}
    assert rows[0]["recovery"]["output_path"] == "/tmp/test.csv"


def test_task_view_model_persists_keyword_values(db_service, project):
    vm = TaskViewModel(task_store=db_service)
    _wait_for_signal(
        vm.finished,
        start=lambda: vm.start(
            lambda **kwargs: kwargs,
            task_name="parameter_capture",
            project_id=project.project_id,
            output_path="/tmp/export.csv",
            include_hidden=True,
        ),
    )
    row = db_service.get_task_history(project.project_id)[0]
    assert row["parameters"]["kwargs"] == {
        "output_path": "/tmp/export.csv",
        "include_hidden": True,
    }


def test_worker_cleanup_waits_for_native_thread_completion():
    worker = WorkerThread(lambda: "done")
    events = []
    worker.finished.connect(lambda _result: events.append("result"))
    worker.thread_finished.connect(lambda: events.append("thread"))
    worker.start()
    assert worker.wait(3000)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert events == ["result", "thread"]


def test_worker_survives_reference_drop_while_native_thread_running():
    import threading
    from PySide6.QtWidgets import QApplication

    entered = threading.Event()
    release = threading.Event()
    thread_finished = threading.Event()

    def task():
        entered.set()
        release.wait(5)
        return "done"

    def spawn():
        worker = WorkerThread(task)
        worker.thread_finished.connect(lambda: thread_finished.set())
        worker.start()
        # worker 引用在此作用域结束时被丢弃；若 QThread 包装对象被提前析构，
        # Qt 会 abort（"QThread: Destroyed while thread '' is still running"）。

    spawn()
    assert entered.wait(3)
    release.set()
    deadline = time.monotonic() + 3
    while not thread_finished.is_set() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert thread_finished.is_set()


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
