"""主窗口与导航框架"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget,
    QLabel, QFrame, QApplication
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QIcon

from DITWorkstation.Views.backup_view import BackupView
from DITWorkstation.Views.raw_extraction_view import RawExtractionView
from DITWorkstation.Views.rename_view import RenameView
from DITWorkstation.Views.shooting_log_view import ShootingLogView
from DITWorkstation.Views.search_view import SearchView
from DITWorkstation.Views.report_view import ReportView
from DITWorkstation.Views.media_import_view import MediaImportView
from DITWorkstation.Views.asset_info_view import AssetInfoView


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
        self.import_view = MediaImportView()
        self.backup_view = BackupView()
        self.raw_view = RawExtractionView()
        self.rename_view = RenameView()
        self.log_view = ShootingLogView()
        self.search_view = SearchView()
        self.asset_info_view = AssetInfoView()
        self.report_view = ReportView()

        self.stack.addWidget(self.import_view)
        self.stack.addWidget(self.backup_view)
        self.stack.addWidget(self.raw_view)
        self.stack.addWidget(self.rename_view)
        self.stack.addWidget(self.log_view)
        self.stack.addWidget(self.search_view)
        self.stack.addWidget(self.asset_info_view)
        self.stack.addWidget(self.report_view)

        layout.addWidget(self.nav_list)
        layout.addWidget(self.stack, 1)

    def _on_nav_changed(self, index: int):
        """导航切换"""
        self.stack.setCurrentIndex(index)

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
