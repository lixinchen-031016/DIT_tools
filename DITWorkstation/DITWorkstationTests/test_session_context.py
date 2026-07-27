"""EventBus + 全局会话状态联动测试 - Phase 1.6

验证 App/session_context.py 的信号广播与项目/工作区联动逻辑。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# EventBus 依赖 Qt 信号系统，需 QCoreApplication 实例
from PySide6.QtCore import QCoreApplication
_app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from DITWorkstation.App import session_context


class TestEventBus(unittest.TestCase):
    """EventBus 信号广播"""

    def setUp(self):
        # 每个测试重置全局状态，避免互相干扰
        session_context._current_project_id = None
        session_context._current_workspace_id = None
        self.received_events = []
        self.received_projects = []
        self.received_workspaces = []

    def _connect(self):
        bus = session_context.get_data_bus()
        bus.data_changed.connect(self.received_events.append)
        bus.project_focus_changed.connect(self.received_projects.append)
        bus.workspace_focus_changed.connect(self.received_workspaces.append)

    def test_emit_data_changed(self):
        bus = session_context.get_data_bus()
        self._connect()
        bus.emit_data_changed("assets_changed")
        self.assertEqual(self.received_events, ["assets_changed"])

    def test_emit_data_changed_default(self):
        bus = session_context.get_data_bus()
        self._connect()
        bus.emit_data_changed()
        self.assertEqual(self.received_events, ["all"])

    def test_emit_project_focus(self):
        bus = session_context.get_data_bus()
        self._connect()
        bus.emit_project_focus_changed("proj_123")
        self.assertEqual(self.received_projects, ["proj_123"])

    def test_emit_workspace_focus(self):
        bus = session_context.get_data_bus()
        self._connect()
        bus.emit_workspace_focus_changed(None)
        self.assertEqual(self.received_workspaces, [None])


class TestGlobalProjectState(unittest.TestCase):
    """全局当前项目状态联动"""

    def setUp(self):
        session_context._current_project_id = None
        session_context._current_workspace_id = None
        self.projects = []
        self.events = []
        bus = session_context.get_data_bus()
        bus.project_focus_changed.connect(self.projects.append)
        bus.data_changed.connect(self.events.append)

    def test_set_project_broadcasts(self):
        session_context.set_current_project("proj_1")
        self.assertEqual(session_context.get_current_project_id(), "proj_1")
        self.assertEqual(self.projects, ["proj_1"])
        # set_current_project 会广播 data_changed("all")
        self.assertIn("all", self.events)

    def test_set_same_project_no_broadcast(self):
        """设为相同值不应重复广播"""
        session_context.set_current_project("proj_1")
        self.projects.clear()
        self.events.clear()
        session_context.set_current_project("proj_1")
        self.assertEqual(self.projects, [])
        self.assertEqual(self.events, [])

    def test_clear_project(self):
        session_context.set_current_project("proj_1")
        self.projects.clear()
        session_context.set_current_project(None)
        self.assertIsNone(session_context.get_current_project_id())
        self.assertEqual(self.projects, [None])


class TestGlobalWorkspaceState(unittest.TestCase):
    """全局当前工作区状态联动"""

    def setUp(self):
        session_context._current_project_id = None
        session_context._current_workspace_id = None
        self.workspaces = []
        bus = session_context.get_data_bus()
        bus.workspace_focus_changed.connect(self.workspaces.append)

    def test_set_workspace_broadcasts(self):
        session_context.set_current_workspace("ws_1")
        self.assertEqual(session_context.get_current_workspace_id(), "ws_1")
        self.assertEqual(self.workspaces, ["ws_1"])

    def test_set_same_workspace_no_broadcast(self):
        session_context.set_current_workspace("ws_1")
        self.workspaces.clear()
        session_context.set_current_workspace("ws_1")
        self.assertEqual(self.workspaces, [])

    def test_clear_workspace(self):
        session_context.set_current_workspace("ws_1")
        self.workspaces.clear()
        session_context.set_current_workspace(None)
        self.assertIsNone(session_context.get_current_workspace_id())
        self.assertEqual(self.workspaces, [None])


if __name__ == "__main__":
    unittest.main()
