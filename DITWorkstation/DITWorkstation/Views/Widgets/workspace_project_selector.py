"""工作区 + 项目选择共享控件

消除 9 个视图中重复的工作区下拉/项目列表/新建对话框/全局信号同步逻辑。
各视图改为组合该控件，监听 `project_changed` / `workspace_changed` 信号做自身业务。

项目选择统一使用 QComboBox（下拉框），替代旧版 QListWidget（黑底大框），
在 macOS/Windows 上均更紧凑易用。
"""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QInputDialog, QMessageBox
)
from PySide6.QtCore import Signal, Slot

from DITWorkstation.App.session_context import (
    get_data_bus, get_current_workspace_id, get_current_project_id,
    set_current_workspace, set_current_project
)
from DITWorkstation.App.feature_flags import is_enabled
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
                 show_workspace: Optional[bool] = None,
                 none_label: str = "（未选择项目）",
                 broadcast_none: bool = True,
                 buttons_below: bool = False,
                 db_service=None):
        """
        Args:
            project_widget: "list" 垂直布局，"combo" 水平布局（均用 QComboBox）
            show_edit_workspace: 是否显示"编辑工作区"按钮
            show_new_project: 是否显示"新建项目"按钮
            show_delete_project: 是否显示"删除项目"按钮
            show_workspace: 是否显示工作区相关控件（标签/下拉/新建/编辑）。
                None 时按功能模式自动决定：个人模式隐藏工作区控件，
                项目列表显示全部项目，新建项目归入数据库 default 工作区。
            none_label: None 项的显示文本（如"不关联项目""全部项目"）
            broadcast_none: 选中 None 项时是否广播 set_current_project(None)。
                False 适用于"不关联/全部"语义（备份/RAW提取/检索），避免清空全局项目。
            buttons_below: 仅 combo 模式有效。True 时把「新建工作区/新建项目」按钮
                放在对应下拉框下方（两行布局），防止窄宽度下按钮与下拉框重叠。
            db_service: 数据库服务（为 None 时用全局单例）
        """
        super().__init__(parent)
        self._db_service = db_service or get_db_service()
        self._project_widget_type = project_widget
        self._show_edit_workspace = show_edit_workspace
        self._show_new_project = show_new_project
        self._show_delete_project = show_delete_project
        # 个人模式默认隐藏工作区控件（功能模式开关统一裁决）
        self._show_workspace = (
            is_enabled("workspace_selector") if show_workspace is None else show_workspace
        )
        self._none_label = none_label
        self._broadcast_none = broadcast_none
        self._buttons_below = buttons_below
        # 内部标志：避免全局信号回环触发再次广播
        self._suppress_broadcast = False
        # 工作区相关控件引用（个人模式下统一隐藏）
        self._ws_widgets = []

        self._setup_ui()
        self._apply_workspace_visibility()
        self._connect_global_signals()

    def _apply_workspace_visibility(self):
        """按功能模式隐藏/显示工作区相关控件。

        控件对象始终创建（保证内部逻辑引用安全），仅设置不可见；
        项目过滤与新建项目的工作区解析由 _show_workspace 分支控制。
        """
        for w in self._ws_widgets:
            w.setVisible(self._show_workspace)

    def _setup_ui(self):
        if self._project_widget_type == "combo":
            if self._buttons_below:
                self._setup_buttons_below_layout()
            else:
                self._setup_horizontal_layout()
        else:
            self._setup_vertical_layout()

    def _setup_buttons_below_layout(self):
        """两行布局：工作区/项目下拉在第一行，新建按钮在第二行对应下拉下方，
        防止窄宽度下按钮与下拉框重叠。"""
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(4)

        # 第一行：工作区 + 项目 下拉框
        ws_label = QLabel("工作区:")
        layout.addWidget(ws_label, 0, 0)
        self.workspace_combo = QComboBox()
        self.workspace_combo.setMinimumWidth(180)
        self.workspace_combo.addItem("（全部工作区）", None)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        layout.addWidget(self.workspace_combo, 0, 1)
        layout.setColumnStretch(1, 1)
        self._ws_widgets.append(ws_label)
        self._ws_widgets.append(self.workspace_combo)

        layout.addWidget(QLabel("项目:"), 0, 2)
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(260)
        self.project_combo.addItem(self._none_label, None)
        self.project_combo.currentIndexChanged.connect(self._on_project_combo_changed)
        layout.addWidget(self.project_combo, 0, 3)
        layout.setColumnStretch(3, 1)

        # 第二行：新建按钮放在对应下拉框下面
        ws_btn_row = QHBoxLayout()
        new_ws_btn = QPushButton("+ 新建工作区")
        new_ws_btn.clicked.connect(self._create_workspace)
        ws_btn_row.addWidget(new_ws_btn)
        self._ws_widgets.append(new_ws_btn)
        if self._show_edit_workspace:
            self._edit_ws_btn = QPushButton("编辑")
            self._edit_ws_btn.clicked.connect(self._edit_workspace)
            self._edit_ws_btn.setEnabled(False)
            ws_btn_row.addWidget(self._edit_ws_btn)
            self._ws_widgets.append(self._edit_ws_btn)
        ws_btn_row.addStretch()
        layout.addLayout(ws_btn_row, 1, 1)

        proj_btn_row = QHBoxLayout()
        if self._show_new_project:
            self.new_proj_btn = QPushButton("+ 新建项目")
            self.new_proj_btn.clicked.connect(self._create_project)
            self.new_proj_btn.setEnabled(False)
            proj_btn_row.addWidget(self.new_proj_btn)
        if self._show_delete_project:
            self.del_proj_btn = QPushButton("删除")
            self.del_proj_btn.clicked.connect(self._delete_project)
            self.del_proj_btn.setEnabled(False)
            proj_btn_row.addWidget(self.del_proj_btn)
        proj_btn_row.addStretch()
        layout.addLayout(proj_btn_row, 1, 3)

    def _setup_vertical_layout(self):
        """垂直布局：工作区下拉 + 按钮 + 项目下拉 + 新建/删除项目按钮"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        ws_label = QLabel("工作区")
        ws_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(ws_label)
        self._ws_widgets.append(ws_label)

        self.workspace_combo = QComboBox()
        self.workspace_combo.addItem("（全部工作区）", None)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        layout.addWidget(self.workspace_combo)
        self._ws_widgets.append(self.workspace_combo)

        ws_btn_layout = QHBoxLayout()
        new_ws_btn = QPushButton("+ 新建工作区")
        new_ws_btn.clicked.connect(self._create_workspace)
        ws_btn_layout.addWidget(new_ws_btn)
        self._ws_widgets.append(new_ws_btn)

        if self._show_edit_workspace:
            self._edit_ws_btn = QPushButton("编辑")
            self._edit_ws_btn.clicked.connect(self._edit_workspace)
            self._edit_ws_btn.setEnabled(False)
            ws_btn_layout.addWidget(self._edit_ws_btn)
            self._ws_widgets.append(self._edit_ws_btn)

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

        ws_label = QLabel("工作区:")
        layout.addWidget(ws_label)
        self.workspace_combo = QComboBox()
        self.workspace_combo.setMinimumWidth(180)
        self.workspace_combo.addItem("（全部工作区）", None)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        layout.addWidget(self.workspace_combo)
        self._ws_widgets.append(ws_label)
        self._ws_widgets.append(self.workspace_combo)

        new_ws_btn = QPushButton("+ 新建工作区")
        new_ws_btn.clicked.connect(self._create_workspace)
        layout.addWidget(new_ws_btn)
        self._ws_widgets.append(new_ws_btn)

        if self._show_edit_workspace:
            self._edit_ws_btn = QPushButton("编辑")
            self._edit_ws_btn.clicked.connect(self._edit_workspace)
            self._edit_ws_btn.setEnabled(False)
            layout.addWidget(self._edit_ws_btn)
            self._ws_widgets.append(self._edit_ws_btn)

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
        # 未设置全局工作区且只有一个工作区时自动选中，
        # 避免停在「全部工作区」导致「新建项目」按钮不可用（用户无法创建项目）。
        if prev_id is None and len(workspaces) == 1:
            target_index = 1
        self.workspace_combo.setCurrentIndex(target_index)
        self.workspace_combo.blockSignals(False)
        # 同步编辑按钮与新建项目按钮启用状态：
        # 编辑需选中具体工作区；新建项目只要有工作区即可（未选中时会自动解析/弹选择）。
        if self._show_edit_workspace:
            self._edit_ws_btn.setEnabled(target_index > 0)
        if self._show_new_project:
            if self._show_workspace:
                self.new_proj_btn.setEnabled(len(workspaces) > 0)
            else:
                # 个人模式：新建项目由数据库归入 default 工作区，始终可用
                self.new_proj_btn.setEnabled(True)

    def _load_projects(self):
        """加载项目下拉，按当前选中工作区过滤（个人模式显示全部项目）"""
        prev_pid = get_current_project_id()
        if self._show_workspace:
            ws_id = self.workspace_combo.currentData()
            if ws_id is None:
                ws_id = get_current_workspace_id()
        else:
            # 个人模式：不按工作区过滤，全部项目均可见；
            # 不强制把会话工作区设为 default，避免旧项目被过滤而暂时不可见
            ws_id = None

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
        if self._show_new_project and self._show_workspace:
            self.new_proj_btn.setEnabled(self.workspace_combo.count() > 1)
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
        """同步删除项目按钮启用状态：团队模式需同时选中工作区和项目；
        个人模式（隐藏工作区控件）只需选中项目。"""
        if not self._show_delete_project:
            return
        ws_id = self.workspace_combo.currentData()
        self.del_proj_btn.setEnabled(
            bool(project_id) and (bool(ws_id) or not self._show_workspace)
        )

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
        if self._show_new_project and self._show_workspace:
            self.new_proj_btn.setEnabled(self.workspace_combo.count() > 1)

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
        # 直接重载下拉，避免视图不可见时依赖全局信号回环才刷新
        self._load_workspaces()
        self._load_projects()

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
        """打开新建项目对话框（自动确定目标工作区；个人模式归入 default 工作区）"""
        if self._show_workspace:
            ws_id = self._resolve_project_workspace()
            if ws_id is None:
                return
            # 下拉停在「全部工作区」时切到目标工作区，保证新建后列表过滤一致
            if self.workspace_combo.currentData() != ws_id:
                self._suppress_broadcast = True
                self.workspace_combo.blockSignals(True)
                for i in range(self.workspace_combo.count()):
                    if self.workspace_combo.itemData(i) == ws_id:
                        self.workspace_combo.setCurrentIndex(i)
                        break
                self.workspace_combo.blockSignals(False)
                self._suppress_broadcast = False
        else:
            # 个人模式：传入 workspace_id=None，由数据库自动创建/使用 default 工作区；
            # 不修改已有项目的 workspace_id
            ws_id = None
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
        if not (ok and name):
            return
        project = self._db_service.create_project(name=name, workspace_id=ws_id)
        # 新建后选中并广播
        self._load_projects()
        set_current_project(project.project_id)
        get_data_bus().emit_data_changed("projects_changed")

    def _resolve_project_workspace(self) -> Optional[str]:
        """确定新建项目应归属的工作区 ID；无法确定时返回 None。

        优先当前下拉选中项；停在「全部工作区」时：
        - 唯一工作区：直接采用；
        - 多个工作区：弹出选择对话框；
        - 没有工作区：提示先新建工作区。
        """
        ws_id = self.workspace_combo.currentData()
        if ws_id:
            return ws_id
        try:
            workspaces = self._db_service.get_workspaces()
        except Exception:
            workspaces = []
        if len(workspaces) == 1:
            return workspaces[0].workspace_id
        if len(workspaces) > 1:
            labels = [
                f"{w.name}  [{w.path}]" if w.path else w.name
                for w in workspaces
            ]
            choice, ok = QInputDialog.getItem(
                self, "选择工作区", "请选择新建项目所属的工作区：", labels, 0, False
            )
            if not ok:
                return None
            return workspaces[labels.index(choice)].workspace_id
        QMessageBox.information(
            self, "提示", "请先新建工作区，再新建项目（工作区是项目的容器）。"
        )
        return None

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
