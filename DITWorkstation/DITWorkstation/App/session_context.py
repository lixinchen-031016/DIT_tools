"""会话上下文与事件总线

从 Views/main_window.py 抽离的全局会话状态层，使测试可在不导入 Qt 视图的前提下
直接访问数据总线与当前项目/工作区状态。

职责：
- EventBus：跨视图数据变更信号总线（projects_changed/assets_changed/logs_changed/...）
- 全局当前项目 / 当前工作区状态，及其切换时的联动广播

main_window.py 仍 re-export 这些符号，现有视图无需改动。
"""
import threading
from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    """
    跨视图数据变更信号总线。

    各视图在数据变更后通过 emit_data_changed 广播事件类型，
    关心的视图监听对应事件并按需刷新，解决"导入后切到日志视图不刷新"等联动断链。

    事件类型：
        - "projects_changed": 项目增删
        - "assets_changed": 素材导入/删除/关联变更
        - "logs_changed": 拍摄日志增删/关联变更
        - "workspaces_changed": 工作区增删
        - "all": 全部刷新
    """
    data_changed = Signal(str)
    # 全局当前项目切换信号：参数为 project_id（None 表示清除选择）
    project_focus_changed = Signal(object)
    # 全局当前工作区切换信号：参数为 workspace_id（None 表示清除选择）
    workspace_focus_changed = Signal(object)

    def emit_data_changed(self, event: str = "all"):
        self.data_changed.emit(event)

    def emit_project_focus_changed(self, project_id):
        self.project_focus_changed.emit(project_id)

    def emit_workspace_focus_changed(self, workspace_id):
        self.workspace_focus_changed.emit(workspace_id)


# 全局单例：所有视图共享同一总线
_data_bus = EventBus()


def get_data_bus() -> EventBus:
    """返回全局数据总线单例"""
    return _data_bus


# 全局状态锁：保护 _current_workspace_id / _current_project_id 的读写，
# 避免主线程槽与 worker 线程并发调用 set_current_* 时产生读改写竞争。
_state_lock = threading.Lock()

# ===== 全局当前工作区 =====
# 工作区是项目的父级容器；切换工作区会过滤项目下拉，并联动清除/重设当前项目。
_current_workspace_id = None


def get_current_workspace_id():
    """返回全局当前工作区 ID（可能为 None）"""
    with _state_lock:
        return _current_workspace_id


def set_current_workspace(workspace_id):
    """设置全局当前工作区并广播 workspace_focus_changed。

    切换工作区时：
      - 广播 workspace_focus_changed，各视图的项目下拉监听后过滤为本工作区项目
      - 当前项目若不属于新工作区，则清除（避免跨工作区误操作）
      - 广播 data_changed("all") 触发各视图刷新
    """
    global _current_workspace_id, _current_project_id
    with _state_lock:
        if _current_workspace_id == workspace_id:
            return
        _current_workspace_id = workspace_id

        # 当前项目若不属于新工作区则清除
        if workspace_id is not None and _current_project_id is not None:
            from DITWorkstation.Utils import get_db_service
            try:
                proj = get_db_service().get_project(_current_project_id)
                if proj is None or proj.workspace_id != workspace_id:
                    _current_project_id = None
            except Exception:
                _current_project_id = None

    # 信号发射放在锁外，避免槽函数中再次获取锁导致死锁
    get_data_bus().emit_workspace_focus_changed(workspace_id)
    get_data_bus().emit_project_focus_changed(_current_project_id)
    get_data_bus().emit_data_changed("all")


# ===== 全局当前项目 =====
# 各视图通过 set_current_project 切换，通过 get_current_project_id 读取，
# 避免每个视图各自维护 current_project 导致切换视图后要重选。
_current_project_id = None


def get_current_project_id():
    """返回全局当前项目 ID（可能为 None）"""
    with _state_lock:
        return _current_project_id


def set_current_project(project_id):
    """设置全局当前项目并广播 project_focus_changed 事件。

    各视图可监听该信号自动同步项目下拉，无需各自维护 current_project。

    若新项目属于某个工作区，会顺带把当前工作区切到该项目所属工作区，
    保证工作区下拉与项目下拉始终一致。
    """
    global _current_project_id, _current_workspace_id
    with _state_lock:
        if _current_project_id == project_id:
            return
        _current_project_id = project_id

        # 顺带同步工作区：若项目有 workspace_id，把当前工作区切到该项目所属工作区
        if project_id is not None:
            from DITWorkstation.Utils import get_db_service
            try:
                proj = get_db_service().get_project(project_id)
                if proj is not None and proj.workspace_id != _current_workspace_id:
                    _current_workspace_id = proj.workspace_id
                    _ws_changed = True
                else:
                    _ws_changed = False
            except Exception:
                _ws_changed = False
        else:
            _ws_changed = False

    # 信号发射放在锁外，避免槽函数中再次获取锁导致死锁
    if project_id is not None and _ws_changed:
        get_data_bus().emit_workspace_focus_changed(_current_workspace_id)
    get_data_bus().emit_project_focus_changed(project_id)
    # 项目切换也视为数据变更，触发各视图刷新
    get_data_bus().emit_data_changed("all")


def reset_session_state():
    """重置全局会话状态（当前工作区 + 当前项目）。

    供单元测试在 setup/teardown 中调用，确保跨测试状态隔离，
    避免上一个测试遗留的 current_project_id 影响下一个测试。
    """
    global _current_workspace_id, _current_project_id
    with _state_lock:
        _current_workspace_id = None
        _current_project_id = None
