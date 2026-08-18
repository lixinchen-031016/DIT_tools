"""主窗口与导航框架"""
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QWidget,
)

from DITWorkstation.App import config

# 功能模式开关：主窗口按「当前激活导航列表」构建（个人模式隐藏 log/report）
from DITWorkstation.App.feature_flags import (
    get_active_nav_items,
    is_enabled,
    is_nav_enabled,
)

# 导航配置（NAV_ITEMS / get_nav_index）已抽离到 App/navigation.py 作为单一事实源，
# 消除 Views ↔ main_window 的循环依赖。视图跳转请直接从 App.navigation 导入。
from DITWorkstation.App.navigation import get_nav_index

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
from DITWorkstation.Services.volume_monitor import VolumeMonitor
from DITWorkstation.Utils import get_db_service, logger
from DITWorkstation.Utils.workers import WorkerThread
from DITWorkstation.Views.asset_info_view import AssetInfoView
from DITWorkstation.Views.backup_view import BackupView
from DITWorkstation.Views.first_run_wizard import _SOP_GUIDE_TEXT, FirstRunWizard
from DITWorkstation.Views.media_import_view import MediaImportView
from DITWorkstation.Views.project_dashboard_view import ProjectDashboardView
from DITWorkstation.Views.raw_extraction_view import RawExtractionView
from DITWorkstation.Views.rename_view import RenameView
from DITWorkstation.Views.report_view import ReportView
from DITWorkstation.Views.search_view import SearchView
from DITWorkstation.Views.shooting_log_view import ShootingLogView
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DIT工作站 {APP_VERSION} - 专业摄影数据管理")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        self._setup_ui()
        self._apply_style()

    def _setup_ui(self):
        """设置界面"""
        self._setup_menu()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_navigation()
        self._build_view_stack()
        layout.addWidget(self.nav_list)
        layout.addWidget(self.stack, 1)

        bus = get_data_bus()
        bus.data_changed.connect(self._on_data_changed)
        self._build_shortcuts()
        self._build_status_bar(bus)
        self._build_background_services()

    def _build_navigation(self):
        """创建左侧导航列表。"""
        # 左侧导航栏（顺序由「当前激活导航列表」决定：个人模式隐藏 log/report）
        self.active_nav_items = get_active_nav_items()
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(200)
        self.nav_list.setIconSize(QSize(24, 24))
        self.nav_list.setSpacing(4)

        for _key, text, tooltip in self.active_nav_items:
            item = QListWidgetItem(text)
            item.setToolTip(tooltip)
            item.setSizeHint(QSize(180, 44))
            self.nav_list.addItem(item)

        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

    def _build_view_stack(self):
        """实例化视图并按当前功能模式填充内容栈。"""
        # 右侧内容区：第一版不做视图懒加载，仍实例化全部视图
        # （关闭事件、后台 worker、数据总线和跨视图跳转直接引用视图属性，
        #  不实例化隐藏视图会扩大改动范围并可能遗漏后台任务收尾），
        # 但只把激活视图加入导航和 QStackedWidget。
        self.stack = QStackedWidget()
        self.dashboard_view = ProjectDashboardView()
        self.import_view = MediaImportView()
        self.backup_view = BackupView()
        self.raw_view = RawExtractionView()
        self.rename_view = RenameView()
        self.log_view = ShootingLogView()
        self.search_view = SearchView()
        self.asset_info_view = AssetInfoView()
        self.report_view = ReportView()

        # key → 视图映射：F5 刷新、视图栈填充等索引逻辑统一经由此映射
        self.view_by_key = {
            "dashboard": self.dashboard_view,
            "import": self.import_view,
            "backup": self.backup_view,
            "log": self.log_view,
            "raw": self.raw_view,
            "rename": self.rename_view,
            "search": self.search_view,
            "asset_info": self.asset_info_view,
            "report": self.report_view,
        }

        # 统一用 QScrollArea 包裹视图，保证内容超出窗口时出现滚动条。
        # addWidget 顺序与 active_nav_items 顺序保持一致（索引即导航行号）。
        for key, _text, _tooltip in self.active_nav_items:
            self.stack.addWidget(self._wrap_scrollable(self.view_by_key[key]))

    def _build_shortcuts(self):
        """注册全局导航、刷新和取消快捷键。"""
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+I"), self, activated=self._focus_import)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self._focus_backup)
        # Ctrl+L 跳拍摄日志仅团队模式注册（个人模式无日志页）
        if is_nav_enabled("log"):
            QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_log)
        # Ctrl+1~N 切换到对应导航页（N = 激活导航项数量，个人模式为 7）
        for i in range(1, len(self.active_nav_items) + 1):
            QShortcut(
                QKeySequence(f"Ctrl+{i}"), self,
                activated=lambda idx=i - 1: self.nav_list.setCurrentRow(idx),
            )
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_current_view)
        QShortcut(QKeySequence("Esc"), self, activated=self._cancel_running_workers)

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
        self.status_label_version = QLabel(APP_VERSION)
        self.status_bar.addWidget(self.status_label_workspace)
        self.status_bar.addWidget(self.status_label_project)
        self.status_bar.addPermanentWidget(self.status_label_task)
        self.status_bar.addPermanentWidget(self.status_label_version)
        bus.project_focus_changed.connect(self._on_project_focus_changed)
        bus.workspace_focus_changed.connect(self._on_workspace_focus_changed)

    def _build_background_services(self):
        """启动任务状态轮询和存储卡监控。"""
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._update_task_status)
        self._status_timer.start()
        self._update_task_status()

        self.volume_monitor = VolumeMonitor(self)
        self.volume_monitor.volume_mounted.connect(self._on_volume_mounted)
        self.volume_monitor.start()
        self.card_automation_service = CardAutomationService(self.backup_view.db_service)
        self.card_automation_worker = None

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

        log_viewer_action = settings_menu.addAction("日志查看器…")
        log_viewer_action.setToolTip("在应用内查看日志文件内容")
        log_viewer_action.triggered.connect(self._show_log_viewer)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")

        restart_action = help_menu.addAction("重新启动新手向导...")
        restart_action.setShortcut("Ctrl+Shift+H")
        restart_action.setToolTip("重新打开首次启动向导，回顾工作区/项目创建流程与 SOP 说明")
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
                self.nav_list.setCurrentRow(import_idx)

    def _show_sop_guide(self):
        """弹出 SOP 操作链说明对话框"""
        QMessageBox.information(self, "SOP 操作链说明", _SOP_GUIDE_TEXT)

    def _show_shortcuts(self):
        """弹出快捷键大全对话框（内容随功能模式裁剪）"""
        nav_count = len(self.active_nav_items)
        lines = [
            "全局快捷键：",
            f"• Ctrl+1~{nav_count}：切换到对应导航页",
            "• Ctrl+F：跳转到素材检索",
            "• Ctrl+I：跳转到媒体导入",
            "• Ctrl+B：跳转到数据备份",
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
            self, "关于 DIT 工作站",
            "<h3>DIT 工作站</h3>"
            f"<p>版本：{APP_VERSION}</p>"
            "<p>专业摄影数据管理应用</p>"
            "<p>支持安全备份、校验和验证、JPG 筛选 RAW 提取、批量重命名、"
            "拍摄日志管理、素材检索与报告生成。</p>"
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

    def _on_nav_changed(self, index: int):
        """导航切换"""
        self.stack.setCurrentIndex(index)

    def _focus_search(self):
        """Ctrl+F: 跳转到素材检索并聚焦搜索框"""
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
            self.nav_list.setCurrentRow(idx)

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
            w = getattr(view, attr, None)
            if w is not None and w.isRunning():
                candidates.append((view, attr, w))
        return candidates

    def _on_project_focus_changed(self, project_id):
        """当前项目切换时更新状态栏"""
        if not project_id:
            self.status_label_project.setText("项目: 未选择")
            return
        try:
            db = get_db_service()
            project = db.get_project(project_id)
            name = project.name if project else "未知"
            self.status_label_project.setText(f"项目: {name}")
        except Exception:
            self.status_label_project.setText("项目: ?")

    def _on_workspace_focus_changed(self, workspace_id):
        """当前工作区切换时更新状态栏"""
        if not workspace_id:
            self.status_label_workspace.setText("工作区: 未选择")
            return
        try:
            db = get_db_service()
            ws = db.get_workspace(workspace_id)
            name = ws.name if ws else "未知"
            self.status_label_workspace.setText(f"工作区: {name}")
        except Exception:
            self.status_label_workspace.setText("工作区: ?")

    def _update_task_status(self):
        """定时更新后台任务数指示（由 _status_timer 每 2 秒触发）"""
        running = self._running_workers()
        count = len(running)
        if count > 0:
            self.status_label_task.setText(f"⚙ 后台任务: {count} 个")
            self.status_label_task.setStyleSheet(f"color: {COLOR.WARNING}; font-weight: 600;")
        else:
            self.status_label_task.setText("就绪")
            self.status_label_task.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")

    def closeEvent(self, event):
        """关闭窗口前检查后台 worker，避免强杀线程导致数据写半截。

        - 无运行中的 worker：直接关闭
        - 有运行中的 worker：弹确认框，用户确认后等待各 worker 最多 5 秒再关闭；
          用户取消则忽略关闭事件
        """
        running = self._running_workers()
        card_running = self.card_automation_worker and self.card_automation_worker.isRunning()
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
        view_name_map = {
            id(self.backup_view): "数据备份",
            id(self.import_view): "媒体导入",
            id(self.raw_view): "RAW提取",
            id(self.rename_view): "文件重命名",
            id(self.report_view): "报告生成",
            id(self.asset_info_view): "批量EXIF读取",
        }
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

    def _on_volume_mounted(self, path: str):
        """检测到存储卡：预填导入视图，并按配置启动自动化流程。"""
        name = Path(path).name or path
        self.status_label_task.setText(f"💾 检测到存储卡: {name}")
        if getattr(config, "auto_detect_volume", True):
            self._navigate_to("import")
            self.import_view.set_source_folder(path, auto_scan=True)
        # 自动化启动条件必须同时满足配置开关与功能模式开关：
        # 个人模式下即使用户曾在团队模式开启过自动化配置，也不得启动。
        if (getattr(config, "auto_card_automation_enabled", False)
                and is_enabled("card_automation")):
            self._start_card_automation(path)

    def _start_card_automation(self, source_path: str):
        """按设置启动一次相机卡自动任务；同一时间只允许一个自动任务。"""
        if self.card_automation_worker and self.card_automation_worker.isRunning():
            self.status_label_task.setText("⚠ 已有相机卡自动任务正在执行")
            return
        project_id = getattr(config, "auto_card_project_id", "")
        template_id = getattr(config, "auto_card_template_id", "")
        do_import = getattr(config, "auto_card_import", True)
        do_backup = getattr(config, "auto_card_backup", False)
        steps = getattr(config, "auto_card_steps", None) or None
        template = self.backup_view.db_service.get_backup_template(template_id) if template_id else None
        if not project_id or ((do_backup or (steps and "backup" in steps)) and template is None):
            self.status_label_task.setText("⚠ 相机卡自动化配置不完整，请检查设置")
            logger.warning(f"相机卡自动化配置不完整: project={project_id} template={template_id}")
            return
        project = self.backup_view.db_service.get_project(project_id)
        if project is None:
            self.status_label_task.setText("⚠ 自动化项目不存在，请检查设置")
            return
        set_current_project(project_id)
        self.card_automation_worker = WorkerThread(
            self.card_automation_service.execute,
            source_path,
            project_id,
            template=template,
            do_import=do_import,
            do_backup=do_backup,
            steps=steps,
            raw_config={"output_folder": getattr(config, "auto_card_raw_output_dir", "")}
            if steps and "raw_extract" in steps else None,
            rename_config={"rule": {"pattern": config.auto_card_rename_pattern}}
            if steps and "rename" in steps and getattr(config, "auto_card_rename_pattern", "") else None,
            report_path=getattr(config, "auto_card_report_path", "") or None,
            inject_progress=True,
            inject_cancel_check=True,
        )
        self.card_automation_worker.progress.connect(self._on_card_automation_progress)
        self.card_automation_worker.finished.connect(self._on_card_automation_finished)
        self.card_automation_worker.error.connect(self._on_card_automation_error)
        self.card_automation_worker.thread_finished.connect(self.card_automation_worker.deleteLater)
        self.card_automation_worker.start()
        self.status_label_task.setText(f"⚙ 自动处理相机卡: {Path(source_path).name}")

    @Slot(str, float, str)
    def _on_card_automation_progress(self, target: str, progress: float, message: str):
        self.status_label_task.setText(f"⚙ {message} ({int(progress * 100)}%)")

    @Slot(object)
    def _on_card_automation_finished(self, result):
        self.card_automation_worker = None
        backup = result.get("backup") if isinstance(result, dict) else None
        imported = (result.get("import") or {}).get("imported", 0) if isinstance(result, dict) else 0
        backup_text = ""
        if backup is not None:
            backup_text = f"，备份状态 {backup.status.value}"
        self.status_label_task.setText(f"✅ 相机卡自动处理完成：导入 {imported} 个{backup_text}")
        if imported:
            get_data_bus().emit_data_changed("assets_changed")

    @Slot(str)
    def _on_card_automation_error(self, error: str):
        self.card_automation_worker = None
        self.status_label_task.setText(f"❌ 相机卡自动处理失败: {error}")
        logger.error(f"相机卡自动处理失败: {error}")

    def _on_data_changed(self, event: str):
        """
        跨视图数据变更广播处理。

        各视图的 showEvent 已会刷新项目列表，这里针对联动断链场景：
        - assets_changed: 切到 log_view/search_view/asset_info_view 时刷新明细
        - logs_changed: 刷新 import_view 的日志下拉、log_view 的日志列表
        - projects_changed: 各视图 showEvent 已处理

        本方法由 data_bus 信号触发（非用户主动操作），异常仅记日志不弹框，
        避免单个子视图的刷新异常中断整个广播链路。

        注意：视图已被 QScrollArea 包裹，stack.currentWidget() 返回的是
        QScrollArea 而非视图本身，故用 currentIndex + 激活导航列表 key 判断。
        隐藏页面不在视图栈中，其视图属性仍存在（MVP 全量实例化）但不会被访问。
        """
        idx = self.stack.currentIndex()
        if idx < 0 or idx >= len(self.active_nav_items):
            return
        key = self.active_nav_items[idx][0]
        try:
            if event in ("assets_changed", "all"):
                # 素材变更：刷新日志关联面板、检索结果、素材信息
                if key == "log":
                    self.log_view._load_project_assets()
                    if self.log_view.current_log_id:
                        self.log_view._load_assets_for_log(self.log_view.current_log_id)
                elif key == "search":
                    # 不自动重搜（会丢失用户筛选条件），仅提示结果可能过期
                    pass
                elif key == "asset_info":
                    self.asset_info_view._load_assets()
            if event in ("logs_changed", "all"):
                # 日志变更：刷新导入视图的日志下拉
                if key == "import" and self.import_view.current_project:
                    self.import_view._load_logs(self.import_view.current_project.project_id)
                elif key == "log":
                    self.log_view._load_logs()
        except Exception as e:
            logger.error(f"_on_data_changed 处理 event={event} 失败: {e}", exc_info=True)

    def _apply_style(self):
        """应用样式（主窗口 + 侧栏）。全局 QSS 由 main.py 通过 theme.apply_global_style 注入。"""
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
            QListWidget::item {{
                color: {COLOR.SIDEBAR_TEXT};
                padding: 10px 16px;
                border-radius: {RADIUS.BUTTON}px;
                margin: 2px 8px;
            }}
            QListWidget::item:selected {{
                background-color: {COLOR.PRIMARY};
                color: {COLOR.SIDEBAR_TEXT};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {COLOR.SIDEBAR_HOVER};
            }}
            QStackedWidget {{
                background-color: {COLOR.BG_CARD};
            }}
        """)
