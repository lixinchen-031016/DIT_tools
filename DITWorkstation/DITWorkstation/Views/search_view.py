"""素材检索页面"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QDateEdit,
    QCheckBox, QDialog, QMessageBox, QProgressDialog
)
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QCompleter

from DITWorkstation.Utils import (
    format_size, get_db_service, safe_slot, logger,
    get_report_service, open_in_file_manager, pick_save_file,
)
from DITWorkstation.App import config
from DITWorkstation.App.feature_flags import is_enabled
from DITWorkstation.Models import RATING_LABELS, AssetRating
from DITWorkstation.App.session_context import get_data_bus, get_current_workspace_id
from DITWorkstation.ViewModels import TaskViewModel
from DITWorkstation.Views.Widgets import RefreshOnShowView
from DITWorkstation.Views.Widgets.empty_state import attach_empty_state, sync_empty_state
from DITWorkstation.Views.Widgets.table_factory import make_table
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS, TITLE_QSS, SUBTITLE_QSS, PRIMARY_BUTTON_QSS


class DuplicateResultsDialog(QDialog):
    """跨项目重复素材结果对话框"""

    def __init__(self, duplicates, db_service, parent=None):
        super().__init__(parent)
        self.db_service = db_service
        self.duplicates = duplicates
        self.setWindowTitle("🔁 跨项目重复素材")
        self.resize(960, 520)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        total_groups = len(self.duplicates)
        total_assets = sum(d["count"] for d in self.duplicates)
        summary = QLabel(
            f"共发现 {total_groups} 组重复（{total_assets} 条素材）。"
            "同一校验和出现在多个项目/多次导入，双击行可打开文件所在目录，"
            "请人工判定是否清理重复记录。"
        )
        summary.setWordWrap(True)
        summary.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY};")
        layout.addWidget(summary)

        self.table = make_table(
            ["项目", "文件名", "类型", "大小", "场景", "镜头", "评级", "校验和"],
            sortable=True,
        )
        # 个人模式隐藏评级列（与检索结果表保持一致）
        if not is_enabled("ratings"):
            self.table.setColumnHidden(6, True)
        self.table.doubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        project_cache = {}
        for d in self.duplicates:
            for a in d["assets"]:
                if a.project_id not in project_cache:
                    p = self.db_service.get_project(a.project_id)
                    project_cache[a.project_id] = p.name if p else a.project_id
                row = self.table.rowCount()
                self.table.insertRow(row)
                name_item = QTableWidgetItem(a.file_name)
                name_item.setData(Qt.UserRole, a.asset_id)
                self.table.setItem(row, 0, QTableWidgetItem(project_cache[a.project_id]))
                self.table.setItem(row, 1, name_item)
                self.table.setItem(row, 2, QTableWidgetItem(a.file_type))
                self.table.setItem(row, 3, QTableWidgetItem(format_size(a.file_size)))
                self.table.setItem(row, 4, QTableWidgetItem(a.scene))
                self.table.setItem(row, 5, QTableWidgetItem(a.shot))
                self.table.setItem(
                    row, 6,
                    QTableWidgetItem(
                        RATING_LABELS.get(a.rating, RATING_LABELS[AssetRating.NONE.value])
                    ),
                )
                self.table.setItem(row, 7, QTableWidgetItem(a.checksum_value))

    def _on_double_clicked(self, index):
        if not index.isValid():
            return
        name_item = self.table.item(index.row(), 1)
        if name_item is None:
            return
        asset_id = name_item.data(Qt.UserRole)
        asset = self.db_service.get_media_asset(asset_id) if asset_id else None
        if asset is None:
            return
        if not asset.file_path or not open_in_file_manager(asset.file_path):
            QMessageBox.warning(
                self, "打开失败",
                f"无法打开文件所在目录：\n{asset.file_path or '（路径为空）'}"
            )


class SearchView(RefreshOnShowView):
    """素材检索视图"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        # 全局日志缓存：跨项目搜索时也能显示日志标签而非裸 UUID
        self._log_cache = {}
        self._results = []
        self._current_filters = {}
        self._total = 0
        self._page = 0
        self._page_cursors = [None]
        self._next_cursor = None
        self._search_generation = 0
        self._requested_page = 0
        self._pending_search = None
        self._page_vm = TaskViewModel(self, task_store=self.db_service)
        self._page_vm.finished.connect(self._on_page_loaded)
        self._page_vm.error.connect(self._on_page_load_error)
        self._export_vm = TaskViewModel(self, task_store=self.db_service)
        self._export_vm.progress.connect(self._on_export_progress)
        self._export_vm.finished.connect(self._on_export_finished)
        self._export_vm.error.connect(self._on_export_error)
        self._export_progress_dialog = None
        self._setup_ui()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self._load_projects()
        # 监听全局工作区切换，重新加载项目列表（按新工作区过滤）
        bus = get_data_bus()
        bus.workspace_focus_changed.connect(self._on_global_workspace_changed)
        # 监听全局项目切换，同步下拉选中项（其他视图切项目后回到检索页不再过期）
        # 这是用户多次反馈的「上下文不同步」类问题的最后一片拼图
        bus.project_focus_changed.connect(self._on_global_project_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("素材检索")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel("按场景、镜头、日期、文件类型等维度快速检索素材")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # 上下两段：上段搜索条件，下段结果（不使用可拖动分割条，
        # 空间不足时由整个视图的外层滚动条滚动）
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(16)

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
        self.keyword_edit.setToolTip("输入文件名、场景、镜头等关键词")
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
        self.log_filter_label = QLabel("拍摄日志:")
        row3.addWidget(self.log_filter_label)
        row3.addWidget(self.log_combo)

        self.rating_combo = QComboBox()
        self.rating_combo.addItem("全部评级", None)
        # 评级档位标签来自 Models.RATING_LABELS 单一事实源
        self.rating_combo.addItem(f"{RATING_LABELS[AssetRating.USABLE.value]} 及以上", AssetRating.USABLE.value)
        self.rating_combo.addItem(f"{RATING_LABELS[AssetRating.BACKUP.value]} 及以上", AssetRating.BACKUP.value)
        self.rating_combo.addItem(RATING_LABELS[AssetRating.PREFERRED.value], AssetRating.PREFERRED.value)
        self.rating_filter_label = QLabel("评级:")
        row3.addWidget(self.rating_filter_label)
        row3.addWidget(self.rating_combo)
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("标签关键字")
        self.tag_edit.setToolTip("按自定义标签筛选（素材信息视图中设置的标签）")
        self.tag_edit.returnPressed.connect(self._search)
        self._refresh_tag_completer()
        row3.addWidget(QLabel("标签:"))
        row3.addWidget(self.tag_edit)
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

        config_layout.addWidget(search_group)

        # 个人模式：隐藏拍摄日志/评级筛选控件（对象仍创建，
        # _collect_filters 中显式传入 log_id=None / rating=None，不依赖控件取值）
        if not is_enabled("shooting_log"):
            self.log_filter_label.setVisible(False)
            self.log_combo.setVisible(False)
        if not is_enabled("ratings"):
            self.rating_filter_label.setVisible(False)
            self.rating_combo.setVisible(False)

        # 搜索按钮
        btn_layout = QHBoxLayout()
        self.search_btn = QPushButton("🔍 搜索")
        self.search_btn.setToolTip("按条件检索素材")
        self.search_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        self.search_btn.clicked.connect(self._search)
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self._reset)
        export_btn = QPushButton("📤 导出 CSV")
        export_btn.setToolTip("把当前搜索结果导出为 CSV 素材清单（Excel 可直接打开）")
        export_btn.clicked.connect(self._export_csv)
        dup_btn = QPushButton("🔁 跨项目查重")
        dup_btn.setToolTip("按校验和聚合跨项目重复入库的素材")
        dup_btn.clicked.connect(self._find_duplicates)
        btn_layout.addWidget(self.search_btn)
        btn_layout.addWidget(reset_btn)
        btn_layout.addWidget(export_btn)
        btn_layout.addWidget(dup_btn)
        btn_layout.addStretch()
        self.result_label = QLabel("")
        self.result_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")
        btn_layout.addWidget(self.result_label)
        config_layout.addLayout(btn_layout)
        config_layout.addStretch()
        layout.addWidget(config_widget)

        # 下段：结果区
        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)

        # 结果表格
        self.result_table = make_table(
            ["文件名", "类型", "大小", "场景", "镜头", "关联日志", "评级", "校验和", "导入时间"],
            sortable=True,
        )
        # 个人模式：隐藏「关联日志」「评级」列（数据字段保留，仅不展示）
        if not is_enabled("shooting_log"):
            self.result_table.setColumnHidden(5, True)
        if not is_enabled("ratings"):
            self.result_table.setColumnHidden(6, True)
        # 双击打开所在目录
        self.result_table.doubleClicked.connect(self._on_result_double_clicked)
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self._on_result_context_menu)
        result_layout.addWidget(self.result_table)
        attach_empty_state(self.result_table, "🔍", "暂无搜索结果", "设置搜索条件后点击「搜索」按钮")

        # 分页控制条：大结果集分页展示，避免一次性渲染过多行导致卡顿
        page_layout = QHBoxLayout()
        self.prev_page_btn = QPushButton("◀ 上一页")
        self.prev_page_btn.clicked.connect(self._previous_page)
        self.next_page_btn = QPushButton("下一页 ▶")
        self.next_page_btn.clicked.connect(self._next_page)
        self.page_label = QLabel("")
        self.page_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        page_layout.addWidget(self.prev_page_btn)
        page_layout.addWidget(self.page_label)
        page_layout.addWidget(self.next_page_btn)
        page_layout.addStretch()
        self.page_bar = QWidget()
        self.page_bar.setLayout(page_layout)
        self.page_bar.setVisible(False)
        result_layout.addWidget(self.page_bar)

        # 数据过期提示条（其他视图改了数据后，提示用户当前结果可能过期）
        self.stale_banner = QLabel("")
        self.stale_banner.setVisible(False)
        self.stale_banner.setStyleSheet(
            f"background-color: {COLOR.BANNER_WARNING_BG}; color: {COLOR.BANNER_WARNING_FG}; "
            f"padding: 8px 12px; border-radius: {RADIUS.INPUT}px; border: 1px solid {COLOR.BANNER_WARNING_BORDER};"
        )
        # 点击提示条触发重搜
        self.stale_banner.mousePressEvent = lambda _e: self._search()
        result_layout.addWidget(self.stale_banner)
        result_widget.setMinimumHeight(280)
        layout.addWidget(result_widget, 1)

        # 监听全局数据总线，数据变更时显示过期提示
        try:
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

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self._load_projects()
        self._refresh_tag_completer()

    def _on_project_changed(self, index: int):
        self._refresh_log_combo()

    def _on_global_workspace_changed(self, _workspace_id):
        """全局工作区切换 -> 重新加载项目列表（按新工作区过滤），并清空旧结果"""
        self._load_projects()
        self.result_table.setRowCount(0)
        sync_empty_state(self.result_table)
        self.result_label.setText("")
        self.page_bar.setVisible(False)
        self._results = []
        self._current_filters = {}
        self._total = 0
        self._page = 0

    def _on_global_project_changed(self, project_id):
        """全局项目切换 -> 同步下拉选中项，保持检索上下文与其他视图一致。

        - project_id 为 None：选中「全部项目」（index 0）
        - project_id 在当前下拉中存在：选中它
        - project_id 不在当前下拉（可能因工作区过滤被排除）：重载项目列表后再选中
        """
        if project_id is None:
            self.project_combo.blockSignals(True)
            self.project_combo.setCurrentIndex(0)
            self.project_combo.blockSignals(False)
            self._refresh_log_combo()
            return

        # 在下拉中查找匹配项
        target_index = -1
        for i in range(self.project_combo.count()):
            if self.project_combo.itemData(i) == project_id:
                target_index = i
                break

        if target_index == -1:
            # 不在当前列表（可能工作区已切换但本视图尚未重载），先重载再查找
            self._load_projects()
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == project_id:
                    target_index = i
                    break

        if target_index >= 0 and target_index != self.project_combo.currentIndex():
            self.project_combo.blockSignals(True)
            self.project_combo.setCurrentIndex(target_index)
            self.project_combo.blockSignals(False)
            # 手动刷新日志下拉（blockSignals 阻止了 _on_project_changed）
            self._refresh_log_combo()

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

    @safe_slot("搜索失败")
    def _search(self, go_to_page: int = 0):
        """执行搜索并加载指定页（默认从第一页开始）。"""
        if self._page_vm.is_running():
            # 连续输入或点击搜索时，旧查询的结果不应覆盖最新筛选条件。
            self._pending_search = go_to_page
            self._search_generation += 1
            self._page_vm.cancel()
            self.result_label.setText("正在切换搜索条件…")
            return
        self._current_filters = self._collect_filters()
        self._total = None
        self._page = 0
        self._page_cursors = [None]
        self._next_cursor = None
        self._requested_page = max(0, int(go_to_page))
        self._search_generation += 1
        self.result_table.setRowCount(0)
        sync_empty_state(self.result_table)
        self.result_label.setText("正在搜索…")
        self._load_page()

    def _collect_filters(self) -> dict:
        project_id = self.project_combo.currentData()
        scene = self.scene_edit.text() or None
        shot = self.shot_edit.text() or None
        keyword = self.keyword_edit.text() or None
        # 个人模式显式传入 log_id=None / rating=None，
        # 不假定已隐藏的界面控件存在有效值
        log_id = (self.log_combo.currentData() or None) if is_enabled("shooting_log") else None
        rating = self.rating_combo.currentData() if is_enabled("ratings") else None
        tag = self.tag_edit.text().strip() or None

        file_type = None
        if self.type_combo.currentIndex() > 0:
            file_type = self.type_combo.currentText()

        date_from = None
        date_to = None
        if self.date_check.isChecked():
            date_from = self.date_from.date().toString("yyyy-MM-dd")
            date_to = self.date_to.date().toString("yyyy-MM-dd") + " 23:59:59"

        return dict(
            project_id=project_id,
            scene=scene,
            shot=shot,
            file_type=file_type,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
            log_id=log_id,
            rating=rating,
            tag=tag,
        )

    def _load_page(self):
        """在后台按当前游标读取一页，避免 SQLite/FTS 查询阻塞主线程。"""
        if self._page_vm.is_running():
            return
        page_size = getattr(config, "search_page_size", 500)
        page_size = max(1, int(page_size))
        cursor = self._page_cursors[self._page]
        generation = self._search_generation
        page = self._page
        filters = dict(self._current_filters)
        total = self._total

        def load_page(cancel_check):
            if cancel_check():
                raise InterruptedError("搜索已取消")
            resolved_total = self.db_service.count_assets(**filters) if total is None else total
            if cancel_check():
                raise InterruptedError("搜索已取消")
            results, next_cursor = self.db_service.get_search_asset_page(
                **filters, page_size=page_size, cursor=cursor,
            )
            return {
                "generation": generation, "page": page, "total": resolved_total,
                "page_size": page_size, "results": results, "next_cursor": next_cursor,
            }

        self.prev_page_btn.setEnabled(False)
        self.next_page_btn.setEnabled(False)
        self._page_vm.start(
            load_page, inject_cancel_check=True, task_name="search_asset_page",
            project_id=filters.get("project_id"),
        )

    def _on_page_loaded(self, result):
        if result.get("generation") != self._search_generation:
            self._start_pending_search()
            return
        self._total = result["total"]
        self._page = result["page"]
        self._next_cursor = result["next_cursor"]
        # Keyset 游标不支持随机跳转。保留旧的 go_to_page 契约时，在后台逐页前进。
        if self._page < self._requested_page and self._next_cursor is not None:
            self._page += 1
            if len(self._page_cursors) <= self._page:
                self._page_cursors.append(self._next_cursor)
            else:
                self._page_cursors[self._page] = self._next_cursor
            self._load_page()
            return
        total_pages = max(1, (self._total + result["page_size"] - 1) // result["page_size"])
        self._display_results(result["results"], self._total, self._page, total_pages)
        self._start_pending_search()

    def _on_page_load_error(self, error: str):
        logger.warning(f"素材搜索失败: {error}")
        self.result_label.setText(f"搜索失败：{error}")
        self.prev_page_btn.setEnabled(self._page > 0)
        self.next_page_btn.setEnabled(self._next_cursor is not None)
        self._start_pending_search()

    def _start_pending_search(self):
        if self._pending_search is None:
            return
        go_to_page = self._pending_search
        self._pending_search = None
        self._search(go_to_page=go_to_page)

    def _next_page(self):
        if self._next_cursor is None:
            return
        self._page += 1
        if len(self._page_cursors) <= self._page:
            self._page_cursors.append(self._next_cursor)
        else:
            self._page_cursors[self._page] = self._next_cursor
        self._load_page()

    def _previous_page(self):
        if self._page <= 0:
            return
        self._page -= 1
        self._load_page()

    def _display_results(self, results, total: int = 0, page: int = 0, total_pages: int = 1):
        self._results = results
        # QTableWidget 在逐格插入时若保持排序开启，会为每个单元格重排一次；
        # 500 条结果会放大成数千次排序。仅在填充期间关闭，结束后恢复用户排序。
        sorting_enabled = self.result_table.isSortingEnabled()
        self.result_table.setSortingEnabled(False)
        self.result_table.setRowCount(len(results))
        sync_empty_state(self.result_table)
        if total > 0:
            self.result_label.setText(f"共 {total} 条结果")
            self.result_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")
        else:
            self.result_label.setText("共 0 条结果")
            self.result_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")

        # 分页控件状态
        self.page_bar.setVisible(total > 0 and total_pages > 1)
        self.page_label.setText(f"第 {page + 1} / {total_pages} 页")
        self.prev_page_btn.setEnabled(page > 0)
        self.next_page_btn.setEnabled(self._next_cursor is not None)

        # 新搜索完成后隐藏过期提示
        self.stale_banner.setVisible(False)

        try:
            for i, asset in enumerate(results):
                name_item = QTableWidgetItem(asset.file_name)
                # 存 asset_id 到 UserRole，双击/右键可排序安全地定位 asset
                name_item.setData(Qt.UserRole, asset.asset_id)
                self.result_table.setItem(i, 0, name_item)
                self.result_table.setItem(i, 1, QTableWidgetItem(asset.file_type))
                self.result_table.setItem(i, 2, QTableWidgetItem(format_size(asset.file_size)))
                self.result_table.setItem(i, 3, QTableWidgetItem(asset.scene))
                self.result_table.setItem(i, 4, QTableWidgetItem(asset.shot))

                log_label = ""
                if asset.log_id:
                    # 跨项目时补齐缓存，避免显示裸 UUID
                    self._ensure_log_cached(asset.log_id)
                    log = self._log_cache.get(asset.log_id)
                    log_label = f"{log.scene}/{log.shot}/{log.take}" if log else "（日志已删除）"
                self.result_table.setItem(i, 5, QTableWidgetItem(log_label))

                rating_label = RATING_LABELS.get(asset.rating, RATING_LABELS[AssetRating.NONE.value])
                self.result_table.setItem(i, 6, QTableWidgetItem(rating_label))
                checksum_short = asset.checksum_value[:12] + "..." if asset.checksum_value else ""
                self.result_table.setItem(i, 7, QTableWidgetItem(checksum_short))
                self.result_table.setItem(i, 8, QTableWidgetItem(
                    asset.date_imported.strftime("%Y-%m-%d %H:%M")
                ))
        finally:
            self.result_table.setSortingEnabled(sorting_enabled)

    @safe_slot("导出 CSV 失败")
    def _export_csv(self):
        """把全部搜索结果（不分页）导出为 CSV 素材清单。"""
        if not self._results:
            QMessageBox.information(self, "提示", "请先搜索，再将结果导出为 CSV")
            return
        from datetime import datetime
        default_name = f"素材清单_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = pick_save_file(
            self, "导出素材清单 CSV", default_name, "CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not path:
            return
        self._export_progress_dialog = QProgressDialog("正在导出素材清单…", "取消", 0, 100, self)
        self._export_progress_dialog.setWindowTitle("导出 CSV")
        self._export_progress_dialog.setWindowModality(Qt.WindowModal)
        self._export_progress_dialog.canceled.connect(self._export_vm.cancel)
        self._export_progress_dialog.show()

        def export_task(progress_callback, cancel_check):
            assets = self.db_service.iter_search_assets(**self._current_filters)
            get_report_service().export_assets_csv_iter(
                assets, path, total=self._total, cancel_check=cancel_check,
                progress_callback=lambda current, total, message: progress_callback(
                    "export", current / total if total else 0.0, message,
                ),
            )
            return {"path": path, "count": self._total}

        self._export_vm.start(
            export_task, inject_progress=True, inject_cancel_check=True,
            task_name="csv_export", project_id=self._current_filters.get("project_id"),
            recovery_info={"output_path": path},
        )

    def _on_export_progress(self, _target, progress, message):
        if self._export_progress_dialog is not None:
            self._export_progress_dialog.setValue(int(progress * 100))
            self._export_progress_dialog.setLabelText(message)

    def _on_export_finished(self, result):
        if self._export_progress_dialog is not None:
            self._export_progress_dialog.close()
            self._export_progress_dialog = None
        QMessageBox.information(self, "导出完成", f"已导出 {result['count']} 条素材记录：\n{result['path']}")

    def _on_export_error(self, message):
        if self._export_progress_dialog is not None:
            self._export_progress_dialog.close()
            self._export_progress_dialog = None
        QMessageBox.information(self, "导出已取消" if "取消" in message else "导出失败", message)

    def _refresh_tag_completer(self):
        """从数据库加载全部标签用于输入自动补全。"""
        try:
            tags = self.db_service.get_all_tags()
        except Exception as e:
            logger.warning(f"加载标签补全失败: {e}")
            tags = []
        completer = QCompleter(tags, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.tag_edit.setCompleter(completer)

    @safe_slot("跨项目查重失败")
    def _find_duplicates(self):
        """跨项目按校验和查重，结果在独立对话框展示。"""
        duplicates = self.db_service.find_duplicate_assets()
        if not duplicates:
            QMessageBox.information(self, "跨项目查重", "未发现重复入库的素材 ✅")
            return
        dialog = DuplicateResultsDialog(duplicates, self.db_service, parent=self)
        dialog.exec()

    def _reset(self):
        self.keyword_edit.clear()
        self.scene_edit.clear()
        self.shot_edit.clear()
        self.type_combo.setCurrentIndex(0)
        self.log_combo.setCurrentIndex(0)
        self.tag_edit.clear()
        self.date_check.setChecked(False)
        self.project_combo.setCurrentIndex(0)
        self.result_table.setRowCount(0)
        sync_empty_state(self.result_table)
        self.result_label.setText("")
        self.page_bar.setVisible(False)
        self._results = []
        self._current_filters = {}
        self._total = 0
        self._page = 0

    def _on_result_double_clicked(self, index):
        """双击检索结果 → 打开所在目录（通过 UserRole 取 asset_id，排序安全）"""
        if not index.isValid():
            return
        name_item = self.result_table.item(index.row(), 0)
        if name_item is None:
            return
        asset_id = name_item.data(Qt.UserRole)
        if not asset_id:
            return
        asset = self.db_service.get_media_asset(asset_id)
        if asset is not None:
            self._open_result_directory(asset)

    def _on_result_context_menu(self, pos):
        """检索结果表右键菜单：打开所在目录 / 复制路径"""
        from PySide6.QtWidgets import QMenu
        item = self.result_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        name_item = self.result_table.item(row, 0)
        asset_id = name_item.data(Qt.UserRole) if name_item else None
        asset = self.db_service.get_media_asset(asset_id) if asset_id else None
        if not asset:
            return

        menu = QMenu(self)
        action_open_dir = menu.addAction("打开所在目录")
        action_copy_path = menu.addAction("复制文件路径")

        action = menu.exec(self.result_table.viewport().mapToGlobal(pos))
        if action == action_open_dir:
            self._open_result_directory(asset)
        elif action == action_copy_path:
            self._copy_result_path(asset)

    def _open_result_directory(self, asset):
        """在文件管理器中打开素材所在目录"""
        import os
        from PySide6.QtWidgets import QMessageBox
        path = asset.file_path
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "路径不存在", f"文件不存在：\n{path}")
            return
        if not open_in_file_manager(path):
            QMessageBox.warning(self, "打开失败", "无法打开文件管理器，请检查路径权限。")

    def _copy_result_path(self, asset):
        """复制文件路径到剪贴板"""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(asset.file_path or "")
        logger.info(f"已复制路径到剪贴板: {asset.file_path}")
