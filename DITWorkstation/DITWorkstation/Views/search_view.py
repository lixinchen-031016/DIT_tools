"""素材检索页面"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QDateEdit,
    QCheckBox
)
from PySide6.QtCore import Qt, QDate

from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Utils import format_size


class SearchView(QWidget):
    """素材检索视图"""

    def __init__(self):
        super().__init__()
        self.db_service = DatabaseService()
        self._log_cache = {}
        self._setup_ui()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self._load_projects()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("素材检索")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        layout.addWidget(title)

        subtitle = QLabel("按场景、镜头、日期、文件类型等维度快速检索素材")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
        layout.addWidget(subtitle)

        # 搜索条件
        search_group = QGroupBox("搜索条件")
        search_layout = QFormLayout(search_group)

        # 第一行：项目和关键词
        row1 = QHBoxLayout()
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(200)
        row1.addWidget(QLabel("项目:"))
        row1.addWidget(self.project_combo)
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("输入关键词搜索文件名...")
        self.keyword_edit.returnPressed.connect(self._search)
        row1.addWidget(QLabel("关键词:"))
        row1.addWidget(self.keyword_edit, 1)
        search_layout.addRow("", row1)

        # 第二行：场景和镜头和类型
        row2 = QHBoxLayout()
        self.scene_edit = QLineEdit()
        self.scene_edit.setPlaceholderText("场景号")
        self.shot_edit = QLineEdit()
        self.shot_edit.setPlaceholderText("镜头号")
        self.type_combo = QComboBox()
        self.type_combo.addItems(["全部", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".jpg", ".jpeg"])
        row2.addWidget(QLabel("场景:"))
        row2.addWidget(self.scene_edit)
        row2.addWidget(QLabel("镜头:"))
        row2.addWidget(self.shot_edit)
        row2.addWidget(QLabel("类型:"))
        row2.addWidget(self.type_combo)
        search_layout.addRow("", row2)

        # 第三行：拍摄日志筛选
        row3 = QHBoxLayout()
        self.log_combo = QComboBox()
        self.log_combo.addItem("全部日志", None)
        self.log_combo.setEnabled(False)
        self.log_combo.setMinimumWidth(250)
        row3.addWidget(QLabel("拍摄日志:"))
        row3.addWidget(self.log_combo)
        row3.addStretch()
        search_layout.addRow("", row3)

        # 第四行：日期范围
        row4 = QHBoxLayout()
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_check = QCheckBox("限定日期范围")
        self.date_check.setChecked(False)
        row4.addWidget(self.date_check)
        row4.addWidget(QLabel("从:"))
        row4.addWidget(self.date_from)
        row4.addWidget(QLabel("到:"))
        row4.addWidget(self.date_to)
        row4.addStretch()
        search_layout.addRow("", row4)

        layout.addWidget(search_group)

        # 搜索按钮
        btn_layout = QHBoxLayout()
        search_btn = QPushButton("🔍 搜索")
        search_btn.setStyleSheet("""
            QPushButton {
                background-color: #0a84ff;
                color: white;
                padding: 10px 24px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0070e0; }
        """)
        search_btn.clicked.connect(self._search)
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self._reset)
        btn_layout.addWidget(search_btn)
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch()
        self.result_label = QLabel("")
        self.result_label.setStyleSheet("color: #86868b;")
        btn_layout.addWidget(self.result_label)
        layout.addLayout(btn_layout)

        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(8)
        self.result_table.setHorizontalHeaderLabels(
            ["文件名", "类型", "大小", "场景", "镜头", "关联日志", "校验和", "导入时间"]
        )
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_table.setAlternatingRowColors(True)
        layout.addWidget(self.result_table, 1)

    def _load_projects(self):
        self.project_combo.clear()
        self.project_combo.addItem("全部项目", None)
        projects = self.db_service.get_projects()
        for p in projects:
            self.project_combo.addItem(p.name, p.project_id)
        self._refresh_log_combo()

    def showEvent(self, event):
        self._load_projects()
        super().showEvent(event)

    def _on_project_changed(self, index: int):
        self._refresh_log_combo()

    def _refresh_log_combo(self):
        project_id = self.project_combo.currentData()
        self.log_combo.clear()
        self.log_combo.addItem("全部日志", None)
        self._log_cache.clear()
        if project_id:
            logs = self.db_service.get_shooting_logs(project_id)
            if logs:
                for log in logs:
                    label = f"{log.scene} / {log.shot} / {log.take}"
                    if log.description:
                        label += f" - {log.description}"
                    self.log_combo.addItem(label, log.log_id)
                    self._log_cache[log.log_id] = log
                self.log_combo.setEnabled(True)
            else:
                self.log_combo.setEnabled(False)
        else:
            self.log_combo.setEnabled(False)

    def _search(self):
        project_id = self.project_combo.currentData()
        scene = self.scene_edit.text() or None
        shot = self.shot_edit.text() or None
        keyword = self.keyword_edit.text() or None
        log_id = self.log_combo.currentData() or None

        file_type = None
        if self.type_combo.currentIndex() > 0:
            file_type = self.type_combo.currentText()

        date_from = None
        date_to = None
        if self.date_check.isChecked():
            date_from = self.date_from.date().toString("yyyy-MM-dd")
            date_to = self.date_to.date().toString("yyyy-MM-dd") + " 23:59:59"

        results = self.db_service.search_assets(
            project_id=project_id,
            scene=scene,
            shot=shot,
            file_type=file_type,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
            log_id=log_id
        )

        self._display_results(results)

    def _display_results(self, results):
        self.result_table.setRowCount(len(results))
        self.result_label.setText(f"找到 {len(results)} 条结果")

        for i, asset in enumerate(results):
            self.result_table.setItem(i, 0, QTableWidgetItem(asset.file_name))
            self.result_table.setItem(i, 1, QTableWidgetItem(asset.file_type))
            self.result_table.setItem(i, 2, QTableWidgetItem(
                format_size(asset.file_size)
            ))
            self.result_table.setItem(i, 3, QTableWidgetItem(asset.scene))
            self.result_table.setItem(i, 4, QTableWidgetItem(asset.shot))

            log_label = ""
            if asset.log_id:
                log = self._log_cache.get(asset.log_id)
                if log:
                    log_label = f"{log.scene}/{log.shot}/{log.take}"
                else:
                    log_label = asset.log_id
            self.result_table.setItem(i, 5, QTableWidgetItem(log_label))

            checksum_short = asset.checksum_value[:12] + "..." if asset.checksum_value else ""
            self.result_table.setItem(i, 6, QTableWidgetItem(checksum_short))
            self.result_table.setItem(i, 7, QTableWidgetItem(
                asset.date_imported.strftime("%Y-%m-%d %H:%M")
            ))

    def _reset(self):
        self.keyword_edit.clear()
        self.scene_edit.clear()
        self.shot_edit.clear()
        self.type_combo.setCurrentIndex(0)
        self.log_combo.setCurrentIndex(0)
        self.date_check.setChecked(False)
        self.project_combo.setCurrentIndex(0)
        self.result_table.setRowCount(0)
        self.result_label.setText("")
