"""主窗口与导航框架"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QScrollArea,
    QMessageBox
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QShortcut, QKeySequence

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
from DITWorkstation.Utils import logger


# ===== 导航栏单一事实源 =====
# 修改导航栏顺序/项目时，只需调整此列表，所有依赖索引的视图（如 dashboard 跳转按钮）
# 通过 get_nav_index(key) 自动获取最新索引，避免索引错位 bug。
# (key, label, tooltip)
NAV_ITEMS = [
    ("dashboard",  "🏠 项目概览",   "当前项目进度看板与 SOP 引导"),
    ("import",     "📁 媒体导入",   "导入图片视频到项目"),
    ("backup",     "📦 数据备份",   "安全拷贝与多重备份"),
    ("raw",        "🎞 RAW提取",    "JPG筛选后提取RAW"),
    ("rename",     "✏️ 文件重命名", "批量重命名与元数据"),
    ("log",        "📋 拍摄日志",   "场景/镜头/镜次管理"),
    ("search",     "🔍 素材检索",   "按条件快速检索"),
    ("asset_info", "ℹ️ 素材信息",   "查看素材EXIF与元数据详情"),
    ("report",     "📊 报告生成",   "数据管理与QC报告"),
]


def get_nav_index(key: str) -> int:
    """按 key 查询导航栏索引。key 不存在时抛 KeyError，便于尽早发现配置错误。"""
    for i, (k, _label, _tooltip) in enumerate(NAV_ITEMS):
        if k == key:
            return i
    raise KeyError(f"未知导航 key: {key}")


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
        self.stack.addWidget(self._wrap_scrollable(self.dashboard_view))
        self.stack.addWidget(self._wrap_scrollable(self.import_view))
        self.stack.addWidget(self._wrap_scrollable(self.backup_view))
        self.stack.addWidget(self._wrap_scrollable(self.raw_view))
        self.stack.addWidget(self._wrap_scrollable(self.rename_view))
        self.stack.addWidget(self._wrap_scrollable(self.log_view))
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
        try:
            self.nav_list.setCurrentRow(get_nav_index(key))
        except KeyError:
            pass

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

    def closeEvent(self, event):
        """关闭窗口前检查后台 worker，避免强杀线程导致数据写半截。

        - 无运行中的 worker：直接关闭
        - 有运行中的 worker：弹确认框，用户确认后等待各 worker 最多 5 秒再关闭；
          用户取消则忽略关闭事件
        """
        running = self._running_workers()
        if not running:
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

        reply = QMessageBox.question(
            self, "确认退出",
            f"以下任务正在后台执行：{task_text}\n\n"
            "强制退出可能导致数据写不完整或文件拷贝残留。\n"
            "是否等待任务完成？点击「Yes」将等待最多 5 秒后退出；点击「No」立即强制退出。",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )

        if reply == QMessageBox.Cancel:
            event.ignore()
            return

        if reply == QMessageBox.Yes:
            # 尝试等待各 worker 结束（最多 5 秒），避免半截写入
            for _view, _attr, w in running:
                try:
                    w.wait(5000)
                except Exception:
                    pass
            logger.info(f"主窗口关闭：已等待 {len(running)} 个后台 worker 结束")

        super().closeEvent(event)

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
        """应用样式"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f7;
            }
            QListWidget {
                background-color: #2c2c2e;
                border: none;
                padding-top: 20px;
                font-size: 14px;
            }
            QListWidget::item {
                color: #ffffff;
                padding: 10px 16px;
                border-radius: 8px;
                margin: 2px 8px;
            }
            QListWidget::item:selected {
                background-color: #0a84ff;
                color: #ffffff;
            }
            QListWidget::item:hover:!selected {
                background-color: #3a3a3c;
            }
            QStackedWidget {
                background-color: #ffffff;
            }
        """)
