"""主窗口与导航框架"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QScrollArea,
    QMessageBox, QLabel
)
from PySide6.QtCore import Qt, QSize, QTimer, Slot
from PySide6.QtGui import QShortcut, QKeySequence
from pathlib import Path

# 会话上下文（EventBus + 全局项目/工作区状态）已抽离到 App/session_context.py
# [DEPRECATED] 此处 re-export 仅为向后兼容；新代码应直接从 App.session_context 导入，
# 避免形成 main_window ↔ Views 的逻辑循环依赖。
from DITWorkstation.App.session_context import (
    get_data_bus,
    get_current_project_id,
    set_current_project,
    get_current_workspace_id,
    set_current_workspace,
)

from DITWorkstation.Views.backup_view import BackupView
from DITWorkstation.Views.raw_extraction_view import RawExtractionView
from DITWorkstation.Views.rename_view import RenameView
from DITWorkstation.Views.shooting_log_view import ShootingLogView
from DITWorkstation.Views.search_view import SearchView
from DITWorkstation.Views.report_view import ReportView
from DITWorkstation.Views.media_import_view import MediaImportView
from DITWorkstation.Views.asset_info_view import AssetInfoView
from DITWorkstation.Views.project_dashboard_view import ProjectDashboardView
from DITWorkstation.Views.first_run_wizard import FirstRunWizard, _SOP_GUIDE_TEXT
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS
from DITWorkstation.App import config
from DITWorkstation.Utils import logger, get_db_service
from DITWorkstation.Services.volume_monitor import VolumeMonitor
from DITWorkstation.Services.card_automation_service import CardAutomationService
from DITWorkstation.Utils.workers import WorkerThread


# 导航配置（NAV_ITEMS / get_nav_index）已抽离到 App/navigation.py 作为单一事实源，
# 消除 Views ↔ main_window 的循环依赖。视图跳转请直接从 App.navigation 导入。
from DITWorkstation.App.navigation import NAV_ITEMS, get_nav_index


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DIT工作站 - 专业摄影数据管理")
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

        # 左侧导航栏（顺序由 NAV_ITEMS 单一事实源决定）
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(200)
        self.nav_list.setIconSize(QSize(24, 24))
        self.nav_list.setSpacing(4)

        for _key, text, tooltip in NAV_ITEMS:
            item = QListWidgetItem(text)
            item.setToolTip(tooltip)
            item.setSizeHint(QSize(180, 44))
            self.nav_list.addItem(item)

        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

        # 右侧内容区（addWidget 顺序必须与 NAV_ITEMS 顺序保持一致）
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

        # 统一用 QScrollArea 包裹视图，保证内容超出窗口时出现滚动条。
        # addWidget 顺序必须与 NAV_ITEMS 顺序保持一致。
        self.stack.addWidget(self._wrap_scrollable(self.dashboard_view))
        self.stack.addWidget(self._wrap_scrollable(self.import_view))
        self.stack.addWidget(self._wrap_scrollable(self.backup_view))
        self.stack.addWidget(self._wrap_scrollable(self.log_view))
        self.stack.addWidget(self._wrap_scrollable(self.raw_view))
        self.stack.addWidget(self._wrap_scrollable(self.rename_view))
        self.stack.addWidget(self._wrap_scrollable(self.search_view))
        self.stack.addWidget(self._wrap_scrollable(self.asset_info_view))
        self.stack.addWidget(self._wrap_scrollable(self.report_view))

        # 连接跨视图刷新信号总线
        bus = get_data_bus()
        bus.data_changed.connect(self._on_data_changed)

        layout.addWidget(self.nav_list)
        layout.addWidget(self.stack, 1)

        # 全局快捷键
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+I"), self, activated=self._focus_import)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self._focus_backup)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_log)
        # Ctrl+1~9 切换到对应导航页
        for i in range(1, 10):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self,
                      activated=lambda idx=i - 1: self.nav_list.setCurrentRow(idx))
        # F5 刷新当前视图
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_current_view)
        # Esc 取消当前后台任务
        QShortcut(QKeySequence("Esc"), self, activated=self._cancel_running_workers)

        # 全局状态栏
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
        self.status_bar.addWidget(self.status_label_workspace)
        self.status_bar.addWidget(self.status_label_project)
        self.status_bar.addPermanentWidget(self.status_label_task)

        # 监听会话上下文：当前项目/工作区切换时更新状态栏
        bus.project_focus_changed.connect(self._on_project_focus_changed)
        bus.workspace_focus_changed.connect(self._on_workspace_focus_changed)

        # 后台任务状态轮询（每 2 秒检查一次）
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._update_task_status)
        self._status_timer.start()
        self._update_task_status()

        # 存储卡自动识别：插入媒体卡时自动跳转到导入视图并预填源目录
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

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")

        restart_action = help_menu.addAction("重新启动新手向导...")
        restart_action.setShortcut("Ctrl+Shift+H")
        restart_action.setToolTip("重新打开首次启动向导，回顾工作区/项目创建流程与 SOP 说明")
        restart_action.triggered.connect(self._restart_wizard)

        sop_action = help_menu.addAction("SOP 操作链说明...")
        sop_action.setToolTip("查看完整的标准操作流程说明")
        sop_action.triggered.connect(self._show_sop_guide)

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
            try:
                self.nav_list.setCurrentRow(get_nav_index("import"))
            except KeyError as e:
                logger.debug(f"向导后跳转媒体导入失败: {e}")

    def _show_sop_guide(self):
        """弹出 SOP 操作链说明对话框"""
        QMessageBox.information(self, "SOP 操作链说明", _SOP_GUIDE_TEXT)

    def _show_shortcuts(self):
        """弹出快捷键大全对话框"""
        QMessageBox.information(
            self, "快捷键大全",
            "全局快捷键：\n"
            "• Ctrl+1~9：切换到对应导航页\n"
            "• Ctrl+F：跳转到素材检索\n"
            "• Ctrl+I：跳转到媒体导入\n"
            "• Ctrl+B：跳转到数据备份\n"
            "• Ctrl+L：跳转到拍摄日志\n"
            "• Ctrl+Shift+H：重新启动新手向导\n"
            "• F5：刷新当前视图\n"
            "• Esc：取消当前后台任务"
        )

    def _show_about(self):
        """关于对话框"""
        QMessageBox.about(
            self, "关于 DIT 工作站",
            "<h3>DIT 工作站</h3>"
            "<p>专业摄影数据管理应用</p>"
            "<p>支持安全备份、校验和验证、JPG 筛选 RAW 提取、批量重命名、"
            "拍摄日志管理、素材检索与报告生成。</p>"
        )

    def _show_settings(self):
        """打开设置对话框"""
        from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        dialog.exec()

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
        """F5: 刷新当前视图"""
        idx = self.stack.currentIndex()
        if 0 <= idx < len(NAV_ITEMS):
            key = NAV_ITEMS[idx][0]
            view_map = {
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
            view = view_map.get(key)
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
        force_btn = box.addButton("立即退出", QMessageBox.DestructiveRole)
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
        if getattr(config, "auto_card_automation_enabled", False):
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
        template = self.backup_view.db_service.get_backup_template(template_id) if template_id else None
        if not project_id or (do_backup and template is None):
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
            inject_progress=True,
            inject_cancel_check=True,
        )
        self.card_automation_worker.progress.connect(self._on_card_automation_progress)
        self.card_automation_worker.finished.connect(self._on_card_automation_finished)
        self.card_automation_worker.error.connect(self._on_card_automation_error)
        self.card_automation_worker.finished.connect(self.card_automation_worker.deleteLater)
        self.card_automation_worker.error.connect(self.card_automation_worker.deleteLater)
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
        QScrollArea 而非视图本身，故用 currentIndex + NAV_ITEMS key 判断。
        """
        idx = self.stack.currentIndex()
        if idx < 0 or idx >= len(NAV_ITEMS):
            return
        key = NAV_ITEMS[idx][0]
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
