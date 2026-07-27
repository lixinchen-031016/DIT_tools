"""素材检索页面"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QDateEdit,
    QCheckBox
)
from PySide6.QtCore import QDate

from DITWorkstation.Utils import format_size, get_db_service, safe_slot, logger


class SearchView(QWidget):
    """素材检索视图"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        # 全局日志缓存：跨项目搜索时也能显示日志标签而非裸 UUID
        self._log_cache = {}
        self._setup_ui()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self._load_projects()
        # 监听全局工作区切换，重新加载项目列表（按新工作区过滤）
        from DITWorkstation.Views.main_window import get_data_bus
        get_data_bus().workspace_focus_changed.connect(self._on_global_workspace_changed)

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

        # 第三行：拍摄日志 + 评级筛选
        row3 = QHBoxLayout()
        self.log_combo = QComboBox()
        self.log_combo.addItem("全部日志", None)
        self.log_combo.setEnabled(False)
        self.log_combo.setMinimumWidth(250)
        row3.addWidget(QLabel("拍摄日志:"))
        row3.addWidget(self.log_combo)

        self.rating_combo = QComboBox()
        self.rating_combo.addItem("全部评级", None)
        # 评级档位标签来自 Models.RATING_LABELS 单一事实源
        from DITWorkstation.Models import RATING_LABELS, AssetRating
        self.rating_combo.addItem(f"{RATING_LABELS[AssetRating.USABLE.value]} 及以上", AssetRating.USABLE.value)
        self.rating_combo.addItem(f"{RATING_LABELS[AssetRating.BACKUP.value]} 及以上", AssetRating.BACKUP.value)
        self.rating_combo.addItem(RATING_LABELS[AssetRating.PREFERRED.value], AssetRating.PREFERRED.value)
        row3.addWidget(QLabel("评级:"))
        row3.addWidget(self.rating_combo)
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
        self.result_table.setColumnCount(9)
        self.result_table.setHorizontalHeaderLabels(
            ["文件名", "类型", "大小", "场景", "镜头", "关联日志", "评级", "校验和", "导入时间"]
        )
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_table.setAlternatingRowColors(True)
        layout.addWidget(self.result_table, 1)

        # 数据过期提示条（其他视图改了数据后，提示用户当前结果可能过期）
        self.stale_banner = QLabel("")
        self.stale_banner.setVisible(False)
        self.stale_banner.setStyleSheet(
            "background-color: #fff3cd; color: #856404; padding: 8px 12px; "
            "border-radius: 6px; border: 1px solid #ffe08a;"
        )
        # 点击提示条触发重搜
        self.stale_banner.mousePressEvent = lambda _e: self._search()
        layout.addWidget(self.stale_banner)

        # 监听全局数据总线，数据变更时显示过期提示
        try:
            from DITWorkstation.Views.main_window import get_data_bus
            get_data_bus().data_changed.connect(self._on_data_changed)
        except Exception as e:
            logger.warning(f"素材检索连接 data_bus 失败: {e}")

    def _on_data_changed(self, event: str):
        """收到数据变更广播时，若已有结果则显示过期提示条"""
        if event in ("assets_changed", "logs_changed", "all"):
            if self.result_table.rowCount() > 0:
                self.stale_banner.setText(
                    "⚠ 数据已变更，当前结果可能过期。点击此处或「🔍 搜索」按钮重新搜索。"
                )
                self.stale_banner.setVisible(True)

    def _load_projects(self):
        # 保留当前选中的项目
        prev_id = self.project_combo.currentData()

        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("全部项目", None)
        from DITWorkstation.Views.main_window import get_current_workspace_id
        ws_id = get_current_workspace_id()
        projects = self.db_service.get_projects(workspace_id=ws_id)
        restore_index = 0
        for i, p in enumerate(projects, start=1):
            self.project_combo.addItem(p.name, p.project_id)
            if prev_id and p.project_id == prev_id:
                restore_index = i
        self.project_combo.setCurrentIndex(restore_index)
        self.project_combo.blockSignals(False)

        self._refresh_log_combo()

    def showEvent(self, event):
        self._load_projects()
        super().showEvent(event)

    def _on_project_changed(self, index: int):
        self._refresh_log_combo()

    def _on_global_workspace_changed(self, _workspace_id):
        """全局工作区切换 -> 重新加载项目列表（按新工作区过滤），并清空旧结果"""
        self._load_projects()
        self.result_table.setRowCount(0)
        self.result_label.setText("")

    def _refresh_log_combo(self):
        project_id = self.project_combo.currentData()
        self.log_combo.clear()
        self.log_combo.addItem("全部日志", None)
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

    def _ensure_log_cached(self, log_id: str):
        """跨项目时按需补齐日志缓存，避免显示裸 UUID"""
        if not log_id or log_id in self._log_cache:
            return
        log = self.db_service.get_shooting_log(log_id)
        if log:
            self._log_cache[log_id] = log

    # 搜索结果上限：避免一次性加载过多导致 UI 卡顿
    _SEARCH_LIMIT = 2000

    @safe_slot("搜索失败")
    def _search(self):
        project_id = self.project_combo.currentData()
        scene = self.scene_edit.text() or None
        shot = self.shot_edit.text() or None
        keyword = self.keyword_edit.text() or None
        log_id = self.log_combo.currentData() or None
        rating = self.rating_combo.currentData()

        file_type = None
        if self.type_combo.currentIndex() > 0:
            file_type = self.type_combo.currentText()

        date_from = None
        date_to = None
        if self.date_check.isChecked():
            date_from = self.date_from.date().toString("yyyy-MM-dd")
            date_to = self.date_to.date().toString("yyyy-MM-dd") + " 23:59:59"

        # 多取一条用于判断是否截断
        results = self.db_service.search_assets(
            project_id=project_id,
            scene=scene,
            shot=shot,
            file_type=file_type,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
            log_id=log_id,
            rating=rating,
            limit=self._SEARCH_LIMIT + 1
        )

        truncated = len(results) > self._SEARCH_LIMIT
        if truncated:
            results = results[:self._SEARCH_LIMIT]

        self._display_results(results, truncated)

    def _display_results(self, results, truncated: bool = False):
        self.result_table.setRowCount(len(results))
        if truncated:
            self.result_label.setText(
                f"找到 {len(results)}+ 条结果（已截断，请缩小搜索条件以查看全部）"
            )
            self.result_label.setStyleSheet("color: #ff9500; font-weight: 600;")
        else:
            self.result_label.setText(f"找到 {len(results)} 条结果")
            self.result_label.setStyleSheet("color: #86868b;")
        # 新搜索完成后隐藏过期提示
        self.stale_banner.setVisible(False)

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
                # 跨项目时补齐缓存，避免显示裸 UUID
                self._ensure_log_cached(asset.log_id)
                log = self._log_cache.get(asset.log_id)
                if log:
                    log_label = f"{log.scene}/{log.shot}/{log.take}"
                else:
                    # 日志可能已被删除，显示为未关联
                    log_label = "（日志已删除）"
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
