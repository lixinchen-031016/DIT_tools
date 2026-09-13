"""主窗口与导航框架"""

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from DITWorkstation.App import config

# 功能模式开关：主窗口按「当前激活导航列表」构建
# （个人模式隐藏 log/report；极简模式仅保留 import）
from DITWorkstation.App.feature_flags import (
    get_active_nav_items,
    is_enabled,
    is_minimal_mode,
    is_nav_enabled,
)

# 导航配置（NAV_ITEMS / NAV_GROUPS / get_nav_index）已抽离到 App/navigation.py
# 作为单一事实源，消除 Views ↔ main_window 的循环依赖。
# 视图跳转请直接从 App.navigation 导入。
from DITWorkstation.App.navigation import NAV_GROUPS, get_nav_index

# 会话上下文（EventBus + 全局项目/工作区状态）已抽离到 App/session_context.py
# [DEPRECATED] 此处 re-export 仅为向后兼容；新代码应直接从 App.session_context 导入，
# 避免形成 main_window ↔ Views 的逻辑循环依赖。
from DITWorkstation.App.session_context import (
    get_data_bus,
    set_current_project,
    set_current_workspace,
)
from DITWorkstation.App.version import APP_VERSION
from DITWorkstation.Services.card_automation_service import CardAutomationService
from DITWorkstation.Services.card_identity import (
    CardBatchQueue,
    compute_card_fingerprint,
)
from DITWorkstation.Services.integrity_scheduler import (
    IntegrityScheduler,
    ScheduledTaskService,
)
from DITWorkstation.Services.volume_monitor import VolumeMonitor
from DITWorkstation.Utils import get_db_service, logger
from DITWorkstation.Utils.workers import WorkerThread
from DITWorkstation.Views.asset_info_view import AssetInfoView
from DITWorkstation.Views.backup_view import BackupView
from DITWorkstation.Views.first_run_wizard import SOP_GUIDE_TEXT, FirstRunWizard
from DITWorkstation.Views.media_import_view import MediaImportView
from DITWorkstation.Views.project_dashboard_view import ProjectDashboardView
from DITWorkstation.Views.raw_extraction_view import RawExtractionView
from DITWorkstation.Views.rename_view import RenameView
from DITWorkstation.Views.report_view import ReportView
from DITWorkstation.Views.search_view import SearchView
from DITWorkstation.Views.shooting_log_view import ShootingLogView
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS

# ===== 导航壳层布局常量（对应报告 §4.1 / §8.4）=====
NAV_WIDTH_EXPANDED = 220  # 完整导航宽度（显示文字）
NAV_WIDTH_COLLAPSED = 64  # 折叠后图标栏宽度（仅图标 + 单字占位）
NAV_AUTO_COLLAPSE_WIDTH = 1100  # 窗口窄于此宽度时自动折叠导航（§8.4）
CONTEXT_BAR_HEIGHT = 40  # 顶部上下文条高度

# 各导航项在列表中的图标：全部使用 Qt 内置标准图标（§5.4 不用 emoji），
# 折叠模式下作为图标占位显示。
_NAV_ICON_BY_KEY = {
    "dashboard": QStyle.StandardPixmap.SP_DesktopIcon,
    "import": QStyle.StandardPixmap.SP_DirOpenIcon,
    "backup": QStyle.StandardPixmap.SP_DriveHDIcon,
    "log": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "raw": QStyle.StandardPixmap.SP_FileIcon,
    "rename": QStyle.StandardPixmap.SP_DialogResetButton,
    "search": QStyle.StandardPixmap.SP_FileDialogContentsView,
    "asset_info": QStyle.StandardPixmap.SP_FileDialogInfoView,
    "report": QStyle.StandardPixmap.SP_DialogSaveButton,
}


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DIT工作站 {APP_VERSION} - 专业摄影数据管理")
        # 最小 1024x720（§8.4：适配小屏幕与系统缩放，不再固定 1200x800）
        self.setMinimumSize(1024, 720)
        self.resize(1400, 900)

        # 导航折叠状态（§8.4）：_nav_manually_toggled 记录用户是否手动切换过，
        # 手动折叠后窗口 resize 不再自动展开；折叠偏好持久化到 QSettings。
        self._nav_collapsed = False
        self._nav_manually_toggled = False
        self._nav_settings = QSettings("DITWorkstation", "mainwindow")

        self._setup_ui()
        self._apply_style()
        self._restore_nav_state()

    def _setup_ui(self):
        """设置界面"""
        self._setup_menu()

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部上下文条（§4.1）：折叠开关 + 工作区/项目上下文 + 任务中心/命令面板
        self._build_context_bar()

        # 下方内容区：左侧导航 + 视图栈
        content = QWidget()
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_navigation()
        self._build_view_stack()
        layout.addWidget(self.nav_list)
        layout.addWidget(self.stack, 1)

        outer.addWidget(self.context_bar)
        outer.addWidget(content, 1)

        bus = get_data_bus()
        self._build_shortcuts()
        self._build_status_bar(bus)
        self._build_background_services()

    def _build_navigation(self):
        """创建左侧导航列表：分组标题行 + 导航项（§4.1）。

        - 按 NAV_GROUPS 在组与组之间插入不可选中的分组标题行
          （Qt.NoItemFlags、12px 次要色）；未登记分组的 key 直接渲染。
        - 维护 self._row_to_stack_index / self._stack_index_to_row 映射：
          列表控件行号（含标题行）↔ 视图栈索引（= get_nav_index 语义）。
          所有 setCurrentRow 调用方一律经由映射转换，行为与改造前一致。
        """
        # 左侧导航栏（顺序由「当前激活导航列表」决定：
        # 团队 9 项 / 个人 7 项 / 极简 1 项）
        self.active_nav_items = get_active_nav_items()
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(NAV_WIDTH_EXPANDED)
        self.nav_list.setIconSize(QSize(20, 20))
        self.nav_list.setSpacing(2)

        self._row_to_stack_index: dict[int, int] = {}
        self._stack_index_to_row: dict[int, int] = {}
        self._header_rows: list[int] = []

        current_group: str | None = None
        for stack_index, (key, text, tooltip) in enumerate(self.active_nav_items):
            # 组名变化时插入分组标题行（§4.1）
            group = next((name for name, keys in NAV_GROUPS.items() if key in keys), "")
            if group and group != current_group:
                header = QListWidgetItem(group)
                # 不可选中、不可聚焦：点击无效果，currentRow 不会落在标题行
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header_font = QFont()
                header_font.setPixelSize(FONT_SIZE.SM)
                header.setFont(header_font)
                header.setForeground(QColor(COLOR.TEXT_SECONDARY))
                header.setSizeHint(QSize(NAV_WIDTH_EXPANDED, 36))
                self._header_rows.append(self.nav_list.count())
                self.nav_list.addItem(header)
            current_group = group

            item = QListWidgetItem(text)
            item.setIcon(
                self.style().standardIcon(
                    _NAV_ICON_BY_KEY.get(key, QStyle.StandardPixmap.SP_FileIcon)
                )
            )
            item.setToolTip(tooltip)
            item.setSizeHint(QSize(NAV_WIDTH_EXPANDED - 24, 40))
            item.setForeground(QColor(COLOR.SIDEBAR_TEXT))
            row = self.nav_list.count()
            self._row_to_stack_index[row] = stack_index
            self._stack_index_to_row[stack_index] = row
            self.nav_list.addItem(item)

        # 默认选中第一个可选中的导航行（栈索引 0 对应的行）
        self.nav_list.setCurrentRow(self._stack_index_to_row.get(0, 0))
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

    def _build_context_bar(self):
        """创建顶部上下文条（§4.1）：折叠开关 + 工作区/项目 + 任务中心/命令面板。

        - 左侧：导航折叠开关（QToolButton + Qt 标准图标）、「工作区：XXX · 项目：XXX」
          （与状态栏标签由同一事件处理器同步更新）。
        - 右侧：任务中心入口（仅 is_enabled("task_history") 时显示）与命令面板按钮。
        - 版本号不放上下文条（已由「关于」对话框承载，§4.1）。
        """
        self.context_bar = QWidget()
        self.context_bar.setObjectName("contextBar")
        self.context_bar.setFixedHeight(CONTEXT_BAR_HEIGHT)

        # 导航折叠开关（§8.4）：Qt 内置标准图标 + tooltip
        self.nav_toggle_btn = QToolButton(self.context_bar)
        self.nav_toggle_btn.setAutoRaise(True)
        self.nav_toggle_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView)
        )
        self.nav_toggle_btn.setToolTip("折叠 / 展开导航栏")
        self.nav_toggle_btn.setMinimumSize(28, 28)
        self.nav_toggle_btn.clicked.connect(self._toggle_nav_collapsed)

        # 工作区 / 项目上下文（与状态栏标签同步更新）
        self.context_label_workspace = QLabel("工作区: 未选择", self.context_bar)
        self.context_label_project = QLabel("项目: 未选择", self.context_bar)

        # 右侧：任务中心 + 命令面板（低干扰文字按钮，§7.1 工具层级）
        self.context_task_btn = QPushButton("任务中心", self.context_bar)
        self.context_task_btn.setToolTip("查看后台任务历史、错误摘要和可恢复任务")
        self.context_task_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context_task_btn.clicked.connect(self._show_task_center)
        self.context_task_btn.setVisible(is_enabled("task_history"))

        self.context_palette_btn = QPushButton("命令面板", self.context_bar)
        self.context_palette_btn.setToolTip("全局命令面板（Ctrl+K）")
        self.context_palette_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context_palette_btn.clicked.connect(self._show_command_palette)

        bar_layout = QHBoxLayout(self.context_bar)
        bar_layout.setContentsMargins(8, 4, 12, 4)
        bar_layout.setSpacing(8)
        bar_layout.addWidget(self.nav_toggle_btn)
        bar_layout.addWidget(self.context_label_workspace)
        bar_layout.addWidget(self.context_label_project)
        bar_layout.addStretch(1)
        bar_layout.addWidget(self.context_task_btn)
        bar_layout.addWidget(self.context_palette_btn)

    def _build_view_stack(self):
        """实例化视图并按当前功能模式填充内容栈。

        只实例化「当前激活导航列表」中出现的视图：
        - 团队模式：全部 9 个视图；
        - 个人模式：7 个（跳过 ShootingLogView / ReportView）；
        - 极简模式：仅 MediaImportView。
        未实例化的视图属性统一置为 None，避免不必要的数据库连接与后台线程；
        其余引用这些视图的逻辑（后台服务、菜单、快捷键、关闭检查）均已按
        None 安全兜底。
        """
        factories = {
            "dashboard": ProjectDashboardView,
            "import": MediaImportView,
            "backup": BackupView,
            "log": ShootingLogView,
            "raw": RawExtractionView,
            "rename": RenameView,
            "search": SearchView,
            "asset_info": AssetInfoView,
            "report": ReportView,
        }
        # key → 实例属性名（供测试与外部代码按名称访问）
        attr_by_key = {
            "dashboard": "dashboard_view",
            "import": "import_view",
            "backup": "backup_view",
            "log": "log_view",
            "raw": "raw_view",
            "rename": "rename_view",
            "search": "search_view",
            "asset_info": "asset_info_view",
            "report": "report_view",
        }

        self.stack = QStackedWidget()
        # 先统一置空，避免新模式遗漏某个属性导致 AttributeError
        for attr in attr_by_key.values():
            setattr(self, attr, None)
        self.view_by_key = {}

        # addWidget 顺序与 active_nav_items 一致（索引即导航行号）
        for key, _text, _tooltip in self.active_nav_items:
            factory = factories.get(key)
            if factory is None:
                continue
            view = factory()
            setattr(self, attr_by_key[key], view)
            self.view_by_key[key] = view
            self.stack.addWidget(self._wrap_scrollable(view))

    def _build_shortcuts(self):
        """注册全局导航、刷新和取消快捷键。"""

        def add_shortcut(sequence, callback):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)

        add_shortcut("Ctrl+I", self._focus_import)
        # Ctrl+F / Ctrl+B 仅目标页在当前模式激活时注册，避免访问未实例化视图
        if is_nav_enabled("search"):
            add_shortcut("Ctrl+F", self._focus_search)
        if is_nav_enabled("backup"):
            add_shortcut("Ctrl+B", self._focus_backup)
        # Ctrl+L 跳拍摄日志仅团队模式注册（个人/极简模式无日志页）
        if is_nav_enabled("log"):
            add_shortcut("Ctrl+L", self._focus_log)
        # Ctrl+1~N 切换到对应导航页（N = 激活导航项数量：团队 9 / 个人 7 / 极简 1）
        # 索引为「激活导航列表中的位置」（栈索引），经映射转换为列表控件行号
        for i in range(1, len(self.active_nav_items) + 1):
            add_shortcut(f"Ctrl+{i}", lambda idx=i - 1: self._select_stack_index(idx))
        add_shortcut("F5", self._refresh_current_view)
        add_shortcut("Ctrl+K", self._show_command_palette)
        add_shortcut("Esc", self._cancel_running_workers)

    def _build_status_bar(self, bus):
        """创建状态栏并绑定会话上下文事件。"""
        self.status_bar = self.statusBar()  # QMainWindow 自带 statusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {COLOR.BG_GROUP};
                border-top: 1px solid {COLOR.BORDER};
                color: {COLOR.TEXT_SECONDARY};
                font-size: {FONT_SIZE.SM}px;
                padding: 2px 12px;
            }}
        """)
        self.status_label_workspace = QLabel("工作区: 未选择")
        self.status_label_project = QLabel("项目: 未选择")
        self.status_label_task = QLabel("就绪")
        # 版本号已从状态栏移除（§4.1）：由「帮助 → 关于」对话框承载
        self.status_bar.addWidget(self.status_label_workspace)
        self.status_bar.addWidget(self.status_label_project)
        self.status_bar.addPermanentWidget(self.status_label_task)
        bus.project_focus_changed.connect(self._on_project_focus_changed)
        bus.workspace_focus_changed.connect(self._on_workspace_focus_changed)

    def _build_background_services(self):
        """启动任务状态轮询和存储卡监控。"""
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._update_task_status)
        self._status_timer.start()
        self._update_task_status()

        # 存储卡监控保留全部模式：极简模式下「检测到存储卡→跳转导入」是核心辅助能力
        self.volume_monitor = VolumeMonitor(self)
        self.volume_monitor.volume_mounted.connect(self._on_volume_mounted)
        self.volume_monitor.start()

        self.card_automation_worker = None
        self.card_automation_source_path = None
        # 相机卡自动化依赖备份视图（项目/备份方案模板查询），且受特性开关约束。
        # 极简模式无备份视图且禁用 card_automation，此处安全跳过。
        self.card_automation_service = None
        self.card_batch_queue = None
        if is_enabled("card_automation") and self.backup_view is not None:
            self.card_automation_service = CardAutomationService(
                self.backup_view.db_service
            )
            # 多卡批处理队列
            self.card_batch_queue = CardBatchQueue(
                start_cb=self._start_card_from_queue,
                dedupe=bool(getattr(config, "skip_processed_cards", True)),
            )
        # 完整性校验定时调度
        self._integrity_scheduler = None
        self._init_integrity_scheduler()

    def _setup_menu(self):
        """创建菜单栏 — 提供帮助入口与新手向导重启"""
        menubar = self.menuBar()

        # 设置菜单
        settings_menu = menubar.addMenu("设置(&S)")
        settings_action = settings_menu.addAction("设置…")
        settings_action.setShortcut("Ctrl+,")
        settings_action.setToolTip("缩略图缓存清理、最近路径管理与数据目录信息")
        settings_action.triggered.connect(self._show_settings)

        recycle_action = settings_menu.addAction("回收站…")
        recycle_action.setToolTip("查看并恢复保留期内删除的项目和素材记录")
        recycle_action.triggered.connect(self._show_recycle_bin)
        # 极简模式仅保留媒体导入，回收站属于管理类入口，一并隐藏
        recycle_action.setVisible(not is_minimal_mode())

        log_viewer_action = settings_menu.addAction("日志查看器…")
        log_viewer_action.setToolTip("在应用内查看日志文件内容")
        log_viewer_action.triggered.connect(self._show_log_viewer)

        restore_action = settings_menu.addAction("从备份恢复素材…")
        restore_action.setToolTip("把已备份素材从备份卷复制回工作盘（回拷）")
        restore_action.triggered.connect(self._show_restore_wizard)
        # 依赖备份视图：备份页未激活时（极简模式）隐藏入口。
        # 菜单构建早于视图栈实例化，故按导航可用性判断而非视图实例。
        restore_action.setVisible(is_nav_enabled("backup"))

        verify_action = settings_menu.addAction("立即完整性校验…")
        verify_action.setToolTip("手动触发一次备份完整性校验")
        verify_action.triggered.connect(self._trigger_integrity_check)
        verify_action.setVisible(is_nav_enabled("backup"))

        task_history_action = settings_menu.addAction("任务中心…")
        task_history_action.setToolTip("查看后台任务历史、错误摘要和可恢复任务")
        task_history_action.triggered.connect(self._show_task_center)
        task_history_action.setVisible(is_enabled("task_history"))

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")

        restart_action = help_menu.addAction("重新启动新手向导...")
        restart_action.setShortcut("Ctrl+Shift+H")
        restart_action.setToolTip(
            "重新打开首次启动向导，回顾工作区/项目创建流程与 SOP 说明"
        )
        restart_action.triggered.connect(self._restart_wizard)

        sop_action = help_menu.addAction("SOP 操作链说明...")
        sop_action.setToolTip("查看完整的标准操作流程说明")
        sop_action.triggered.connect(self._show_sop_guide)
        # 个人模式隐藏 SOP 团队引导（其文案包含日志/报告等团队流程）
        sop_action.setVisible(is_enabled("sop_guide"))

        shortcuts_action = help_menu.addAction("快捷键大全...")
        shortcuts_action.setToolTip("查看所有全局快捷键")
        shortcuts_action.triggered.connect(self._show_shortcuts)

        help_menu.addSeparator()

        about_action = help_menu.addAction("关于 DIT 工作站")
        about_action.triggered.connect(self._show_about)

    def _restart_wizard(self):
        """重新启动新手向导，允许用户回顾 SOP 并创建新的工作区/项目"""
        wizard = FirstRunWizard(self)
        wizard.exec()
        # 如果向导创建了工作区/项目，设为全局当前并跳转到媒体导入
        if wizard._created_workspace_id:
            set_current_workspace(wizard._created_workspace_id)
        if wizard._created_project_id:
            set_current_project(wizard._created_project_id)
            # get_nav_index 在目标页未激活时返回 None，禁止直接传给 setCurrentRow
            import_idx = get_nav_index("import")
            if import_idx is not None:
                self._select_stack_index(import_idx)

    def _show_sop_guide(self):
        """弹出 SOP 操作链说明对话框"""
        QMessageBox.information(self, "SOP 操作链说明", SOP_GUIDE_TEXT)

    def _show_shortcuts(self):
        """弹出快捷键大全对话框（内容随功能模式裁剪）"""
        nav_count = len(self.active_nav_items)
        lines = [
            "全局快捷键：",
            f"• Ctrl+1~{nav_count}：切换到对应导航页",
            "• Ctrl+I：跳转到媒体导入",
        ]
        if is_nav_enabled("search"):
            lines.append("• Ctrl+F：跳转到素材检索")
        if is_nav_enabled("backup"):
            lines.append("• Ctrl+B：跳转到数据备份")
        lines += [
            "• Ctrl+,：打开设置对话框",
            "• Ctrl+K：打开命令面板（运行路径/URL/搜索跳转）",
        ]
        # Ctrl+L 跳拍摄日志仅团队模式可用
        if is_nav_enabled("log"):
            lines.append("• Ctrl+L：跳转到拍摄日志")
        lines += [
            "• Ctrl+Shift+H：重新启动新手向导",
            "• F5：刷新当前视图",
            "• Esc：取消当前后台任务",
        ]
        QMessageBox.information(self, "快捷键大全", "\n".join(lines))

    def _show_about(self):
        """关于对话框"""
        QMessageBox.about(
            self,
            "关于 DIT 工作站",
            "<h3>DIT 工作站</h3>"
            f"<p>版本：{APP_VERSION}</p>"
            "<p>专业摄影数据管理应用</p>"
            "<p>支持安全备份、校验和验证、JPG 筛选 RAW 提取、批量重命名、"
            "拍摄日志管理、素材检索与报告生成。</p>",
        )

    def _show_settings(self):
        """打开设置对话框"""
        from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self)
        dialog.exec()

    def _show_recycle_bin(self):
        """打开数据库回收站；恢复操作完成后会广播数据刷新事件。"""
        from DITWorkstation.Views.Widgets.recycle_bin_dialog import RecycleBinDialog

        RecycleBinDialog(self, db_service=get_db_service()).exec()

    def _show_log_viewer(self):
        """打开日志查看器对话框。"""
        from DITWorkstation.Views.Widgets.log_viewer_dialog import LogViewerDialog

        LogViewerDialog(self).exec()

    def _show_restore_wizard(self):
        """打开「从备份恢复素材」向导。"""
        if self.backup_view is None:
            return
        from DITWorkstation.Views.Widgets.restore_wizard import RestoreWizard

        RestoreWizard(self, db_service=self.backup_view.db_service).exec()

    def _trigger_integrity_check(self):
        """手动触发一次完整性校验（立即执行，走任务线程避免卡 UI）。"""
        if self.backup_view is None:
            return
        from DITWorkstation.Services.integrity_scheduler import IntegrityScheduler

        integrity = IntegrityScheduler(
            self.backup_view.backup_service, self.backup_view.db_service
        )
        worker = WorkerThread(integrity.run_all_projects)
        self._integrity_worker = worker

        def _done(results):
            self._set_task_status("完整性校验完成", COLOR.SUCCESS)
            total = len(results) if isinstance(results, dict) else 0
            QMessageBox.information(
                self,
                "完整性校验",
                f"校验完成，共检查 {total} 个项目。\n详细结果已写入操作审计日志。",
            )

        def _err(error):
            QMessageBox.warning(self, "校验失败", str(error))

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.thread_finished.connect(worker.deleteLater)
        worker.start()
        self._set_task_status("正在执行完整性校验…", COLOR.WARNING)

    def _show_task_center(self):
        """打开后台任务历史中心。"""
        from DITWorkstation.Views.Widgets.task_history_dialog import TaskHistoryDialog

        TaskHistoryDialog(self).exec()

    def _wrap_scrollable(self, view: QWidget) -> QScrollArea:
        """把视图包裹在 QScrollArea 中，内容超出时自动出现滚动条。

        - setWidgetResizable(True)：视图随 ScrollArea 缩放，避免内容被裁剪
        - NoFrame：去除 ScrollArea 边框，与 QStackedWidget 背景融合
        - 透明背景：让视图自身的背景样式生效
        """
        scroll = QScrollArea()
        scroll.setWidget(view)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        return scroll

    def _on_nav_changed(self, row: int):
        """导航切换：列表行号 → 视图栈索引（分组标题行无映射，安全忽略）。"""
        stack_index = self._row_to_stack_index.get(row)
        if stack_index is None:
            return
        self.stack.setCurrentIndex(stack_index)

    def _select_stack_index(self, stack_index: int):
        """按「激活导航列表中的位置」（栈索引）选中导航行。

        get_nav_index 返回的索引即栈索引；经 _stack_index_to_row 转换为
        列表控件行号后再 setCurrentRow，确保含分组标题行时行号正确。
        """
        row = self._stack_index_to_row.get(stack_index)
        if row is not None:
            self.nav_list.setCurrentRow(row)

    def _focus_search(self):
        """Ctrl+F: 跳转到素材检索并聚焦搜索框"""
        if self.search_view is None:
            return
        self._navigate_to("search")
        self.search_view.keyword_edit.setFocus()

    def _focus_import(self):
        """Ctrl+I: 跳转到媒体导入"""
        self._navigate_to("import")

    def _focus_backup(self):
        """Ctrl+B: 跳转到数据备份"""
        self._navigate_to("backup")

    def _focus_log(self):
        """Ctrl+L: 跳转到拍摄日志"""
        self._navigate_to("log")

    def _navigate_to(self, key: str):
        """按 key 跳转到对应导航页"""
        idx = get_nav_index(key)
        if idx is not None:
            self._select_stack_index(idx)

    def _refresh_current_view(self):
        """F5: 刷新当前视图（索引基于激活导航列表，不会访问隐藏页面）"""
        idx = self.stack.currentIndex()
        if 0 <= idx < len(self.active_nav_items):
            key = self.active_nav_items[idx][0]
            view = self.view_by_key.get(key)
            if view and hasattr(view, "_trigger_refresh_now"):
                view._trigger_refresh_now()

    def _cancel_running_workers(self):
        """Esc: 取消当前正在运行的后台 worker"""
        running = self._running_workers()
        if not running:
            return
        for _view, _attr, w in running:
            try:
                # 优先用协作式中断，worker 自行检查 isInterruptionRequested()
                if hasattr(w, "requestInterruption"):
                    w.requestInterruption()
                elif hasattr(w, "quit"):
                    w.quit()
            except Exception as e:
                logger.warning(f"取消 worker 失败: {e}")

    def _running_workers(self):
        """收集所有视图中仍在运行的后台 worker。

        用于关闭窗口前判断是否有未完成的长耗时操作（备份/导入/RAW提取/重命名/报告/批量EXIF），
        避免强杀 QThread 导致 DB 写半截或文件拷贝残留。
        个人模式隐藏 log/report，极简模式仅实例化媒体导入视图，
        其余视图为 None，此处 getattr/None 判断安全跳过。
        """
        candidates = []
        for view, attr in (
            (self.backup_view, "worker"),
            (self.import_view, "worker"),
            (self.raw_view, "worker"),
            (self.rename_view, "_worker"),
            (self.report_view, "worker"),
            (self.asset_info_view, "_batch_worker"),
        ):
            if view is None:
                continue
            w = getattr(view, attr, None)
            if w is not None and w.isRunning():
                candidates.append((view, attr, w))
        return candidates

    def _on_project_focus_changed(self, project_id):
        """当前项目切换时更新状态栏与顶部上下文条"""
        if not project_id:
            text = "项目: 未选择"
            self.status_label_project.setText(text)
            self.context_label_project.setText(text)
            return
        try:
            db = get_db_service()
            project = db.get_project(project_id)
            name = project.name if project else "未知"
            text = f"项目: {name}"
        except Exception:
            text = "项目: ?"
        self.status_label_project.setText(text)
        self.context_label_project.setText(text)

    def _on_workspace_focus_changed(self, workspace_id):
        """当前工作区切换时更新状态栏与顶部上下文条"""
        if not workspace_id:
            text = "工作区: 未选择"
            self.status_label_workspace.setText(text)
            self.context_label_workspace.setText(text)
            return
        try:
            db = get_db_service()
            ws = db.get_workspace(workspace_id)
            name = ws.name if ws else "未知"
            text = f"工作区: {name}"
        except Exception:
            text = "工作区: ?"
        self.status_label_workspace.setText(text)
        self.context_label_workspace.setText(text)

    def _update_task_status(self):
        """定时更新后台任务数指示（由 _status_timer 每 2 秒触发）"""
        running = self._running_workers()
        count = len(running)
        if count > 0:
            self._set_task_status(f"后台任务: {count} 个", COLOR.WARNING, bold=True)
        else:
            self._set_task_status("就绪")

    def _set_task_status(self, text: str, color: str | None = None, bold: bool = False):
        """更新状态栏任务指示：纯文字 + 颜色语义（§5.4/§9 不用 emoji）。

        Args:
            text: 状态文字。
            color: 语义色（WARNING/SUCCESS/DANGER），None 时用次要文本色。
            bold: 是否加粗（运行中状态）。
        """
        self.status_label_task.setText(text)
        weight = "600" if bold else "400"
        self.status_label_task.setStyleSheet(
            f"color: {color or COLOR.TEXT_SECONDARY}; font-weight: {weight};"
        )

    def closeEvent(self, event):
        """关闭窗口前检查后台 worker，避免强杀线程导致数据写半截。

        - 无运行中的 worker：直接关闭
        - 有运行中的 worker：弹确认框，用户确认后等待各 worker 最多 5 秒再关闭；
          用户取消则忽略关闭事件
        """
        running = self._running_workers()
        card_running = (
            self.card_automation_worker and self.card_automation_worker.isRunning()
        )
        if not running and not card_running:
            self.volume_monitor.stop()
            super().closeEvent(event)
            return
        if not running and card_running:
            self.volume_monitor.stop()
            self.card_automation_worker.cancel()
            self.card_automation_worker.wait(5000)
            super().closeEvent(event)
            return

        names = []
        view_name_map = {}
        for v, name in (
            (self.backup_view, "数据备份"),
            (self.import_view, "媒体导入"),
            (self.raw_view, "RAW提取"),
            (self.rename_view, "文件重命名"),
            (self.report_view, "报告生成"),
            (self.asset_info_view, "批量EXIF读取"),
        ):
            if v is not None:
                view_name_map[id(v)] = name
        for view, _attr, _w in running:
            names.append(view_name_map.get(id(view), "后台任务"))
        task_text = "、".join(names)

        # 自定义按钮文案，避免 Qt 默认 Yes/No/Cancel 英文按钮
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("确认退出")
        box.setText(
            f"以下任务正在后台执行：{task_text}\n\n"
            "强制退出可能导致数据写不完整或文件拷贝残留。\n"
            "· 点击「等待完成」将等待最多 5 秒后退出\n"
            "· 点击「立即退出」可能产生残留文件\n"
            "· 点击「取消」返回程序继续操作"
        )
        wait_btn = box.addButton("等待完成", QMessageBox.AcceptRole)
        box.addButton("立即退出", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.exec()

        if box.clickedButton() is cancel_btn:
            event.ignore()
            return

        if box.clickedButton() is wait_btn:
            # 尝试等待各 worker 结束（最多 5 秒），避免半截写入
            for _view, _attr, w in running:
                try:
                    w.wait(5000)
                except Exception as e:
                    logger.debug(f"等待 worker 结束失败: {e}")
            logger.info(f"主窗口关闭：已等待 {len(running)} 个后台 worker 结束")

        self.volume_monitor.stop()
        if card_running:
            self.card_automation_worker.cancel()
            self.card_automation_worker.wait(5000)
        super().closeEvent(event)

    def _start_card_automation(self, source_path: str):
        """按设置启动一次相机卡自动任务；同一时间只允许一个自动任务。"""
        # 极简模式：相机卡自动化不可用（无备份视图、无自动化服务）
        if self.card_automation_service is None or self.backup_view is None:
            return
        if self.card_automation_worker and self.card_automation_worker.isRunning():
            self._set_task_status("已有相机卡自动任务正在执行", COLOR.WARNING)
            return
        project_id = getattr(config, "auto_card_project_id", "")
        template_id = getattr(config, "auto_card_template_id", "")
        do_import = getattr(config, "auto_card_import", True)
        do_backup = getattr(config, "auto_card_backup", False)
        steps = getattr(config, "auto_card_steps", None) or None
        template = (
            self.backup_view.db_service.get_backup_template(template_id)
            if template_id
            else None
        )
        if not project_id or (
            (do_backup or (steps and "backup" in steps)) and template is None
        ):
            self._set_task_status("相机卡自动化配置不完整，请检查设置", COLOR.WARNING)
            logger.warning(
                f"相机卡自动化配置不完整: project={project_id} template={template_id}"
            )
            return
        project = self.backup_view.db_service.get_project(project_id)
        if project is None:
            self._set_task_status("自动化项目不存在，请检查设置", COLOR.WARNING)
            return
        set_current_project(project_id)
        self.card_automation_source_path = source_path
        self.card_automation_worker = WorkerThread(
            self.card_automation_service.execute,
            source_path,
            project_id,
            template=template,
            do_import=do_import,
            do_backup=do_backup,
            steps=steps,
            raw_config={
                "output_folder": getattr(config, "auto_card_raw_output_dir", "")
            }
            if steps and "raw_extract" in steps
            else None,
            rename_config={"rule": {"pattern": config.auto_card_rename_pattern}}
            if steps
            and "rename" in steps
            and getattr(config, "auto_card_rename_pattern", "")
            else None,
            report_path=getattr(config, "auto_card_report_path", "") or None,
            inject_progress=True,
            inject_cancel_check=True,
        )
        self.card_automation_worker.progress.connect(self._on_card_automation_progress)
        self.card_automation_worker.finished.connect(self._on_card_automation_finished)
        self.card_automation_worker.error.connect(self._on_card_automation_error)
        self.card_automation_worker.thread_finished.connect(
            self.card_automation_worker.deleteLater
        )
        self.card_automation_worker.start()
        self._set_task_status(
            f"自动处理相机卡: {Path(source_path).name}", COLOR.WARNING
        )

    @Slot(str, float, str)
    def _on_card_automation_progress(self, target: str, progress: float, message: str):
        self._set_task_status(f"{message} ({int(progress * 100)}%)", COLOR.WARNING)

    def _show_command_palette(self):
        """打开 Ctrl+K 全局命令面板。"""
        from DITWorkstation.Views.Widgets.command_palette import CommandPalette

        cp = CommandPalette(self)
        cp.exec()

    def _start_card_from_queue(self, path: str):
        """从多卡队列取出一张卡启动自动化（与单卡触发走同一流程）。"""
        self._start_card_automation(path)

    def _on_volume_mounted(self, path: str):
        """检测到新存储卡：跳转导入视图，并按需入多卡自动队列。"""
        if getattr(config, "auto_detect_volume", True):
            self._set_task_status(
                f"检测到存储卡: {Path(path).name or path}", COLOR.WARNING
            )
            self._navigate_to("import")
            if self.import_view is not None:
                self.import_view.set_source_folder(path, auto_scan=True)
        # 队列仅在相机卡自动化可用时创建（极简模式为 None）
        if (
            self.card_batch_queue is not None
            and getattr(config, "auto_card_automation_enabled", False)
            and is_enabled("card_automation")
        ):
            self.card_batch_queue.enqueue(path)

    def _on_card_automation_finished(self, result):
        self.card_automation_worker = None
        # 多卡队列：记录指纹并派发下一张
        if self.card_automation_source_path and self.card_batch_queue is not None:
            fp = compute_card_fingerprint(self.card_automation_source_path)
            self.card_batch_queue.on_finished(fingerprint=fp)
        backup = result.get("backup") if isinstance(result, dict) else None
        imported = (
            (result.get("import") or {}).get("imported", 0)
            if isinstance(result, dict)
            else 0
        )
        backup_text = ""
        if backup is not None:
            backup_text = f"，备份状态 {backup.status.value}"
        self._set_task_status(
            f"相机卡自动处理完成：导入 {imported} 个{backup_text}", COLOR.SUCCESS
        )
        if imported:
            get_data_bus().emit_data_changed("assets_changed")

    def _on_card_automation_error(self, error: str):
        self.card_automation_worker = None
        if self.card_automation_source_path and self.card_batch_queue is not None:
            self.card_batch_queue.on_failed()
        self._set_task_status(f"相机卡自动处理失败: {error}", COLOR.DANGER)
        logger.error(f"相机卡自动处理失败: {error}")

    def _init_integrity_scheduler(self):
        """启动完整性校验定时调度（按配置间隔）。

        依赖备份视图；极简模式未实例化备份视图时安全跳过。
        """
        if self.backup_view is None:
            return
        interval = int(getattr(config, "integrity_check_interval_hours", 0) or 0)
        if interval <= 0:
            return
        try:
            scheduler = ScheduledTaskService()
            integrity = IntegrityScheduler(
                self.backup_view.backup_service,
                self.backup_view.db_service,
                scheduler,
            )
            integrity.setup(interval_hours=interval)
            scheduler.start()
            self._integrity_scheduler = scheduler
            logger.info(f"完整性校验调度已启动，间隔 {interval} 小时")
        except Exception as exc:
            logger.warning(f"启动完整性校验调度失败: {exc}")

    # ===== 导航折叠（§8.4 响应式）=====

    def _toggle_nav_collapsed(self):
        """折叠开关点击：切换导航宽度并标记为用户手动操作（不再自动展开）。"""
        self._nav_manually_toggled = True
        self._set_nav_collapsed(not self._nav_collapsed, persist=True)

    def _set_nav_collapsed(self, collapsed: bool, persist: bool = False):
        """切换导航折叠状态。

        - 完整模式：宽度 220px，显示完整文字与分组标题行；
        - 折叠模式：宽度 64px，导航项仅显示首字占位（配合 Qt 标准图标），
          分组标题行隐藏（setRowHidden）。
        - persist=True 时写入 QSettings（用户手动切换才持久化；
          窗口过窄触发的自动折叠为临时状态，不写配置）。
        """
        if collapsed == self._nav_collapsed and persist is False:
            return
        self._nav_collapsed = collapsed
        self.nav_list.setFixedWidth(
            NAV_WIDTH_COLLAPSED if collapsed else NAV_WIDTH_EXPANDED
        )
        for row in self._header_rows:
            self.nav_list.setRowHidden(row, collapsed)
        for row, stack_index in self._row_to_stack_index.items():
            item = self.nav_list.item(row)
            if item is None:
                continue
            text = self.active_nav_items[stack_index][1]
            # 折叠时仅显示首字占位（§8.4：不用 emoji，配合标准图标）
            item.setText(text[0] if collapsed else text)
        if persist:
            try:
                self._nav_settings.setValue("nav_collapsed", collapsed)
            except Exception as exc:
                logger.debug(f"持久化导航折叠状态失败: {exc}")

    def _restore_nav_state(self):
        """启动时从 QSettings 恢复上次的导航折叠状态。"""
        try:
            collapsed = self._nav_settings.value("nav_collapsed", False, type=bool)
        except Exception:
            collapsed = False
        if collapsed:
            self._set_nav_collapsed(True)

    def resizeEvent(self, event):
        """窗口尺寸变化（§8.4）：宽度 <1100 自动折叠；恢复宽度且用户未手动
        操作过折叠时自动展开。"""
        super().resizeEvent(event)
        # __init__ 早期 resize 触发时导航尚未构建，安全跳过
        if getattr(self, "nav_list", None) is None:
            return
        if self.width() < NAV_AUTO_COLLAPSE_WIDTH:
            if not self._nav_collapsed:
                self._set_nav_collapsed(True)
        elif self._nav_collapsed and not self._nav_manually_toggled:
            self._set_nav_collapsed(False)

    def _apply_style(self):
        """应用样式（主窗口 + 侧栏 + 上下文条）。全局 QSS 由 main.py 通过 theme.apply_global_style 注入。"""
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {COLOR.BG_APP};
            }}
            QListWidget {{
                background-color: {COLOR.SIDEBAR_BG};
                border: none;
                padding-top: 20px;
                font-size: {FONT_SIZE.MD}px;
            }}
            /* 分组标题行由 item 级 ForegroundRole 控制次要色，
               故基础规则不设 color（避免 QSS 覆盖标题行颜色） */
            QListWidget::item {{
                padding: 9px 14px;
                border-radius: {RADIUS.BUTTON}px;
                margin: 2px 8px;
                border-left: 3px solid transparent;
            }}
            QListWidget::item:selected {{
                background-color: {COLOR.SIDEBAR_HOVER};
                color: {COLOR.SIDEBAR_TEXT};
                border-left: 3px solid {COLOR.PRIMARY};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {COLOR.SIDEBAR_HOVER};
            }}
            QStackedWidget {{
                background-color: {COLOR.BG_CARD};
            }}
        """)
        # 顶部上下文条（§4.1）：卡片底色 + 底部 1px 分隔，右侧为低干扰文字按钮
        self.context_bar.setStyleSheet(f"""
            QWidget#contextBar {{
                background-color: {COLOR.BG_CARD};
                border-bottom: 1px solid {COLOR.BORDER};
            }}
            QWidget#contextBar QLabel {{
                color: {COLOR.TEXT_SECONDARY};
                font-size: {FONT_SIZE.SM}px;
                background: transparent;
            }}
            QWidget#contextBar QToolButton {{
                background: transparent;
                border: none;
                border-radius: {RADIUS.INPUT}px;
                padding: 4px;
            }}
            QWidget#contextBar QToolButton:hover {{
                background-color: {COLOR.BG_GROUP};
            }}
            QWidget#contextBar QPushButton {{
                background: transparent;
                border: none;
                border-radius: {RADIUS.INPUT}px;
                color: {COLOR.TEXT_SECONDARY};
                font-size: {FONT_SIZE.SM}px;
                padding: 4px 10px;
            }}
            QWidget#contextBar QPushButton:hover {{
                background-color: {COLOR.BG_GROUP};
                color: {COLOR.TEXT_PRIMARY};
            }}
        """)
