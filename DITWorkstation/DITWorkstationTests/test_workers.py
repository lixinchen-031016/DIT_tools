"""WorkerThread 回调注入测试 - 回归 "got multiple values for argument" bug

场景：调用方用位置参数传 None 占位 progress_callback / file_completed_callback，
WorkerThread 不应再通过 kwargs 注入同名回调，否则会触发
"got multiple values for argument 'progress_callback'" 错误。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock
from typing import Optional, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication

from DITWorkstation.Utils.workers import WorkerThread


def _dummy_func_with_callbacks(
    job_id: str,
    progress_callback: Optional[Callable] = None,
    file_completed_callback: Optional[Callable] = None,
    project_id: Optional[str] = None
):
    """模拟 BackupService.execute_backup 的签名"""
    if progress_callback:
        progress_callback("/target", 0.5, "copying")
    if file_completed_callback:
        file_completed_callback("/target", {"file": "test.cr2"})
    return {"job_id": job_id, "project_id": project_id}


class TestWorkerThreadCallbackInjection(unittest.TestCase):
    """WorkerThread 回调自动注入逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication()

    def test_positional_none_callbacks_not_overwritten(self):
        """回归：位置参数传 None 占位回调时，不应触发 multiple values 错误

        这是 backup_view.py 的实际调用方式：
            WorkerThread(execute_backup, job, None, None, project_id)
        """
        worker = WorkerThread(
            _dummy_func_with_callbacks,
            "job_001",
            None,        # progress_callback 位置占位
            None,        # file_completed_callback 位置占位
            "proj_001"   # project_id
        )
        # 模拟 run() 的回调注入判断逻辑
        worker._maybe_inject_callback('progress_callback', worker._on_progress)
        worker._maybe_inject_callback('file_completed_callback', worker._on_file_completed)

        # 验证：位置参数已覆盖，kwargs 不应被注入同名回调
        self.assertNotIn('progress_callback', worker.kwargs)
        self.assertNotIn('file_completed_callback', worker.kwargs)

    def test_kwargs_none_callback_not_overwritten(self):
        """关键字参数显式传 None 时，也不应被覆盖"""
        worker = WorkerThread(
            _dummy_func_with_callbacks,
            "job_002",
            progress_callback=None,
            file_completed_callback=None,
        )
        worker._maybe_inject_callback('progress_callback', worker._on_progress)
        worker._maybe_inject_callback('file_completed_callback', worker._on_file_completed)

        # 调用方已通过 kwargs 显式传 None，不应覆盖
        self.assertEqual(worker.kwargs['progress_callback'], None)
        self.assertEqual(worker.kwargs['file_completed_callback'], None)

    def test_missing_callbacks_get_injected(self):
        """未传回调参数时，应自动注入 worker 的回调转发"""
        worker = WorkerThread(
            _dummy_func_with_callbacks,
            "job_003",
            project_id="proj_003"
        )
        worker._maybe_inject_callback('progress_callback', worker._on_progress)
        worker._maybe_inject_callback('file_completed_callback', worker._on_file_completed)

        # 未传回调 → 应自动注入
        self.assertEqual(worker.kwargs['progress_callback'], worker._on_progress)
        self.assertEqual(worker.kwargs['file_completed_callback'], worker._on_file_completed)

    def test_func_without_callback_params_no_injection(self):
        """函数不接受回调参数时，不应注入"""

        def _simple_func(job_id: str, project_id: str = None):
            return {"job_id": job_id, "project_id": project_id}

        worker = WorkerThread(_simple_func, "job_004", project_id="proj_004")
        worker._maybe_inject_callback('progress_callback', worker._on_progress)
        worker._maybe_inject_callback('file_completed_callback', worker._on_file_completed)

        self.assertNotIn('progress_callback', worker.kwargs)
        self.assertNotIn('file_completed_callback', worker.kwargs)

    def test_actual_run_no_error_with_positional_none(self):
        """端到端：位置参数传 None 占位时，实际 run() 不报错"""
        worker = WorkerThread(
            _dummy_func_with_callbacks,
            "job_005",
            None,        # progress_callback 位置占位
            None,        # file_completed_callback 位置占位
            "proj_005"
        )
        # 直接调用 run() 内部逻辑（不 start 线程，避免信号到主线程的复杂性）
        worker._maybe_inject_callback('progress_callback', worker._on_progress)
        worker._maybe_inject_callback('file_completed_callback', worker._on_file_completed)
        result = worker.func(*worker.args, **worker.kwargs)

        self.assertEqual(result["job_id"], "job_005")
        self.assertEqual(result["project_id"], "proj_005")


if __name__ == "__main__":
    unittest.main()
