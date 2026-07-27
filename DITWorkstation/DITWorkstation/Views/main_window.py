"""主窗口与导航框架"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget,
    QLabel, QFrame, QApplication
)
from PySide6.QtCore import Qt, QSize, Signal, QObject
from PySide6.QtGui import QFont, QIcon

from DITWorkstation.Views.backup_view import BackupView
from DITWorkstation.Views.raw_extraction_view import RawExtractionView
from DITWorkstation.Views.rename_view import RenameView
from DITWorkstation.Views.shooting_log_view import ShootingLogView
from DITWorkstation.Views.search_view import SearchView
from DITWorkstation.Views.report_view import ReportView
from DITWorkstation.Views.media_import_view import MediaImportView
from DITWorkstation.Views.asset_info_view import AssetInfoView
from DITWorkstation.Views.project_dashboard_view import ProjectDashboardView


class _DataBus(QObject):
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
_data_bus = _DataBus()


def get_data_bus() -> _DataBus:
    """返回全局数据总线单例"""
    return _data_bus


# ===== 全局当前工作区 =====
# 工作区是项目的父级容器；切换工作区会过滤项目下拉，并联动清除/重设当前项目。
_current_workspace_id = None


def get_current_workspace_id():
    """返回全局当前工作区 ID（可能为 None）"""
    return _current_workspace_id


def set_current_workspace(workspace_id):
    """设置全局当前工作区并广播 workspace_focus_changed。

    切换工作区时：
      - 广播 workspace_focus_changed，各视图的项目下拉监听后过滤为本工作区项目
      - 当前项目若不属于新工作区，则清除（避免跨工作区误操作）
      - 广播 data_changed("all") 触发各视图刷新
    """
    global _current_workspace_id, _current_project_id
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

    get_data_bus().emit_workspace_focus_changed(workspace_id)
    get_data_bus().emit_project_focus_changed(_current_project_id)
    get_data_bus().emit_data_changed("all")


# ===== 全局当前项目 =====
# MainWindow 持有全局 current_project_id；各视图通过 set_current_project 切换，
# 通过 get_current_project_id 读取，避免每个视图各自维护 current_project 导致切换视图后要重选。
_current_project_id = None


def get_current_project_id():
    """返回全局当前项目 ID（可能为 None）"""
    return _current_project_id


def set_current_project(project_id):
    """设置全局当前项目并广播 project_focus_changed 事件。

    各视图可监听该信号自动同步项目下拉，无需各自维护 current_project。

    若新项目属于某个工作区，会顺带把当前工作区切到该项目所属工作区，
    保证工作区下拉与项目下拉始终一致。
    """
    global _current_project_id, _current_workspace_id
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
                get_data_bus().emit_workspace_focus_changed(_current_workspace_id)
        except Exception:
            pass

    get_data_bus().emit_project_focus_changed(project_id)
    # 项目切换也视为数据变更，触发各视图刷新
    get_data_bus().emit_data_changed("all")


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

        # 左侧导航栏
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(200)
        self.nav_list.setIconSize(QSize(24, 24))
        self.nav_list.setSpacing(4)

        nav_items = [
            ("🏠 项目概览", "当前项目进度看板与 SOP 引导"),
            ("📁 媒体导入", "导入图片视频到项目"),
            ("📦 数据备份", "安全拷贝与多重备份"),
            ("🎞 RAW提取", "JPG筛选后提取RAW"),
            ("✏️ 文件重命名", "批量重命名与元数据"),
            ("📋 拍摄日志", "场景/镜头/镜次管理"),
            ("🔍 素材检索", "按条件快速检索"),
            ("ℹ️ 素材信息", "查看素材EXIF与元数据详情"),
            ("📊 报告生成", "数据管理与QC报告"),
        ]

        for text, tooltip in nav_items:
            item = QListWidgetItem(text)
            item.setToolTip(tooltip)
            item.setSizeHint(QSize(180, 44))
            self.nav_list.addItem(item)

        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

        # 右侧内容区
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

        self.stack.addWidget(self.dashboard_view)
        self.stack.addWidget(self.import_view)
        self.stack.addWidget(self.backup_view)
        self.stack.addWidget(self.raw_view)
        self.stack.addWidget(self.rename_view)
        self.stack.addWidget(self.log_view)
        self.stack.addWidget(self.search_view)
        self.stack.addWidget(self.asset_info_view)
        self.stack.addWidget(self.report_view)

        # 连接跨视图刷新信号总线
        bus = get_data_bus()
        bus.data_changed.connect(self._on_data_changed)

        layout.addWidget(self.nav_list)
        layout.addWidget(self.stack, 1)

    def _on_nav_changed(self, index: int):
        """导航切换"""
        self.stack.setCurrentIndex(index)

    def _on_data_changed(self, event: str):
        """
        跨视图数据变更广播处理。

        各视图的 showEvent 已会刷新项目列表，这里针对联动断链场景：
        - assets_changed: 切到 log_view/search_view/asset_info_view 时刷新明细
        - logs_changed: 刷新 import_view 的日志下拉、log_view 的日志列表
        - projects_changed: 各视图 showEvent 已处理
        """
        current = self.stack.currentWidget()
        if event in ("assets_changed", "all"):
            # 素材变更：刷新日志关联面板、检索结果、素材信息
            if current is self.log_view:
                self.log_view._load_project_assets()
                if self.log_view.current_log_id:
                    self.log_view._load_assets_for_log(self.log_view.current_log_id)
            elif current is self.search_view:
                # 不自动重搜（会丢失用户筛选条件），仅提示结果可能过期
                pass
            elif current is self.asset_info_view:
                self.asset_info_view._load_assets()
        if event in ("logs_changed", "all"):
            # 日志变更：刷新导入视图的日志下拉
            if current is self.import_view and self.import_view.current_project:
                self.import_view._load_logs(self.import_view.current_project.project_id)
            elif current is self.log_view:
                self.log_view._load_logs()

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
