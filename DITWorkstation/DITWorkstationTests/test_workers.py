"""WorkerThread 显式回调注入契约测试

验证 WorkerThread 通过 inject_progress / inject_file_completed 显式开关注入回调，
不再依赖 inspect 反射，从而规避 "got multiple values for argument" 与
位置参数传 None 被误判为「已覆盖」的旧 bug。
"""
import os
import sys
import unittest
from typing import Optional, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication

from DITWorkstation.Utils.workers import WorkerThread


def _dummy_func_with_callbacks(
    job_id: str,
    progress_callback: Optional[Callable] = None,
    file_completed_callback: Optional[Callable] = None,
    project_id: Optional[str] = None,
):
    """模拟 BackupService.execute_backup 的签名，回显收到的回调与参数"""
    received = {
        "job_id": job_id,
        "project_id": project_id,
        "progress_callback": progress_callback,
        "file_completed_callback": file_completed_callback,
    }
    if progress_callback:
        progress_callback("/target", 0.5, "copying")
    if file_completed_callback:
        file_completed_callback("/target", {"file": "test.cr2"})
    return received


class TestWorkerThreadExplicitInjection(unittest.TestCase):
    """WorkerThread 显式回调注入契约"""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication()

    def _run_and_collect(self, worker):
        """同步调用 run()（不起线程），收集 finished/error 信号结果"""
        captured = {}

        def _on_finished(result):
            captured["result"] = result

        def _on_error(message):
            captured["error"] = message

        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.run()  # 同步执行，信号在同线程直接派发
        return captured

    def test_inject_progress_provides_callback(self):
        """inject_progress=True 时，目标函数收到 worker 的 progress 转发回调"""
        worker = WorkerThread(
            _dummy_func_with_callbacks, "job_001",
            inject_progress=True, project_id="proj_001",
        )
        captured = self._run_and_collect(worker)
        self.assertNotIn("error", captured)
        self.assertIsNotNone(captured["result"]["progress_callback"])
        # 未声明注入 file_completed → 不应被注入
        self.assertIsNone(captured["result"]["file_completed_callback"])
        self.assertEqual(captured["result"]["project_id"], "proj_001")

    def test_inject_file_completed_provides_callback(self):
        """inject_file_completed=True 时，目标函数收到 file_completed 转发回调"""
        worker = WorkerThread(
            _dummy_func_with_callbacks, "job_002",
            inject_file_completed=True, project_id="proj_002",
        )
        captured = self._run_and_collect(worker)
        self.assertNotIn("error", captured)
        self.assertIsNotNone(captured["result"]["file_completed_callback"])
        self.assertIsNone(captured["result"]["progress_callback"])

    def test_both_injections(self):
        """同时注入两个回调"""
        worker = WorkerThread(
            _dummy_func_with_callbacks, "job_003",
            inject_progress=True, inject_file_completed=True,
        )
        captured = self._run_and_collect(worker)
        self.assertIsNotNone(captured["result"]["progress_callback"])
        self.assertIsNotNone(captured["result"]["file_completed_callback"])

    def test_no_injection_by_default(self):
        """默认不开启注入时，两个回调均为 None（函数默认值）"""
        worker = WorkerThread(
            _dummy_func_with_callbacks, "job_004", project_id="proj_004",
        )
        captured = self._run_and_collect(worker)
        self.assertIsNone(captured["result"]["progress_callback"])
        self.assertIsNone(captured["result"]["file_completed_callback"])

    def test_caller_explicit_callback_not_overwritten(self):
        """调用方显式传 progress_callback=None 且不开 inject 时，原样透传不被覆盖

        回归旧 bug：反射版本会因 None 占位误判，新契约由开关决定，不再误覆盖。
        """
        worker = WorkerThread(
            _dummy_func_with_callbacks, "job_005",
            progress_callback=None, file_completed_callback=None,
            project_id="proj_005",
        )
        captured = self._run_and_collect(worker)
        self.assertIsNone(captured["result"]["progress_callback"])
        self.assertIsNone(captured["result"]["file_completed_callback"])

    def test_caller_business_kwargs_preserved(self):
        """调用方的业务 kwargs 透传不被丢弃

        目标函数需以 **kwargs 接收注入的 progress_callback（与显式契约一致；
        不接受该形参却又开 inject_progress=True 属于调用方误用，应报错）。
        """
        def _func(job_id, compute_checksum=False, workspace_dir=None, **kwargs):
            return {
                "job_id": job_id,
                "compute_checksum": compute_checksum,
                "workspace_dir": workspace_dir,
                "has_progress_callback": "progress_callback" in kwargs,
            }
        worker = WorkerThread(
            _func, "job_006",
            compute_checksum=True, workspace_dir="/tmp/ws",
            inject_progress=True,
        )
        captured = self._run_and_collect(worker)
        self.assertNotIn("error", captured)
        self.assertTrue(captured["result"]["compute_checksum"])
        self.assertEqual(captured["result"]["workspace_dir"], "/tmp/ws")
        self.assertTrue(captured["result"]["has_progress_callback"])

    def test_func_without_callback_params_accepts_inject_flag(self):
        """目标函数无回调参数时声明 inject 不报错（kwargs 多余键由 **kwargs 吸收或忽略）

        这里目标函数显式接受 **kwargs，确保 inject 注入的键不会触发
        "got an unexpected keyword argument"。
        """
        def _flexible_func(job_id, **kwargs):
            return {"job_id": job_id, "got_progress": "progress_callback" in kwargs}
        worker = WorkerThread(
            _flexible_func, "job_007",
            inject_progress=True, inject_file_completed=True,
        )
        captured = self._run_and_collect(worker)
        self.assertTrue(captured["result"]["got_progress"])


if __name__ == "__main__":
    unittest.main()
