"""工作区 + 项目选择共享控件

消除 9 个视图中重复的工作区下拉/项目列表/新建对话框/全局信号同步逻辑。
各视图改为组合该控件，监听 `project_changed` / `workspace_changed` 信号做自身业务。

项目选择统一使用 QComboBox（下拉框），替代旧版 QListWidget（黑底大框），
在 macOS/Windows 上均更紧凑易用。
"""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QInputDialog, QMessageBox
)
from PySide6.QtCore import Signal, Slot

from DITWorkstation.App.session_context import (
    get_data_bus, get_current_workspace_id, get_current_project_id,
    set_current_workspace, set_current_project
)
from DITWorkstation.Models import Workspace, Project
from DITWorkstation.Utils import get_db_service, safe_slot, logger
from DITWorkstation.Views.Widgets.workspace_dialog import WorkspaceDialog


class WorkspaceProjectSelector(QWidget):
    """工作区 + 项目两级选择控件

    封装职责：
    - 工作区下拉（含"全部工作区"项）+ 新建按钮 + 可选编辑按钮
    - 项目下拉（QComboBox），按工作区过滤
    - 新建项目按钮（需选中具体工作区才启用）
    - 可选删除项目按钮
    - 监听全局 workspace_focus_changed / project_focus_changed 并同步
    - 工作区切换 → 广播 set_current_workspace + 重载项目
    - 项目切换 → 广播 set_current_project

    对外信号：
    - workspace_changed(workspace_id)：工作区切换（含 None）
    - project_changed(project_id)：项目切换（含 None）

    布局模式：
    - project_widget="list"：垂直布局（适合左面板）
    - project_widget="combo"：水平布局（适合 header）
    两种模式均使用 QComboBox 作为项目选择控件。
    """

    workspace_changed = Signal(object)  # workspace_id
    project_changed = Signal(object)  # project_id

    def __init__(self, parent=None, *,
                 project_widget: str = "list",
                 show_edit_workspace: bool = False,
                 show_new_project: bool = True,
                 show_delete_project: bool = False,
                 none_label: str = "（未选择项目）",
                 broadcast_none: bool = True,
                 db_service=None):
        """
        Args:
            project_widget: "list" 垂直布局，"combo" 水平布局（均用 QComboBox）
            show_edit_workspace: 是否显示"编辑工作区"按钮
            show_new_project: 是否显示"新建项目"按钮
            show_delete_project: 是否显示"删除项目"按钮
            none_label: None 项的显示文本（如"不关联项目""全部项目"）
            broadcast_none: 选中 None 项时是否广播 set_current_project(None)。
                False 适用于"不关联/全部"语义（备份/RAW提取/检索），避免清空全局项目。
            db_service: 数据库服务（为 None 时用全局单例）
        """
        super().__init__(parent)
        self._db_service = db_service or get_db_service()
        self._project_widget_type = project_widget
        self._show_edit_workspace = show_edit_workspace
        self._show_new_project = show_new_project
        self._show_delete_project = show_delete_project
        self._none_label = none_label
        self._broadcast_none = broadcast_none
        # 内部标志：避免全局信号回环触发再次广播
        self._suppress_broadcast = False

        self._setup_ui()
        self._connect_global_signals()

    def _setup_ui(self):
        if self._project_widget_type == "combo":
            self._setup_horizontal_layout()
        else:
            self._setup_vertical_layout()

    def _setup_vertical_layout(self):
        """垂直布局：工作区下拉 + 按钮 + 项目下拉 + 新建/删除项目按钮"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        ws_label = QLabel("工作区")
        ws_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(ws_label)

        self.workspace_combo = QComboBox()
        self.workspace_combo.addItem("（全部工作区）", None)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        layout.addWidget(self.workspace_combo)

        ws_btn_layout = QHBoxLayout()
        new_ws_btn = QPushButton("+ 新建工作区")
        new_ws_btn.clicked.connect(self._create_workspace)
        ws_btn_layout.addWidget(new_ws_btn)

        if self._show_edit_workspace:
            self._edit_ws_btn = QPushButton("编辑")
            self._edit_ws_btn.clicked.connect(self._edit_workspace)
            self._edit_ws_btn.setEnabled(False)
            ws_btn_layout.addWidget(self._edit_ws_btn)

        layout.addLayout(ws_btn_layout)

        proj_label = QLabel("项目")
        proj_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(proj_label)

        self.project_combo = QComboBox()
        self.project_combo.addItem(self._none_label, None)
        self.project_combo.currentIndexChanged.connect(self._on_project_combo_changed)
        layout.addWidget(self.project_combo)

        if self._show_new_project or self._show_delete_project:
            proj_btn_layout = QHBoxLayout()
            if self._show_new_project:
                self.new_proj_btn = QPushButton("+ 新建项目")
                self.new_proj_btn.clicked.connect(self._create_project)
                self.new_proj_btn.setEnabled(False)
                proj_btn_layout.addWidget(self.new_proj_btn)
            if self._show_delete_project:
                self.del_proj_btn = QPushButton("删除")
                self.del_proj_btn.clicked.connect(self._delete_project)
                self.del_proj_btn.setEnabled(False)
                proj_btn_layout.addWidget(self.del_proj_btn)
            layout.addLayout(proj_btn_layout)

        layout.addStretch()

    def _setup_horizontal_layout(self):
        """水平布局：工作区下拉 + 按钮 + 项目下拉 + 按钮（适合 header）"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("工作区:"))
        self.workspace_combo = QComboBox()
        self.workspace_combo.setMinimumWidth(180)
        self.workspace_combo.addItem("（全部工作区）", None)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        layout.addWidget(self.workspace_combo)

        new_ws_btn = QPushButton("+ 新建工作区")
        new_ws_btn.clicked.connect(self._create_workspace)
        layout.addWidget(new_ws_btn)

        if self._show_edit_workspace:
            self._edit_ws_btn = QPushButton("编辑")
            self._edit_ws_btn.clicked.connect(self._edit_workspace)
            self._edit_ws_btn.setEnabled(False)
            layout.addWidget(self._edit_ws_btn)

        layout.addWidget(QLabel("项目:"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(260)
        self.project_combo.addItem(self._none_label, None)
        self.project_combo.currentIndexChanged.connect(self._on_project_combo_changed)
        layout.addWidget(self.project_combo)

        if self._show_new_project:
            self.new_proj_btn = QPushButton("+ 新建项目")
            self.new_proj_btn.clicked.connect(self._create_project)
            self.new_proj_btn.setEnabled(False)
            layout.addWidget(self.new_proj_btn)

        if self._show_delete_project:
            self.del_proj_btn = QPushButton("删除")
            self.del_proj_btn.clicked.connect(self._delete_project)
            self.del_proj_btn.setEnabled(False)
            layout.addWidget(self.del_proj_btn)

    # ===== 全局信号连接 =====
    def _connect_global_signals(self):
        """监听全局工作区/项目切换，同步本控件选择"""
        try:
            bus = get_data_bus()
            bus.workspace_focus_changed.connect(self._on_global_workspace_changed)
            bus.project_focus_changed.connect(self._on_global_project_changed)
        except Exception as e:
            logger.warning(f"全局信号连接失败: {e}")

    # ===== 加载 =====
    def refresh(self):
        """重新加载工作区和项目（showEvent 时调用）"""
        self._load_workspaces()
        self._load_projects()

    def _load_workspaces(self):
        """加载工作区下拉，优先选中全局 current_workspace_id"""
        prev_id = get_current_workspace_id()
        self.workspace_combo.blockSignals(True)
        self.workspace_combo.clear()
        self.workspace_combo.addItem("（全部工作区）", None)
        try:
            workspaces = self._db_service.get_workspaces()
        except Exception:
            workspaces = []
        target_index = 0
        for i, ws in enumerate(workspaces, start=1):
            label = ws.name
            if ws.path:
                label += f"  [{ws.path}]"
            self.workspace_combo.addItem(label, ws.workspace_id)
            if prev_id and ws.workspace_id == prev_id:
                target_index = i
        self.workspace_combo.setCurrentIndex(target_index)
        self.workspace_combo.blockSignals(False)
        # 同步编辑按钮与新建项目按钮启用状态
        if self._show_edit_workspace:
            self._edit_ws_btn.setEnabled(target_index > 0)
        if self._show_new_project:
            self.new_proj_btn.setEnabled(target_index > 0)

    def _load_projects(self):
        """加载项目下拉，按当前选中工作区过滤"""
        prev_pid = get_current_project_id()
        ws_id = self.workspace_combo.currentData()
        if ws_id is None:
            ws_id = get_current_workspace_id()

        try:
            projects = self._db_service.get_projects(workspace_id=ws_id)
        except Exception:
            projects = []

        self._suppress_broadcast = True
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem(self._none_label, None)
        target_index = 0
        for i, p in enumerate(projects, start=1):
            self.project_combo.addItem(f"{p.name} ({p.project_id})", p.project_id)
            if prev_pid and p.project_id == prev_pid:
                target_index = i
        self.project_combo.setCurrentIndex(target_index)
        self.project_combo.blockSignals(False)
        self._suppress_broadcast = False
        # 同步删除项目按钮
        if self._show_delete_project:
            self._sync_delete_project_btn(self.get_current_project_id())

    # ===== 槽函数 =====
    @Slot(int)
    def _on_workspace_changed(self, _index: int):
        """本控件工作区下拉切换 → 广播到全局，重载项目"""
        if self._suppress_broadcast:
            return
        ws_id = self.workspace_combo.currentData()
        set_current_workspace(ws_id)
        # set_current_workspace 会触发 _on_global_workspace_changed，但同 ID 不触发
        # 手动重载保证一致性
        self._load_projects()
        # 同步按钮启用状态
        if self._show_edit_workspace:
            self._edit_ws_btn.setEnabled(ws_id is not None)
        if self._show_new_project:
            self.new_proj_btn.setEnabled(ws_id is not None)
        # 通知宿主视图
        self.workspace_changed.emit(ws_id)

    @Slot(int)
    def _on_project_combo_changed(self, _index: int):
        """项目下拉切换 → 广播到全局"""
        if self._suppress_broadcast:
            return
        project_id = self.project_combo.currentData()
        self._sync_delete_project_btn(project_id)
        self._broadcast_project_changed(project_id)

    def _sync_delete_project_btn(self, project_id):
        """同步删除项目按钮启用状态：需同时选中工作区和项目"""
        if not self._show_delete_project:
            return
        ws_id = self.workspace_combo.currentData()
        self.del_proj_btn.setEnabled(bool(ws_id and project_id))

    def _broadcast_project_changed(self, project_id):
        """广播项目切换到全局并通知宿主。

        broadcast_none=False 时，None 不广播到全局（保留"不关联/全部"语义），
        但仍向宿主视图发射 project_changed 信号。
        """
        if project_id is None and not self._broadcast_none:
            self.project_changed.emit(project_id)
            return
        set_current_project(project_id)
        self.project_changed.emit(project_id)

    @Slot(object)
    def _on_global_workspace_changed(self, workspace_id):
        """全局工作区切换 → 同步本控件下拉（避免回环）"""
        # 不可见时跳过刷新，等 showEvent 触发时再加载（减少信号风暴时的无效 DB 查询）
        if not self.isVisible():
            return
        self._suppress_broadcast = True
        self.workspace_combo.blockSignals(True)
        if workspace_id is None:
            self.workspace_combo.setCurrentIndex(0)
        else:
            found = False
            for i in range(self.workspace_combo.count()):
                if self.workspace_combo.itemData(i) == workspace_id:
                    self.workspace_combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                # 工作区不在下拉中（如新建后），重载
                self.workspace_combo.blockSignals(False)
                self._suppress_broadcast = False
                self._load_workspaces()
                self._load_projects()
                return
        self.workspace_combo.blockSignals(False)
        self._suppress_broadcast = False
        self._load_projects()
        if self._show_edit_workspace:
            self._edit_ws_btn.setEnabled(workspace_id is not None)
        if self._show_new_project:
            self.new_proj_btn.setEnabled(workspace_id is not None)

    @Slot(object)
    def _on_global_project_changed(self, project_id):
        """全局项目切换 → 同步本控件选择（避免回环）。

        broadcast_none=False 时，None 不强制覆盖（保留"不关联"语义），
        与原 backup/raw 视图的 `if project_id is None: return` 行为一致。
        """
        # 不可见时跳过刷新，等 showEvent 触发时再加载（减少信号风暴时的无效 DB 查询）
        if not self.isVisible():
            return
        if project_id is None and not self._broadcast_none:
            return
        self._suppress_broadcast = True
        self.project_combo.blockSignals(True)
        if project_id is None:
            self.project_combo.setCurrentIndex(0)
        else:
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == project_id:
                    self.project_combo.setCurrentIndex(i)
                    break
        self.project_combo.blockSignals(False)
        self._suppress_broadcast = False
        # 通知宿主视图（不广播到全局，避免回环）
        self.project_changed.emit(project_id)

    # ===== 新建/编辑操作 =====
    @safe_slot("新建工作区失败")
    def _create_workspace(self):
        """打开新建工作区对话框"""
        ws = WorkspaceDialog.create(parent=self, db_service=self._db_service)
        if ws is None:
            return
        # 新建后自动设为当前工作区，广播变更
        set_current_workspace(ws.workspace_id)
        get_data_bus().emit_data_changed("workspaces_changed")

    @safe_slot("编辑工作区失败")
    def _edit_workspace(self):
        """打开编辑工作区对话框"""
        ws_id = self.workspace_combo.currentData()
        if not ws_id:
            QMessageBox.information(self, "提示", "请先选择一个工作区")
            return
        ws = self._db_service.get_workspace(ws_id)
        if not ws:
            QMessageBox.warning(self, "提示", "工作区不存在")
            return
        updated = WorkspaceDialog.edit(
            workspace=ws, parent=self, db_service=self._db_service
        )
        if updated is None:
            return
        get_data_bus().emit_data_changed("workspaces_changed")
        # 刷新下拉显示
        self._load_workspaces()

    @safe_slot("新建项目失败")
    def _create_project(self):
        """打开新建项目对话框（需选中具体工作区）"""
        ws_id = self.workspace_combo.currentData()
        if not ws_id:
            QMessageBox.warning(self, "提示", "请先选择一个工作区")
            return
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
        if not (ok and name):
            return
        project = self._db_service.create_project(name=name, workspace_id=ws_id)
        # 新建后选中并广播
        self._load_projects()
        set_current_project(project.project_id)
        get_data_bus().emit_data_changed("projects_changed")

    @safe_slot("删除项目失败")
    def _delete_project(self):
        """删除当前选中的项目（含确认对话框）"""
        project_id = self.project_combo.currentData()
        if not project_id:
            return
        project_name = self.project_combo.currentText().split(" (")[0]
        reply = QMessageBox.question(
            self, "确认",
            f"确定删除项目「{project_name}」及所有关联数据？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self._db_service.delete_project(project_id)
        self._load_projects()
        set_current_project(None)
        get_data_bus().emit_data_changed("projects_changed")

    # ===== 公开查询方法 =====
    def get_current_workspace_id(self) -> Optional[str]:
        """返回当前选中工作区 ID（无选中返回 None）"""
        return self.workspace_combo.currentData()

    def get_current_workspace(self) -> Optional[Workspace]:
        """返回当前选中工作区对象（无选中返回 None）"""
        ws_id = self.workspace_combo.currentData()
        if not ws_id:
            return None
        try:
            return self._db_service.get_workspace(ws_id)
        except Exception:
            return None

    def get_current_project_id(self) -> Optional[str]:
        """返回当前选中项目 ID（无选中返回 None）"""
        return self.project_combo.currentData()

    def get_current_project(self) -> Optional[Project]:
        """返回当前选中项目对象（无选中返回 None）"""
        pid = self.get_current_project_id()
        if not pid:
            return None
        try:
            return self._db_service.get_project(pid)
        except Exception:
            return None
