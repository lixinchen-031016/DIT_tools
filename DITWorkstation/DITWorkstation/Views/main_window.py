"""主窗口与导航框架"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget
)
from PySide6.QtCore import Qt, QSize

# 会话上下文（EventBus + 全局项目/工作区状态）已抽离到 App/session_context.py
# 此处 re-export，保持现有视图的 `from ...main_window import get_data_bus` 等导入不变
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

        本方法由 data_bus 信号触发（非用户主动操作），异常仅记日志不弹框，
        避免单个子视图的刷新异常中断整个广播链路。
        """
        current = self.stack.currentWidget()
        try:
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
