"""素材库页面：筛选、分页列表、详情面板与批量整理。

本视图合并了原「素材检索」与「素材信息」两个页面：
- 上方筛选区沿用检索条件（项目/关键词/场景/镜头/类型/日志/评级/标签/日期）；
- 左侧列表复用素材分页模型，支持多选与批量评级/删除；
- 右侧详情面板展示 EXIF、视频元数据、标签备注，并提供重新链接与丢失检测；
- 「拍摄时间线」作为列表的另一种浏览方式保留。
"""

import json
import os
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from DITWorkstation.App import config
from DITWorkstation.App.feature_flags import is_enabled
from DITWorkstation.App.session_context import get_current_workspace_id, get_data_bus
from DITWorkstation.Models import RATING_LABELS, AssetRating
from DITWorkstation.Services.thumbnail_service import SIZE_LARGE
from DITWorkstation.Utils import (
    WorkerThread,
    format_size,
    get_asset_relink_service,
    get_db_service,
    get_metadata_service,
    get_report_service,
    get_thumbnail_service,
    logger,
    now_local,
    open_in_file_manager,
    pick_directory,
    pick_save_file,
    safe_slot,
)
from DITWorkstation.ViewModels import TaskViewModel
from DITWorkstation.Views.Styles.theme import (
    COLOR,
    FONT_SIZE,
    PRIMARY_BUTTON_QSS,
    RADIUS,
    SECONDARY_BUTTON_QSS,
    SUBTITLE_QSS,
    TITLE_QSS,
)
from DITWorkstation.Views.Widgets import (
    AssetTableModel,
    CaptureTimelineWidget,
    RefreshOnShowView,
    WorkspaceProjectSelector,
)
from DITWorkstation.Views.Widgets.empty_state import (
    attach_empty_state,
    sync_empty_state,
)
from DITWorkstation.Views.Widgets.error_dialog import show_error
from DITWorkstation.Views.Widgets.table_factory import make_table, make_table_view

# 缺失值占位符
_PLACEHOLDER = "—"

# 值标签样式：有值时深色加粗，缺失时灰色
_STYLE_HAS_VALUE = (
    f"color: {COLOR.TEXT_PRIMARY}; font-size: {FONT_SIZE.BASE}px; font-weight: 500;"
)
_STYLE_MISSING = (
    f"color: {COLOR.DISABLED}; font-size: {FONT_SIZE.BASE}px; font-style: italic;"
)

# 分组标题样式
_GROUP_STYLE = f"""
    QGroupBox {{
        font-size: {FONT_SIZE.MD}px;
        font-weight: 600;
        color: {COLOR.TEXT_PRIMARY};
        border: 1px solid {COLOR.BORDER};
        border-radius: {RADIUS.GROUP}px;
        margin-top: 12px;
        padding-top: 16px;
        background-color: {COLOR.BG_GROUP};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 16px;
        padding: 0 6px;
    }}
"""

# 列表列索引（与 _asset_cell 保持一致）
_COL_NAME = 0
_COL_LOG = 5
_COL_RATING = 6
_COL_STATUS = 9


class _BatchExifWorker(QThread):
    """批量重新读取 EXIF 的后台线程"""

    progress = Signal(int, int, str)  # current, total, message
    finished_batch = Signal(int, int)  # success_count, total_count
    error = Signal(str)  # 错误信息（确保异常时 UI 能恢复）
    # 保持与 WorkerThread 一致的信号别名，使调用方可以统一用 thread_finished 释放资源
    thread_finished = QThread.finished

    def __init__(self, db_service, project_id, total, metadata_service):
        super().__init__()
        self._db_service = db_service
        self._project_id = project_id
        self._total = total
        self._metadata_service = metadata_service
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = self._total
        success = 0
        try:
            for i, asset in enumerate(
                self._db_service.iter_project_assets(self._project_id), 1
            ):
                if self._cancelled:
                    break
                try:
                    file_path = asset.file_path
                    if not file_path or not Path(file_path).exists():
                        self.progress.emit(
                            i, total, f"跳过：{asset.file_name}（文件不存在）"
                        )
                        continue

                    update_fields = {}

                    # 视频文件读取视频元数据
                    if asset.asset_type == "video":
                        vm = self._metadata_service.read_video_metadata(file_path)
                        if vm.width:
                            update_fields["width"] = vm.width
                        if vm.height:
                            update_fields["height"] = vm.height
                        if vm.duration_seconds:
                            update_fields["duration_seconds"] = vm.duration_seconds
                        update_fields["video_metadata"] = json.dumps(
                            {
                                "codec": vm.codec,
                                "frame_rate": vm.frame_rate,
                                "bit_rate": vm.bit_rate,
                                "audio_codec": vm.audio_codec,
                                "audio_sample_rate": vm.audio_sample_rate,
                            }
                        )
                    else:
                        # 图片/RAW 读取 EXIF
                        metadata = self._metadata_service.read_metadata(file_path)
                        if metadata.camera_make:
                            update_fields["camera_make"] = metadata.camera_make
                        if metadata.camera_model:
                            update_fields["camera_model"] = metadata.camera_model
                        if metadata.lens_model:
                            update_fields["lens_model"] = metadata.lens_model
                        if metadata.iso:
                            update_fields["iso"] = metadata.iso
                        if metadata.aperture:
                            update_fields["aperture"] = metadata.aperture
                        if metadata.shutter_speed:
                            update_fields["shutter_speed"] = metadata.shutter_speed
                        if metadata.focal_length:
                            update_fields["focal_length"] = metadata.focal_length
                        if metadata.width:
                            update_fields["width"] = metadata.width
                        if metadata.height:
                            update_fields["height"] = metadata.height
                        if metadata.date_taken:
                            update_fields["date_taken"] = metadata.date_taken

                    if update_fields:
                        ok = self._db_service.update_media_asset(
                            asset.asset_id, **update_fields
                        )
                        if ok:
                            success += 1
                            self.progress.emit(i, total, f"已更新：{asset.file_name}")
                        else:
                            self.progress.emit(i, total, f"更新失败：{asset.file_name}")
                    else:
                        self.progress.emit(i, total, f"无元数据：{asset.file_name}")
                except Exception as e:
                    # 单个文件异常不中断整体流程
                    self.progress.emit(i, total, f"出错：{e}")

            self.finished_batch.emit(success, total)
        except Exception as e:
            self.error.emit(str(e))


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
        # 个人模式隐藏评级列（与素材库列表保持一致）
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
                self.table.setItem(
                    row, 0, QTableWidgetItem(project_cache[a.project_id])
                )
                self.table.setItem(row, 1, name_item)
                self.table.setItem(row, 2, QTableWidgetItem(a.file_type))
                self.table.setItem(row, 3, QTableWidgetItem(format_size(a.file_size)))
                self.table.setItem(row, 4, QTableWidgetItem(a.scene))
                self.table.setItem(row, 5, QTableWidgetItem(a.shot))
                self.table.setItem(
                    row,
                    6,
                    QTableWidgetItem(
                        RATING_LABELS.get(
                            a.rating, RATING_LABELS[AssetRating.NONE.value]
                        )
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
                self,
                "打开失败",
                f"无法打开文件所在目录：\n{asset.file_path or '（路径为空）'}",
            )


class LibraryView(RefreshOnShowView):
    """素材库视图 — 检索、预览与整理同一份素材数据。"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self.metadata_service = get_metadata_service()
        self.thumbnail_service = get_thumbnail_service()
        self.thumbnail_service.thumbnail_ready.connect(self._on_thumbnail_ready)

        # 检索状态
        self._log_cache = {}
        self._current_filters = {}
        self._total = 0
        self._page = 0
        self._page_cursors = [None]
        self._next_cursor = None
        self._search_generation = 0
        self._requested_page = 0
        self._pending_search = None
        self._timeline_period = (None, None)

        # 列表与详情状态
        self.current_asset = None
        self._assets = []
        self._missing_ids = set()
        self._missing_scan_worker = None
        self._missing_scan_generation = 0
        self._missing_scan_pending = False
        self._batch_worker = None

        # 后台任务
        self._page_vm = TaskViewModel(self, task_store=self.db_service)
        self._page_vm.finished.connect(self._on_page_loaded)
        self._page_vm.error.connect(self._on_page_load_error)
        self._export_vm = TaskViewModel(self, task_store=self.db_service)
        self._export_vm.progress.connect(self._on_export_progress)
        self._export_vm.finished.connect(self._on_export_finished)
        self._export_vm.error.connect(self._on_export_error)
        self._export_progress_dialog = None

        self._setup_ui()
        self.selector.project_changed.connect(self._on_project_changed)
        self.selector.workspace_changed.connect(self._on_workspace_changed)
        try:
            get_data_bus().data_changed.connect(self._on_data_changed)
        except Exception as e:
            logger.warning(f"素材库连接 data_bus 失败: {e}")

    # ===================== UI 构建 =====================

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self._setup_header(layout)
        layout.addWidget(self._setup_filters())

        self.list_container = self._setup_list_panel()
        self.detail_panel = self._setup_detail_panel()
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addWidget(self.list_container, 3)
        content_layout.addWidget(self.detail_panel, 2)
        content.setMinimumHeight(420)
        self.content_widget = content
        layout.addWidget(content, 1)

        self.timeline = CaptureTimelineWidget()
        self.timeline.setVisible(False)
        self.timeline.period_selected.connect(self._on_timeline_period_selected)
        layout.addWidget(self.timeline, 1)

        # 底部批量 EXIF 进度
        self.batch_progress_frame = QFrame()
        batch_layout = QVBoxLayout(self.batch_progress_frame)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.setSpacing(4)
        self.batch_status_label = QLabel("")
        self.batch_status_label.setStyleSheet(
            f"font-size: {FONT_SIZE.SM}px; color: {COLOR.TEXT_SECONDARY};"
        )
        batch_layout.addWidget(self.batch_status_label)
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        batch_layout.addWidget(self.batch_progress)
        self.batch_progress_frame.setVisible(False)
        layout.addWidget(self.batch_progress_frame)

    def _setup_header(self, layout):
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("素材库")
        title.setStyleSheet(TITLE_QSS)
        title_col.addWidget(title)
        subtitle = QLabel("按条件检索素材，并在同一页面查看详情、评级与整理")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        self.batch_exif_btn = QPushButton("重新读取全部 EXIF")
        self.batch_exif_btn.setToolTip("对当前项目下全部素材重新读取 EXIF/视频元数据")
        self.batch_exif_btn.setStyleSheet(SECONDARY_BUTTON_QSS)
        self.batch_exif_btn.clicked.connect(self._batch_refresh_exif)
        self.batch_exif_btn.setEnabled(False)
        header.addWidget(self.batch_exif_btn)

        self.relink_missing_btn = QPushButton("重新链接丢失素材")
        self.relink_missing_btn.setToolTip(
            "选择新根目录，预览并回写可唯一匹配的移动后素材路径"
        )
        self.relink_missing_btn.clicked.connect(self._relink_missing_assets)
        self.relink_missing_btn.setEnabled(False)
        header.addWidget(self.relink_missing_btn)

        self.cleanup_missing_btn = QPushButton("清理丢失素材")
        self.cleanup_missing_btn.setToolTip(
            "删除数据库中「文件已丢失」的素材记录（不删除磁盘文件）"
        )
        self.cleanup_missing_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.DANGER_HOVER};
                color: white;
                border: none;
                border-radius: {RADIUS.BUTTON}px;
                padding: 8px 16px;
                font-size: {FONT_SIZE.BASE}px;
            }}
            QPushButton:hover {{ background-color: {COLOR.DANGER}; }}
            QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}
        """)
        self.cleanup_missing_btn.clicked.connect(self._batch_cleanup_missing)
        self.cleanup_missing_btn.setEnabled(False)
        header.addWidget(self.cleanup_missing_btn)

        layout.addLayout(header)

    def _setup_filters(self):
        group = QGroupBox("筛选")
        form = QFormLayout(group)
        form.setContentsMargins(16, 12, 16, 12)
        form.setVerticalSpacing(8)

        # 第一行：工作区/项目 + 关键词 + 类型
        row1 = QHBoxLayout()
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_new_project=True,
            none_label="（全部项目）",
            broadcast_none=False,
            db_service=self.db_service,
        )
        row1.addWidget(self.selector, 1)
        row1.addWidget(QLabel("关键词:"))
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("文件名/场景/镜头/标签/备注")
        self.keyword_edit.returnPressed.connect(self._search)
        row1.addWidget(self.keyword_edit, 1)
        row1.addWidget(QLabel("类型:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(
            ["全部", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".jpg", ".jpeg"]
        )
        row1.addWidget(self.type_combo)
        form.addRow("", row1)

        # 第二行：场景 / 镜头 / 日志 / 评级
        row2 = QHBoxLayout()
        self.scene_edit = QLineEdit()
        self.scene_edit.setPlaceholderText("场景号")
        self.shot_edit = QLineEdit()
        self.shot_edit.setPlaceholderText("镜头号")
        row2.addWidget(QLabel("场景:"))
        row2.addWidget(self.scene_edit)
        row2.addWidget(QLabel("镜头:"))
        row2.addWidget(self.shot_edit)

        self.log_filter_label = QLabel("拍摄日志:")
        self.log_combo = QComboBox()
        self.log_combo.addItem("全部日志", None)
        self.log_combo.setEnabled(False)
        self.log_combo.setMinimumWidth(220)
        row2.addWidget(self.log_filter_label)
        row2.addWidget(self.log_combo)

        self.rating_filter_label = QLabel("评级:")
        self.rating_combo = QComboBox()
        self.rating_combo.addItem("全部评级", None)
        self.rating_combo.addItem(
            f"{RATING_LABELS[AssetRating.USABLE.value]} 及以上",
            AssetRating.USABLE.value,
        )
        self.rating_combo.addItem(
            f"{RATING_LABELS[AssetRating.BACKUP.value]} 及以上",
            AssetRating.BACKUP.value,
        )
        self.rating_combo.addItem(
            RATING_LABELS[AssetRating.PREFERRED.value],
            AssetRating.PREFERRED.value,
        )
        row2.addWidget(self.rating_filter_label)
        row2.addWidget(self.rating_combo)
        row2.addStretch()
        form.addRow("", row2)

        # 第三行：标签 / 日期 / 视图模式
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("标签:"))
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("标签关键字")
        self.tag_edit.returnPressed.connect(self._search)
        row3.addWidget(self.tag_edit)
        self._refresh_tag_completer()

        self.date_check = QCheckBox("限定日期范围")
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        row3.addWidget(self.date_check)
        row3.addWidget(QLabel("从:"))
        row3.addWidget(self.date_from)
        row3.addWidget(QLabel("到:"))
        row3.addWidget(self.date_to)

        row3.addWidget(QLabel("视图:"))
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["列表", "拍摄时间线"])
        self.view_mode_combo.currentIndexChanged.connect(self._on_view_mode_changed)
        row3.addWidget(self.view_mode_combo)
        row3.addStretch()
        form.addRow("", row3)

        # 第四行：操作按钮
        btn_row = QHBoxLayout()
        self.search_btn = QPushButton("搜索")
        self.search_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        self.search_btn.clicked.connect(self._search)
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self._reset)
        export_btn = QPushButton("导出 CSV")
        export_btn.setToolTip("把当前筛选结果导出为 CSV 素材清单（不分页）")
        export_btn.clicked.connect(self._export_csv)
        dup_btn = QPushButton("跨项目查重")
        dup_btn.setToolTip("按校验和聚合跨项目重复入库的素材")
        dup_btn.clicked.connect(self._find_duplicates)
        save_btn = QPushButton("保存搜索")
        save_btn.clicked.connect(self._save_current_search)
        manage_btn = QPushButton("保存的搜索")
        manage_btn.clicked.connect(self._manage_saved_searches)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._search)

        btn_row.addWidget(self.search_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(dup_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(manage_btn)
        btn_row.addStretch()
        self.result_label = QLabel("")
        self.result_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")
        btn_row.addWidget(self.result_label)
        form.addRow("", btn_row)

        # 个人模式：隐藏日志/评级筛选（filters 中强制为 None）
        if not is_enabled("shooting_log"):
            self.log_filter_label.setVisible(False)
            self.log_combo.setVisible(False)
        if not is_enabled("ratings"):
            self.rating_filter_label.setVisible(False)
            self.rating_combo.setVisible(False)

        return group

    def _setup_list_panel(self):
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)

        self.asset_count_label = QLabel("")
        self.asset_count_label.setStyleSheet(
            f"font-size: {FONT_SIZE.SM}px; color: {COLOR.TEXT_SECONDARY};"
        )
        panel_layout.addWidget(self.asset_count_label)

        self.asset_table = make_table_view(
            sortable=True,
            selection_mode=QAbstractItemView.ExtendedSelection,
            resize_to_contents_cols=[1, 2, 3, 4, 6, 8],
        )
        self.asset_model = AssetTableModel(
            [
                "文件名",
                "类型",
                "大小",
                "场景",
                "镜头",
                "关联日志",
                "评级",
                "校验和",
                "导入时间",
                "状态",
            ],
            self._asset_cell,
            self,
        )
        self.asset_table.setModel(self.asset_model)
        self.asset_table.setMinimumHeight(320)
        self.asset_table.selectionModel().selectionChanged.connect(
            self._on_asset_selected
        )
        self.asset_table.doubleClicked.connect(self._on_asset_double_clicked)
        self.asset_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.asset_table.customContextMenuRequested.connect(self._on_asset_context_menu)
        panel_layout.addWidget(self.asset_table, 1)
        attach_empty_state(
            self.asset_table, "🔍", "暂无素材", "设置筛选条件后点击「搜索」"
        )

        # 个人模式：隐藏关联日志/评级列（数据字段保留）
        if not is_enabled("shooting_log"):
            self.asset_table.setColumnHidden(_COL_LOG, True)
        if not is_enabled("ratings"):
            self.asset_table.setColumnHidden(_COL_RATING, True)

        # 分页
        self.asset_page_bar = QWidget()
        page_layout = QHBoxLayout(self.asset_page_bar)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.prev_asset_page_btn = QPushButton("◀ 上一页")
        self.prev_asset_page_btn.clicked.connect(self._previous_asset_page)
        self.asset_page_label = QLabel("")
        self.asset_page_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
        )
        self.next_asset_page_btn = QPushButton("下一页 ▶")
        self.next_asset_page_btn.clicked.connect(self._next_asset_page)
        page_layout.addStretch()
        page_layout.addWidget(self.prev_asset_page_btn)
        page_layout.addWidget(self.asset_page_label)
        page_layout.addWidget(self.next_asset_page_btn)
        page_layout.addStretch()
        self.asset_page_bar.setVisible(False)
        panel_layout.addWidget(self.asset_page_bar)

        # 批量操作
        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)
        self.batch_label = QLabel("批量:")
        batch_row.addWidget(self.batch_label)
        self.batch_rating_buttons = []
        for value, text in sorted(RATING_LABELS.items()):
            btn = QPushButton(text)
            btn.setToolTip(f"把选中素材的评级设为 {text}")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR.BORDER_LIGHT};
                    border: 1px solid {COLOR.BORDER};
                    border-radius: {RADIUS.INPUT}px;
                    padding: 4px 8px;
                    font-size: {FONT_SIZE.SM}px;
                    color: {COLOR.TEXT_PRIMARY};
                }}
                QPushButton:hover {{ background-color: {COLOR.BORDER}; }}
            """)
            btn.clicked.connect(lambda checked, v=value: self._batch_set_rating(v))
            self.batch_rating_buttons.append(btn)
            batch_row.addWidget(btn)

        self.batch_delete_btn = QPushButton("删除选中")
        self.batch_delete_btn.setToolTip("从项目移除选中的素材记录（不删除磁盘文件）")
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.DANGER_HOVER};
                color: white;
                border: none;
                border-radius: {RADIUS.INPUT}px;
                padding: 4px 10px;
                font-size: {FONT_SIZE.SM}px;
            }}
            QPushButton:hover {{ background-color: {COLOR.DANGER}; }}
        """)
        self.batch_delete_btn.clicked.connect(self._batch_delete)
        batch_row.addWidget(self.batch_delete_btn)
        self.batch_selected_label = QLabel("")
        self.batch_selected_label.setStyleSheet(
            f"font-size: {FONT_SIZE.SM}px; color: {COLOR.TEXT_SECONDARY};"
        )
        batch_row.addWidget(self.batch_selected_label)
        batch_row.addStretch()
        panel_layout.addLayout(batch_row)

        # 个人模式：隐藏批量评级按钮（删除保留）
        if not is_enabled("ratings"):
            self.batch_label.setVisible(False)
            for btn in self.batch_rating_buttons:
                btn.setVisible(False)

        # 数据过期提示
        self.stale_banner = QLabel("")
        self.stale_banner.setVisible(False)
        self.stale_banner.setStyleSheet(
            f"background-color: {COLOR.BANNER_WARNING_BG}; color: {COLOR.BANNER_WARNING_FG}; "
            f"padding: 8px 12px; border-radius: {RADIUS.INPUT}px; "
            f"border: 1px solid {COLOR.BANNER_WARNING_BORDER};"
        )
        self.stale_banner.mousePressEvent = lambda _e: self._search()
        panel_layout.addWidget(self.stale_banner)

        return panel

    def _setup_detail_panel(self):
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(300)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        props_widget = QWidget()
        props_widget.setStyleSheet("background: transparent;")
        props_layout = QVBoxLayout(props_widget)
        props_layout.setContentsMargins(8, 0, 8, 8)
        props_layout.setSpacing(0)

        self._setup_props_file_header(props_layout)
        self._setup_missing_banner(props_layout)
        self._setup_rating_row(props_layout)
        self._setup_props_groups(props_layout)
        self._setup_tags_group(props_layout)

        props_layout.addStretch()
        right_scroll.setWidget(props_widget)
        return right_scroll

    def _setup_props_file_header(self, props_layout):
        self.current_file_label = QLabel("请选择左侧素材查看详情")
        self.current_file_label.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; "
            f"padding: 8px 0 12px 0;"
        )
        props_layout.addWidget(self.current_file_label)

        self.thumbnail_label = QLabel("🖼 选择素材后显示缩略图")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setMinimumHeight(180)
        self.thumbnail_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; border: 1px dashed {COLOR.BORDER}; "
            f"border-radius: {RADIUS.INPUT}px; padding: 12px;"
        )
        props_layout.addWidget(self.thumbnail_label)

        refresh_row = QHBoxLayout()
        self.refresh_exif_btn = QPushButton("重新读取当前文件 EXIF")
        self.refresh_exif_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.BORDER_LIGHT};
                border: 1px solid {COLOR.BORDER};
                border-radius: {RADIUS.INPUT}px;
                padding: 6px 14px;
                font-size: {FONT_SIZE.SM}px;
                color: {COLOR.TEXT_PRIMARY};
            }}
            QPushButton:hover {{ background-color: {COLOR.BORDER}; }}
            QPushButton:disabled {{ color: {COLOR.DISABLED}; border-color: {COLOR.BORDER}; }}
        """)
        self.refresh_exif_btn.clicked.connect(self._refresh_single_exif)
        self.refresh_exif_btn.setEnabled(False)
        refresh_row.addWidget(self.refresh_exif_btn)
        refresh_row.addStretch()
        props_layout.addLayout(refresh_row)

    def _setup_missing_banner(self, props_layout):
        self.missing_banner = QLabel("")
        self.missing_banner.setWordWrap(True)
        self.missing_banner.setStyleSheet(f"""
            QLabel {{
                background-color: {COLOR.BANNER_WARNING_BG};
                color: {COLOR.BANNER_WARNING_FG};
                border: 1px solid {COLOR.BANNER_WARNING_BORDER};
                border-radius: {RADIUS.INPUT}px;
                padding: 8px 10px;
                font-size: {FONT_SIZE.SM}px;
                font-weight: 600;
            }}
        """)
        self.missing_banner.setVisible(False)
        props_layout.addWidget(self.missing_banner)

    def _setup_rating_row(self, props_layout):
        rating_row = QHBoxLayout()
        self.rating_label = QLabel("⭐ 镜次评级")
        self.rating_label.setStyleSheet(
            f"font-size: {FONT_SIZE.BASE}px; font-weight: 600; "
            f"color: {COLOR.TEXT_PRIMARY}; padding: 4px 0;"
        )
        rating_row.addWidget(self.rating_label)
        rating_row.addStretch()

        self._rating_buttons = []
        for value, text in sorted(RATING_LABELS.items()):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR.BORDER_LIGHT};
                    border: 1px solid {COLOR.BORDER};
                    border-radius: {RADIUS.INPUT}px;
                    padding: 4px 10px;
                    font-size: {FONT_SIZE.SM}px;
                    color: {COLOR.TEXT_PRIMARY};
                }}
                QPushButton:hover {{ background-color: {COLOR.BORDER}; }}
                QPushButton:checked {{
                    background-color: {COLOR.PRIMARY};
                    color: white;
                    border-color: {COLOR.PRIMARY};
                }}
                QPushButton:disabled {{ color: {COLOR.DISABLED}; border-color: {COLOR.BORDER}; }}
            """)
            btn.clicked.connect(lambda checked, v=value: self._set_rating(v))
            self._rating_buttons.append((value, btn))
            rating_row.addWidget(btn)
        props_layout.addLayout(rating_row)

        # 个人模式：隐藏评级操作入口
        if not is_enabled("ratings"):
            self.rating_label.setVisible(False)
            for _value, btn in self._rating_buttons:
                btn.setVisible(False)

    def _setup_tags_group(self, props_layout):
        group = QGroupBox("🏷 标签与备注")
        group.setStyleSheet(_GROUP_STYLE)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setContentsMargins(16, 8, 16, 12)
        form.setVerticalSpacing(8)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("多个标签用逗号分隔，如：日戏,主镜头,精修")
        self.tags_edit.setToolTip("自定义标签，可在筛选区按标签检索")
        self.tags_edit.setEnabled(False)
        self._refresh_detail_tags_completer()
        form.addRow("标签:", self.tags_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("备注信息（可选）")
        self.notes_edit.setMaximumHeight(90)
        self.notes_edit.setEnabled(False)
        form.addRow("备注:", self.notes_edit)

        save_row = QHBoxLayout()
        self.save_tags_btn = QPushButton("保存标签与备注")
        self.save_tags_btn.setEnabled(False)
        self.save_tags_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.PRIMARY};
                color: white;
                border: none;
                border-radius: {RADIUS.INPUT}px;
                padding: 6px 14px;
                font-size: {FONT_SIZE.SM}px;
            }}
            QPushButton:hover {{ background-color: {COLOR.PRIMARY_HOVER}; }}
            QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}
        """)
        self.save_tags_btn.clicked.connect(self._save_tags_notes)
        save_row.addWidget(self.save_tags_btn)
        save_row.addStretch()
        form.addRow("", save_row)

        props_layout.addWidget(group)

    def _setup_props_groups(self, props_layout):
        self._group_widgets = {}
        self._label_refs = {}

        self._create_group(
            props_layout,
            "basic",
            "📄 基本信息",
            [
                ("file_name", "文件名"),
                ("file_path", "文件路径"),
                ("asset_type", "资产类型"),
                ("file_type", "文件类型"),
                ("file_size", "文件大小"),
                ("date_imported", "导入时间"),
                ("original_path", "原始路径"),
                ("is_working_copy", "工作副本"),
            ],
        )
        self._create_group(
            props_layout,
            "camera",
            "📷 拍摄参数 (EXIF)",
            [
                ("camera_make", "相机制造商"),
                ("camera_model", "相机型号"),
                ("lens_model", "镜头型号"),
                ("focal_length", "焦距"),
                ("date_taken", "拍摄时间"),
            ],
        )
        self._create_group(
            props_layout,
            "image",
            "🖼 图像尺寸",
            [
                ("dimensions", "尺寸"),
                ("width", "宽度"),
                ("height", "高度"),
            ],
        )
        self._create_group(
            props_layout,
            "video",
            "🎬 视频信息",
            [
                ("duration", "时长"),
                ("codec", "视频编码"),
                ("frame_rate", "帧率"),
                ("bit_rate", "比特率"),
                ("audio_codec", "音频编码"),
                ("audio_sample_rate", "音频采样率"),
            ],
        )
        self._create_group(
            props_layout,
            "project",
            "🔗 项目关联",
            [
                ("scene", "场景号"),
                ("shot", "镜头号"),
                ("take", "镜次"),
                ("log_id", "关联日志"),
                ("backup_locations", "备份位置"),
            ],
        )
        self._create_group(
            props_layout,
            "checksum",
            "🔐 完整性校验",
            [
                ("checksum_algorithm", "校验算法"),
                ("checksum_value", "校验值"),
            ],
        )

    def _create_group(self, parent_layout, group_key, title, fields):
        group = QGroupBox(title)
        group.setStyleSheet(_GROUP_STYLE)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setContentsMargins(16, 8, 16, 12)

        self._label_refs[group_key] = {}
        for key, label_text in fields:
            field_label = QLabel(label_text)
            field_label.setFixedWidth(90)
            field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            field_label.setStyleSheet(
                f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px; "
                f"font-weight: 500;"
            )
            value_label = QLabel(_PLACEHOLDER)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_label.setStyleSheet(_STYLE_MISSING)
            value_label.setMinimumWidth(0)
            value_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self._label_refs[group_key][key] = value_label
            form.addRow(field_label, value_label)

        parent_layout.addWidget(group)
        self._group_widgets[group_key] = group

    def _set_value(self, group_key, key, value, raw=None):
        lbl = self._label_refs[group_key][key]
        display = value if value else _PLACEHOLDER
        lbl.setText(display)
        lbl.setToolTip(raw if raw else display)
        if value:
            lbl.setStyleSheet(_STYLE_HAS_VALUE)
        else:
            lbl.setStyleSheet(_STYLE_MISSING)

    # ===================== 筛选与加载 =====================

    def _on_show_refresh(self):
        self.selector.refresh()
        self._refresh_tag_completer()
        self._refresh_log_combo()
        if self.selector.get_current_project_id():
            self._search()

    @Slot(str)
    def _on_data_changed(self, event: str):
        if event in ("assets_changed", "logs_changed", "all"):
            if self.asset_model.rowCount() > 0:
                self.stale_banner.setText(
                    "⚠ 数据已变更，当前结果可能过期。点击此处或「搜索」按钮刷新。"
                )
                self.stale_banner.setVisible(True)

    def _on_project_changed(self, _project_id):
        self._refresh_log_combo()
        self._search()

    def _on_workspace_changed(self, _workspace_id):
        self._refresh_log_combo()
        self._reset()

    def _collect_filters(self) -> dict:
        project_id = self.selector.get_current_project_id()
        scene = self.scene_edit.text().strip() or None
        shot = self.shot_edit.text().strip() or None
        keyword = self.keyword_edit.text().strip() or None
        log_id = (
            (self.log_combo.currentData() or None)
            if is_enabled("shooting_log")
            else None
        )
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

        return {
            "project_id": project_id,
            "scene": scene,
            "shot": shot,
            "file_type": file_type,
            "date_from": date_from,
            "date_to": date_to,
            "keyword": keyword,
            "log_id": log_id,
            "rating": rating,
            "tag": tag,
            "taken_from": self._timeline_period[0],
            "taken_to": (self._timeline_period[1] + " 23:59:59")
            if self._timeline_period[1]
            else None,
        }

    @safe_slot("搜索失败")
    def _search(self, go_to_page: int = 0):
        if self._page_vm.is_running():
            self._pending_search = go_to_page
            self._search_generation += 1
            self._page_vm.cancel()
            self.result_label.setText("正在切换搜索条件…")
            return
        self._current_filters = self._collect_filters()
        self._total = 0
        self._page = 0
        self._page_cursors = [None]
        self._next_cursor = None
        self._requested_page = max(0, int(go_to_page))
        self._search_generation += 1
        self._missing_scan_generation += 1
        self._missing_ids = set()
        self._missing_scan_pending = False
        self.asset_model.clear()
        sync_empty_state(self.asset_table)
        self._reset_detail()
        self.result_label.setText("正在搜索…")
        self._load_page()

    def _load_page(self):
        if self._page_vm.is_running():
            return
        page_size = max(1, int(getattr(config, "search_page_size", 500)))
        cursor = self._page_cursors[self._page]
        generation = self._search_generation
        page = self._page
        filters = dict(self._current_filters)
        total = self._total

        if self.view_mode_combo.currentIndex() == 1:

            def load_timeline(cancel_check):
                if cancel_check():
                    raise InterruptedError("时间线查询已取消")
                entries = self.db_service.get_capture_timeline(
                    filters.get("project_id")
                )
                if cancel_check():
                    raise InterruptedError("时间线查询已取消")
                return {"generation": generation, "timeline": entries}

            self._page_vm.start(
                load_timeline,
                inject_cancel_check=True,
                task_name="capture_timeline",
                project_id=filters.get("project_id"),
            )
            return

        def load_page(cancel_check):
            if cancel_check():
                raise InterruptedError("搜索已取消")
            resolved_total = (
                self.db_service.count_assets(**filters) if total == 0 else total
            )
            if cancel_check():
                raise InterruptedError("搜索已取消")
            results, next_cursor = self.db_service.get_search_asset_page(
                **filters,
                page_size=page_size,
                cursor=cursor,
            )
            return {
                "generation": generation,
                "page": page,
                "total": resolved_total,
                "page_size": page_size,
                "results": results,
                "next_cursor": next_cursor,
            }

        self.prev_asset_page_btn.setEnabled(False)
        self.next_asset_page_btn.setEnabled(False)
        self._page_vm.start(
            load_page,
            inject_cancel_check=True,
            task_name="library_asset_page",
            project_id=filters.get("project_id"),
        )

    def _on_page_loaded(self, result):
        if result.get("generation") != self._search_generation:
            self._start_pending_search()
            return
        if "timeline" in result:
            self._display_timeline(result["timeline"])
            self._start_pending_search()
            return
        self._total = result["total"]
        self._page = result["page"]
        self._next_cursor = result["next_cursor"]
        if self._page < self._requested_page and self._next_cursor is not None:
            self._page += 1
            if len(self._page_cursors) <= self._page:
                self._page_cursors.append(self._next_cursor)
            else:
                self._page_cursors[self._page] = self._next_cursor
            self._load_page()
            return
        total_pages = max(
            1, (self._total + result["page_size"] - 1) // result["page_size"]
        )
        # 先决定是否启动文件存在性扫描，再渲染列表，保证状态列显示「检查中…」
        project_id = self._current_filters.get("project_id")
        if project_id and self._page == 0:
            self._missing_scan_pending = True
        self._display_results(result["results"], self._total, self._page, total_pages)
        if project_id and self._page == 0:
            self._start_missing_file_scan(project_id)

        self._start_pending_search()

    def _on_page_load_error(self, error: str):
        logger.warning(f"素材库查询失败: {error}")
        self.result_label.setText(f"搜索失败：{error}")
        self.prev_asset_page_btn.setEnabled(self._page > 0)
        self.next_asset_page_btn.setEnabled(self._next_cursor is not None)
        self._start_pending_search()

    def _start_pending_search(self):
        if self._pending_search is None:
            return
        go_to_page = self._pending_search
        self._pending_search = None
        self._search(go_to_page=go_to_page)

    def _display_results(self, results, total: int, page: int, total_pages: int):
        self._assets = list(results)
        self._reset_detail()
        self.asset_model.clear()
        self.asset_model.set_assets(self._assets)
        sync_empty_state(self.asset_table)
        self.result_label.setText(f"共 {total} 条结果")

        self.asset_page_bar.setVisible(total > 0 and total_pages > 1)
        self.asset_page_label.setText(f"第 {page + 1} / {total_pages} 页")
        self.prev_asset_page_btn.setEnabled(page > 0)
        self.next_asset_page_btn.setEnabled(self._next_cursor is not None)
        self.stale_banner.setVisible(False)
        self.batch_exif_btn.setEnabled(
            bool(self._assets) and bool(self._current_filters.get("project_id"))
        )
        self._refresh_asset_summary()
        self._update_batch_state()

        # 回填已完成的文件存在性扫描结果
        if not self._missing_scan_pending:
            for asset in self._assets:
                is_missing = asset.asset_id in self._missing_ids
                self.asset_model.set_status(
                    asset.asset_id,
                    "⚠ 文件已丢失" if is_missing else "✓ 正常",
                    QColor(COLOR.DANGER) if is_missing else QColor(COLOR.SUCCESS),
                )

    def _display_timeline(self, entries):
        self._assets = []
        self._total = sum(int(item.get("asset_count") or 0) for item in entries)
        self.timeline.set_entries(entries)
        self.result_label.setText(f"时间线共 {self._total} 条素材")
        self.asset_page_bar.setVisible(False)
        self.stale_banner.setVisible(False)

    def _next_asset_page(self):
        if self._next_cursor is None:
            return
        self._page += 1
        if len(self._page_cursors) <= self._page:
            self._page_cursors.append(self._next_cursor)
        else:
            self._page_cursors[self._page] = self._next_cursor
        self._load_page()

    def _previous_asset_page(self):
        if self._page <= 0:
            return
        self._page -= 1
        self._load_page()

    def _on_view_mode_changed(self, index: int):
        timeline_mode = index == 1
        self.content_widget.setVisible(not timeline_mode)
        self.timeline.setVisible(timeline_mode)
        if timeline_mode:
            self._timeline_period = (None, None)
        if self._current_filters or self._assets:
            self._search()

    def _on_timeline_period_selected(self, start: str, end: str):
        self._timeline_period = (start, end)
        # 切回列表模式会触发 _on_view_mode_changed，由后者统一发起查询
        self.view_mode_combo.setCurrentIndex(0)

    # ===================== 列表渲染 =====================

    def _asset_cell(self, asset, column: int):
        if column == 0:
            return asset.file_name, None
        if column == 1:
            return asset.file_type, None
        if column == 2:
            return format_size(asset.file_size), None
        if column == 3:
            return asset.scene, None
        if column == 4:
            return asset.shot, None
        if column == 5:
            return self._log_label(asset), None
        if column == 6:
            return (
                RATING_LABELS.get(asset.rating, RATING_LABELS[AssetRating.NONE.value]),
                None,
            )
        if column == 7:
            checksum = asset.checksum_value or ""
            return (checksum[:12] + "..." if checksum else ""), None
        if column == 8:
            return asset.date_imported.strftime("%Y-%m-%d %H:%M"), None
        # 状态列：缺失扫描结果优先，其次给出默认提示
        return self.asset_model.status_for(
            asset.asset_id,
            (
                "检查中…" if self._missing_scan_pending else "—",
                QColor(COLOR.TEXT_SECONDARY),
            ),
        )

    def _log_label(self, asset) -> str:
        if not asset.log_id:
            return ""
        self._ensure_log_cached(asset.log_id)
        log = self._log_cache.get(asset.log_id)
        return f"{log.scene}/{log.shot}/{log.take}" if log else "（日志已删除）"

    def _refresh_asset_summary(self):
        missing_count = len(self._missing_ids)
        text = f"共 {self._total} 个素材"
        if self._assets:
            start = (
                self._page * max(1, int(getattr(config, "search_page_size", 500))) + 1
            )
            end = start + len(self._assets) - 1
            text += f"（当前显示 {start}-{end}）"
        if missing_count:
            text += f"（{missing_count} 个文件已丢失）"
        self.asset_count_label.setText(text)

        total_pages = max(
            1,
            (self._total + max(1, int(getattr(config, "search_page_size", 500))) - 1)
            // max(1, int(getattr(config, "search_page_size", 500))),
        )
        self.asset_page_label.setText(f"第 {self._page + 1} / {total_pages} 页")
        self.cleanup_missing_btn.setEnabled(
            missing_count > 0 and not self._missing_scan_pending
        )
        self.relink_missing_btn.setEnabled(
            missing_count > 0
            and not self._missing_scan_pending
            and bool(self.selector.get_current_project_id())
        )

    # ===================== 文件存在性与项目联动 =====================

    def _start_missing_file_scan(self, project_id: str, for_cleanup: bool = False):
        generation = self._missing_scan_generation
        worker = WorkerThread(self.db_service.get_missing_file_asset_ids, project_id)
        worker._missing_project_id = project_id
        worker._missing_generation = generation
        worker._missing_for_cleanup = for_cleanup
        worker.finished.connect(self._on_missing_scan_finished)
        worker.error.connect(self._on_missing_scan_error)
        worker.thread_finished.connect(worker.deleteLater)
        self._missing_scan_worker = worker
        worker.start()

    @Slot(object)
    def _on_missing_scan_finished(self, missing_ids):
        worker = self.sender()
        if worker is None:
            return
        project_id = getattr(worker, "_missing_project_id", None)
        generation = getattr(worker, "_missing_generation", -1)
        if generation != self._missing_scan_generation:
            return
        if self.selector.get_current_project_id() != project_id:
            return

        self._missing_scan_pending = False
        self._missing_ids = set(missing_ids or [])
        for asset in self._assets:
            is_missing = asset.asset_id in self._missing_ids
            self.asset_model.set_status(
                asset.asset_id,
                "⚠ 文件已丢失" if is_missing else "✓ 正常",
                QColor(COLOR.DANGER) if is_missing else QColor(COLOR.SUCCESS),
            )
        self._refresh_asset_summary()
        if self.current_asset:
            self._on_asset_selected()

        if getattr(worker, "_missing_for_cleanup", False):
            if isinstance(project_id, str):
                self._confirm_cleanup_missing(project_id)

    @Slot(str)
    def _on_missing_scan_error(self, error: str):
        worker = self.sender()
        if worker is None:
            return
        generation = getattr(worker, "_missing_generation", -1)
        if generation != self._missing_scan_generation:
            return
        self._missing_scan_pending = False
        self.cleanup_missing_btn.setEnabled(False)
        self.relink_missing_btn.setEnabled(False)
        for asset in self._assets:
            self.asset_model.set_status(
                asset.asset_id, "⚠ 检查失败", QColor(COLOR.DANGER)
            )
        logger.warning(f"文件存在性扫描失败: {error}")

    # ===================== 详情面板 =====================

    def _on_asset_selected(self, *_args):
        index = self.asset_table.currentIndex()
        if not index.isValid():
            self.current_asset = None
            self.refresh_exif_btn.setEnabled(False)
            self._sync_rating_buttons(None)
            self.current_file_label.setText("请选择左侧素材查看详情")
            self.thumbnail_label.setPixmap(QPixmap())
            self.thumbnail_label.setText("🖼 选择素材后显示缩略图")
            self.missing_banner.setVisible(False)
            self._clear_properties()
            self._update_batch_state()
            return

        self.current_asset = self.asset_model.asset_at(index.row())
        asset_id = str(self.current_asset.asset_id) if self.current_asset else ""
        if self.current_asset:
            is_missing = asset_id in self._missing_ids
            self._set_asset_missing(index.row(), asset_id, is_missing)
            self.refresh_exif_btn.setEnabled(True)
            self._sync_rating_buttons(self.current_asset.rating)
            self.current_file_label.setText(self.current_asset.file_name)
            self._display_properties(self.current_asset)
            if is_missing:
                self.missing_banner.setText(
                    "⚠ 文件已丢失：该素材在磁盘上不存在，可能已被移动或删除。"
                )
                self.missing_banner.setVisible(True)
                self.thumbnail_label.setPixmap(QPixmap())
                self.thumbnail_label.setText("🚫 文件已丢失，无法生成缩略图")
            else:
                self.missing_banner.setVisible(False)
                self._request_thumbnail(self.current_asset)
        self._update_batch_state()

    def _reset_detail(self):
        self.current_asset = None
        self.refresh_exif_btn.setEnabled(False)
        self._sync_rating_buttons(None)
        self.current_file_label.setText("请选择左侧素材查看详情")
        self.thumbnail_label.setPixmap(QPixmap())
        self.thumbnail_label.setText("🖼 选择素材后显示缩略图")
        self.missing_banner.setVisible(False)
        self._clear_properties()
        self._update_batch_state()

    def _set_asset_missing(self, row: int, asset_id: str, is_missing: bool):
        if is_missing:
            self._missing_ids.add(asset_id)
        else:
            self._missing_ids.discard(asset_id)
        self.asset_model.set_status(
            asset_id,
            "⚠ 文件已丢失" if is_missing else "✓ 正常",
            QColor(COLOR.DANGER) if is_missing else QColor(COLOR.SUCCESS),
        )
        self._refresh_asset_summary()

    def _request_thumbnail(self, asset):
        self.thumbnail_label.setPixmap(QPixmap())
        self.thumbnail_label.setText("生成缩略图中…")
        pix = self.thumbnail_service.get_thumbnail(
            asset.asset_id, asset.file_path, asset.asset_type, SIZE_LARGE
        )
        if pix is not None:
            self._apply_thumbnail(asset.asset_id, pix)

    @Slot(str, QPixmap)
    def _on_thumbnail_ready(self, cache_key, pixmap):
        if self.current_asset and self.current_asset.asset_id == cache_key:
            self._apply_thumbnail(cache_key, pixmap)

    def _apply_thumbnail(self, cache_key, pixmap):
        self.thumbnail_label.setText("")
        self.thumbnail_label.setPixmap(
            pixmap.scaled(320, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _display_properties(self, asset):
        self._set_value("basic", "file_name", asset.file_name)
        self._set_value("basic", "file_path", asset.file_path)
        self._set_value("basic", "asset_type", asset.asset_type)
        self._set_value("basic", "file_type", asset.file_type)
        self._set_value(
            "basic",
            "file_size",
            f"{format_size(asset.file_size)}（{asset.file_size:,} 字节）",
        )
        self._set_value(
            "basic",
            "date_imported",
            asset.date_imported.strftime("%Y-%m-%d %H:%M:%S")
            if asset.date_imported
            else "",
        )
        self._set_value("basic", "original_path", asset.original_path)
        self._set_value(
            "basic", "is_working_copy", "是" if asset.is_working_copy else "否"
        )

        self._set_value("camera", "camera_make", asset.camera_make)
        self._set_value("camera", "camera_model", asset.camera_model)
        self._set_value("camera", "lens_model", asset.lens_model)
        self._set_value("camera", "focal_length", asset.focal_length)
        self._set_value(
            "camera",
            "date_taken",
            asset.date_taken.strftime("%Y-%m-%d %H:%M:%S") if asset.date_taken else "",
        )

        if asset.width and asset.height:
            self._set_value("image", "dimensions", f"{asset.width} × {asset.height} px")
        else:
            self._set_value("image", "dimensions", "")
        self._set_value("image", "width", f"{asset.width} px" if asset.width else "")
        self._set_value("image", "height", f"{asset.height} px" if asset.height else "")

        video_info = {}
        if asset.video_metadata:
            try:
                video_info = json.loads(asset.video_metadata)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"解析 video_metadata 失败: {e}")
        if asset.duration_seconds:
            mins = int(asset.duration_seconds // 60)
            secs = asset.duration_seconds % 60
            self._set_value("video", "duration", f"{mins:02d}:{secs:05.2f}")
        else:
            self._set_value("video", "duration", "")
        self._set_value("video", "codec", video_info.get("codec", ""))
        self._set_value(
            "video",
            "frame_rate",
            f"{video_info['frame_rate']:.1f} fps"
            if video_info.get("frame_rate")
            else "",
        )
        self._set_value(
            "video",
            "bit_rate",
            f"{video_info['bit_rate']:,} bps" if video_info.get("bit_rate") else "",
        )
        self._set_value("video", "audio_codec", video_info.get("audio_codec", ""))
        self._set_value(
            "video",
            "audio_sample_rate",
            f"{video_info['audio_sample_rate']:,} Hz"
            if video_info.get("audio_sample_rate")
            else "",
        )

        self._set_value("project", "scene", asset.scene)
        self._set_value("project", "shot", asset.shot)
        self._set_value("project", "take", asset.take)
        log_label = ""
        if asset.log_id:
            log = self.db_service.get_shooting_log(asset.log_id)
            if log:
                log_label = f"{log.scene}/{log.shot}/{log.take}"
                if log.description:
                    log_label += f" - {log.description}"
            else:
                log_label = "（日志已删除）"
        self._set_value("project", "log_id", log_label, raw=asset.log_id or "")
        backup_locs = asset.backup_locations if asset.backup_locations else []
        self._set_value(
            "project", "backup_locations", "\n".join(backup_locs) if backup_locs else ""
        )

        self._set_value("checksum", "checksum_algorithm", asset.checksum_algorithm)
        self._set_value("checksum", "checksum_value", asset.checksum_value)

        self.tags_edit.setText(asset.tags or "")
        self.notes_edit.setPlainText(asset.notes or "")
        self.tags_edit.setEnabled(True)
        self.notes_edit.setEnabled(True)
        self.save_tags_btn.setEnabled(True)

    def _clear_properties(self):
        for group_labels in self._label_refs.values():
            for lbl in group_labels.values():
                lbl.setText(_PLACEHOLDER)
                lbl.setStyleSheet(_STYLE_MISSING)
                lbl.setToolTip("")
        self.tags_edit.clear()
        self.notes_edit.clear()
        self.tags_edit.setEnabled(False)
        self.notes_edit.setEnabled(False)
        self.save_tags_btn.setEnabled(False)

    # ===================== 批量与编辑操作 =====================

    def _selected_asset_ids(self) -> list:
        ids = []
        if not self.asset_table.selectionModel():
            return ids
        for index in self.asset_table.selectionModel().selectedRows():
            asset = self.asset_model.asset_at(index.row())
            if asset is not None:
                ids.append(asset.asset_id)
        return ids

    def _update_batch_state(self):
        count = len(self._selected_asset_ids())
        enabled = count > 0
        for btn in self.batch_rating_buttons:
            btn.setEnabled(enabled)
        self.batch_delete_btn.setEnabled(enabled)
        self.batch_selected_label.setText(f"已选 {count} 个" if count else "")

    def _sync_rating_buttons(self, rating):
        for value, btn in self._rating_buttons:
            if rating is None:
                btn.setEnabled(False)
                btn.setChecked(False)
            else:
                btn.setEnabled(True)
                btn.setChecked(value == rating)

    @safe_slot("设置评级失败")
    def _set_rating(self, rating: int):
        if not self.current_asset:
            return
        ok = self.db_service.update_media_asset(
            self.current_asset.asset_id, rating=rating
        )
        if not ok:
            return
        self.current_asset = self.db_service.get_media_asset(
            self.current_asset.asset_id
        )
        if self.current_asset is None:
            return
        self._sync_rating_buttons(rating)
        try:
            self.db_service.record_operation(
                "设置评级",
                f"{self.current_asset.file_name} -> {RATING_LABELS.get(rating, rating)}",
                project_id=self.current_asset.project_id,
            )
        except Exception as e:
            logger.warning(f"记录评级操作日志失败: {e}")
        self._sync_xmp_sidecar()
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

    @safe_slot("批量评级失败")
    def _batch_set_rating(self, rating: int):
        ids = self._selected_asset_ids()
        if not ids:
            return
        ok = 0
        for aid in ids:
            if self.db_service.update_media_asset(aid, rating=rating):
                ok += 1
        self._reload_current_page()
        QMessageBox.information(
            self, "批量评级", f"已为 {ok}/{len(ids)} 个素材设置评级"
        )
        self._broadcast_assets_changed()

    @safe_slot("批量删除失败")
    def _batch_delete(self):
        ids = self._selected_asset_ids()
        if not ids:
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定从项目移除 {len(ids)} 条素材记录？\n\n"
            "仅移除数据库记录，不会删除磁盘上的源文件。\n"
            "记录将在回收站保留 30 天，可恢复其元数据。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        ok = self.db_service.delete_media_assets(ids)
        self._reload_current_page()
        QMessageBox.information(self, "删除完成", f"已移除 {ok}/{len(ids)} 条素材记录")
        self._broadcast_assets_changed()

    @safe_slot("清理丢失素材失败")
    def _batch_cleanup_missing(self):
        project_id = self.selector.get_current_project_id()
        if not project_id:
            QMessageBox.information(self, "提示", "请先选择具体项目再清理丢失素材。")
            return
        if self._missing_scan_pending:
            QMessageBox.information(self, "正在检查", "正在检查文件状态，请稍后再试。")
            return
        self._missing_scan_pending = True
        self._refresh_asset_summary()
        self._start_missing_file_scan(project_id, for_cleanup=True)

    def _confirm_cleanup_missing(self, project_id: str):
        if self.selector.get_current_project_id() != project_id:
            return
        missing_ids = sorted(self._missing_ids)
        if not missing_ids:
            QMessageBox.information(
                self, "无丢失素材", "当前项目没有文件已丢失的素材记录。"
            )
            return

        reply = QMessageBox.question(
            self,
            "确认清理丢失素材",
            f"即将删除 {len(missing_ids)} 条「文件已丢失」的素材记录。\n\n"
            "• 仅移除数据库中的素材记录，不会删除磁盘上的任何文件\n"
            "• 仅影响文件已丢失的无效记录，正常素材不会被改动\n"
            "• 记录将在回收站保留 30 天，可恢复其元数据\n\n"
            "确定要清理吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        deleted = self.db_service.delete_media_assets(missing_ids)
        self._reload_current_page()
        QMessageBox.information(
            self, "清理完成", f"已删除 {deleted}/{len(missing_ids)} 条丢失素材记录。"
        )
        self._broadcast_assets_changed()

    @safe_slot("重新链接丢失素材失败")
    def _relink_missing_assets(self):
        project_id = self.selector.get_current_project_id()
        if not project_id:
            QMessageBox.information(self, "提示", "请先选择具体项目再重新链接素材。")
            return
        root = pick_directory(self, "选择素材的新根目录", category="relink_root")
        if not root:
            return
        service = get_asset_relink_service()
        preview = service.preview(project_id, root)
        if preview.matched_count == 0:
            QMessageBox.information(
                self,
                "未找到可重新链接的素材",
                f"未匹配 {preview.unmatched_count} 个素材；"
                f"存在冲突 {preview.conflict_count} 个。",
            )
            return
        selections = self._show_relink_preview(preview)
        if selections is None:
            return
        result = service.apply(preview, selections)
        if not result:
            QMessageBox.warning(self, "重新链接未完成", result.message)
            return
        self._reload_current_page()
        QMessageBox.information(
            self, "重新链接完成", f"已重新链接 {result.affected_count} 个素材。"
        )
        self._broadcast_assets_changed()

    def _show_relink_preview(self, preview):
        dialog = QDialog(self)
        dialog.setWindowTitle("预览重新链接")
        dialog.resize(920, 520)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                f"唯一匹配 {preview.matched_count} 个，冲突 {preview.conflict_count} 个，"
                f"未匹配 {preview.unmatched_count} 个。选择后将批量回写数据库路径。"
            )
        )
        table = QTableWidget(len(preview.matches), 4)
        table.setHorizontalHeaderLabels(["原始文件", "匹配方式", "状态", "目标文件"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        selections = {}
        for row, match in enumerate(preview.matches):
            table.setItem(row, 0, QTableWidgetItem(Path(match.old_path).name))
            table.setItem(row, 1, QTableWidgetItem(match.match_method))
            status = (
                "冲突"
                if match.is_conflict
                else "可链接"
                if match.selected_path
                else "未匹配"
            )
            table.setItem(row, 2, QTableWidgetItem(status))
            combo = QComboBox(table)
            combo.addItem("跳过", "")
            for candidate in match.candidate_paths:
                combo.addItem(candidate, candidate)
            if match.selected_path:
                combo.setCurrentIndex(1)
            combo.setEnabled(bool(match.candidate_paths))
            table.setCellWidget(row, 3, combo)
            selections[match.asset_id] = combo
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return None
        return [
            (asset_id, combo.currentData())
            for asset_id, combo in selections.items()
            if combo.currentData()
        ]

    @safe_slot("保存标签备注失败")
    def _save_tags_notes(self):
        if not self.current_asset:
            return
        ok = self.db_service.update_media_asset(
            self.current_asset.asset_id,
            tags=self.tags_edit.text().strip(),
            notes=self.notes_edit.toPlainText().strip(),
        )
        if not ok:
            QMessageBox.warning(self, "保存失败", "更新标签/备注失败，请重试")
            return
        self.current_asset = self.db_service.get_media_asset(
            self.current_asset.asset_id
        )
        if self.current_asset is None:
            return
        logger.info(f"已保存素材标签/备注: {self.current_asset.asset_id}")
        try:
            self.db_service.record_operation(
                "更新标签/备注",
                self.current_asset.file_name,
                project_id=self.current_asset.project_id,
            )
        except Exception as e:
            logger.warning(f"记录标签操作日志失败: {e}")
        self._sync_xmp_sidecar()
        self._refresh_tag_completer()
        self._broadcast_assets_changed()

    def _sync_xmp_sidecar(self):
        if not self.current_asset or not self.current_asset.file_path:
            return
        asset_type = getattr(self.current_asset, "asset_type", "") or ""
        if asset_type in ("video", "audio"):
            return
        file_path = self.current_asset.file_path
        if not Path(file_path).exists():
            return
        try:
            svc = get_metadata_service()
            tags = self.current_asset.tags or ""
            ok = svc.write_xmp_sidecar(
                file_path,
                rating=self.current_asset.rating,
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                notes=self.current_asset.notes or "",
            )
            if not ok:
                logger.info(f"XMP 同步跳过: {file_path}")
        except Exception as e:
            logger.warning(f"XMP 同步失败 {file_path}: {e}")

    def _reload_current_page(self):
        """保持当前页重新查询（批量操作后调用）。"""
        target_page = self._page
        self._search(go_to_page=target_page)

    def _broadcast_assets_changed(self):
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

    # ===================== 打开目录 / 复制路径 =====================

    def _on_asset_double_clicked(self, index):
        if not index.isValid():
            return
        asset = self.asset_model.asset_at(index.row())
        if asset is not None:
            self._open_asset_directory(asset)

    def _on_asset_context_menu(self, pos):
        index = self.asset_table.indexAt(pos)
        if not index.isValid():
            return
        asset = self.asset_model.asset_at(index.row())
        if not asset:
            return
        menu = QMenu(self)
        action_open_dir = menu.addAction("打开所在目录")
        action_copy_path = menu.addAction("复制文件路径")
        action = menu.exec(self.asset_table.viewport().mapToGlobal(pos))
        if action == action_open_dir:
            self._open_asset_directory(asset)
        elif action == action_copy_path:
            self._copy_asset_path(asset)

    def _open_asset_directory(self, asset):
        path = asset.file_path
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "路径不存在", f"文件不存在：\n{path}")
            return
        if not open_in_file_manager(path):
            QMessageBox.warning(
                self, "打开失败", "无法打开文件管理器，请检查路径权限。"
            )

    def _copy_asset_path(self, asset):
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(asset.file_path or "")
        logger.info(f"已复制路径到剪贴板: {asset.file_path}")

    # ===================== 元数据刷新 =====================

    @safe_slot("读取元数据失败")
    def _refresh_single_exif(self):
        if not self.current_asset:
            return
        file_path = self.current_asset.file_path
        if not file_path or not Path(file_path).exists():
            QMessageBox.warning(self, "提示", "文件不存在，无法读取元数据信息。")
            return

        update_fields = {}
        if self.current_asset.asset_type == "video":
            vm = self.metadata_service.read_video_metadata(file_path)
            if vm.width:
                update_fields["width"] = vm.width
            if vm.height:
                update_fields["height"] = vm.height
            if vm.duration_seconds:
                update_fields["duration_seconds"] = vm.duration_seconds
            update_fields["video_metadata"] = json.dumps(
                {
                    "codec": vm.codec,
                    "frame_rate": vm.frame_rate,
                    "bit_rate": vm.bit_rate,
                    "audio_codec": vm.audio_codec,
                    "audio_sample_rate": vm.audio_sample_rate,
                }
            )
        else:
            metadata = self.metadata_service.read_metadata(file_path)
            if metadata.camera_make:
                update_fields["camera_make"] = metadata.camera_make
            if metadata.camera_model:
                update_fields["camera_model"] = metadata.camera_model
            if metadata.lens_model:
                update_fields["lens_model"] = metadata.lens_model
            if metadata.iso:
                update_fields["iso"] = metadata.iso
            if metadata.aperture:
                update_fields["aperture"] = metadata.aperture
            if metadata.shutter_speed:
                update_fields["shutter_speed"] = metadata.shutter_speed
            if metadata.focal_length:
                update_fields["focal_length"] = metadata.focal_length
            if metadata.width:
                update_fields["width"] = metadata.width
            if metadata.height:
                update_fields["height"] = metadata.height
            if metadata.date_taken:
                update_fields["date_taken"] = metadata.date_taken

        if update_fields:
            ok = self.db_service.update_media_asset(
                self.current_asset.asset_id, **update_fields
            )
            if not ok:
                QMessageBox.warning(self, "更新失败", "数据库更新失败，请重试。")
                return
            self.current_asset = self.db_service.get_media_asset(
                self.current_asset.asset_id
            )
            if self.current_asset is None:
                return
            self._display_properties(self.current_asset)
            self._reload_current_page()
            QMessageBox.information(
                self, "成功", f"已更新 {len(update_fields)} 项元数据。"
            )
        else:
            QMessageBox.information(
                self, "提示", "未读取到元数据信息（该文件可能不含元数据）。"
            )

    @safe_slot("批量读取 EXIF 失败")
    def _batch_refresh_exif(self):
        if self._batch_worker is not None and self._batch_worker.isRunning():
            QMessageBox.information(
                self, "提示", "正在执行批量读取，请等待当前任务完成。"
            )
            return
        project_id = self.selector.get_current_project_id()
        if not project_id:
            QMessageBox.information(self, "提示", "请先选择具体项目。")
            return
        total = self.db_service.count_project_assets(project_id)
        if not total:
            QMessageBox.information(self, "提示", "当前项目没有素材。")
            return
        reply = QMessageBox.question(
            self, "确认", f"将对项目下 {total} 个素材重新读取 EXIF 信息，是否继续？"
        )
        if reply != QMessageBox.Yes:
            return

        self.batch_exif_btn.setEnabled(False)
        self.refresh_exif_btn.setEnabled(False)
        self.batch_progress_frame.setVisible(True)
        self.batch_progress.setVisible(True)
        self.batch_progress.setRange(0, total)
        self.batch_progress.setValue(0)
        self.batch_status_label.setText("正在读取 EXIF 信息...")

        self._batch_worker = _BatchExifWorker(
            self.db_service, project_id, total, self.metadata_service
        )
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.finished_batch.connect(self._on_batch_finished)
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.thread_finished.connect(self._batch_worker.deleteLater)
        self._batch_worker.start()

    def _on_batch_progress(self, current, total, message):
        self.batch_progress.setValue(current)
        self.batch_status_label.setText(f"({current}/{total}) {message}")

    def _on_batch_error(self, error_msg: str):
        self.batch_status_label.setText(f"出错：{error_msg}")
        self.batch_status_label.setStyleSheet(
            f"font-size: {FONT_SIZE.SM}px; color: {COLOR.DANGER};"
        )
        self.batch_exif_btn.setEnabled(True)
        if self.current_asset:
            self.refresh_exif_btn.setEnabled(True)
        self._batch_worker = None
        QTimer.singleShot(3000, lambda: self.batch_progress_frame.setVisible(False))
        show_error(
            title="批量读取出错",
            description=error_msg,
            details=error_msg,
            parent=self,
        )

    def _on_batch_finished(self, success, total):
        self.batch_status_label.setText(
            f"完成：{success}/{total} 个素材成功更新 EXIF 信息"
        )
        self.batch_exif_btn.setEnabled(True)
        if self.current_asset:
            self.refresh_exif_btn.setEnabled(True)
        self._batch_worker = None
        self._reload_current_page()
        if self.current_asset:
            self.current_asset = self.db_service.get_media_asset(
                self.current_asset.asset_id
            )
            if self.current_asset:
                self.current_file_label.setText(self.current_asset.file_name)
                self._display_properties(self.current_asset)
        if success > 0:
            self._broadcast_assets_changed()
        QTimer.singleShot(3000, lambda: self.batch_progress_frame.setVisible(False))

    # ===================== 导出 / 查重 / 保存搜索 =====================

    @safe_slot("导出 CSV 失败")
    def _export_csv(self):
        if not self._assets:
            QMessageBox.information(self, "提示", "请先搜索，再将结果导出为 CSV")
            return
        default_name = f"素材清单_{now_local().strftime('%Y%m%d_%H%M%S')}.csv"
        path = pick_save_file(
            self, "导出素材清单 CSV", default_name, "CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not path:
            return
        self._export_progress_dialog = QProgressDialog(
            "正在导出素材清单…", "取消", 0, 100, self
        )
        self._export_progress_dialog.setWindowTitle("导出 CSV")
        self._export_progress_dialog.setWindowModality(Qt.WindowModal)
        self._export_progress_dialog.canceled.connect(self._export_vm.cancel)
        self._export_progress_dialog.show()

        filters = dict(self._current_filters)
        total = self._total or 0

        def export_task(progress_callback, cancel_check):
            assets = self.db_service.iter_search_assets(**filters)
            get_report_service().export_assets_csv_iter(
                assets,
                path,
                total=total,
                cancel_check=cancel_check,
                progress_callback=lambda current, _total, message: progress_callback(
                    "export",
                    current / total if total else 0.0,
                    message,
                ),
            )
            return {"path": path, "count": total}

        self._export_vm.start(
            export_task,
            inject_progress=True,
            inject_cancel_check=True,
            task_name="csv_export",
            project_id=filters.get("project_id"),
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
        QMessageBox.information(
            self, "导出完成", f"已导出 {result['count']} 条素材记录：\n{result['path']}"
        )

    def _on_export_error(self, message):
        if self._export_progress_dialog is not None:
            self._export_progress_dialog.close()
            self._export_progress_dialog = None
        QMessageBox.information(
            self, "导出已取消" if "取消" in message else "导出失败", message
        )

    @safe_slot("跨项目查重失败")
    def _find_duplicates(self):
        duplicates = self.db_service.find_duplicate_assets()
        if not duplicates:
            QMessageBox.information(self, "跨项目查重", "未发现重复入库的素材")
            return
        dialog = DuplicateResultsDialog(duplicates, self.db_service, parent=self)
        dialog.exec()

    def _save_current_search(self):
        from PySide6.QtWidgets import QInputDialog

        filters = self._collect_filters()
        name, ok = QInputDialog.getText(
            self,
            "保存搜索",
            "搜索名称：",
            text=f"{filters.get('scene') or ''}{filters.get('shot') or ''}",
        )
        if not ok or not name.strip():
            return
        workspace_id = get_current_workspace_id()
        try:
            self.db_service.create_saved_search(
                name.strip(),
                filters,
                workspace_id=workspace_id,
                project_id=filters.get("project_id"),
                is_smart=False,
            )
            QMessageBox.information(self, "已保存", f"搜索「{name.strip()}」已保存。")
        except Exception as exc:
            logger.warning(f"保存搜索失败: {exc}")
            QMessageBox.warning(self, "保存失败", f"无法保存搜索：{exc}")

    def _manage_saved_searches(self):
        from DITWorkstation.Views.Widgets.saved_search_dialog import SavedSearchDialog

        dialog = SavedSearchDialog(
            self,
            db_service=self.db_service,
            on_apply=self._apply_saved_filters,
        )
        dialog.exec()

    def _apply_saved_filters(self, filters: dict):
        if not isinstance(filters, dict):
            return
        self.selector.project_combo.blockSignals(True)
        idx = self.selector.project_combo.findData(filters.get("project_id"))
        self.selector.project_combo.setCurrentIndex(max(idx, 0))
        self.selector.project_combo.blockSignals(False)
        self._refresh_log_combo()

        self.scene_edit.setText(filters.get("scene") or "")
        self.shot_edit.setText(filters.get("shot") or "")
        self.keyword_edit.setText(filters.get("keyword") or "")
        self.tag_edit.setText(filters.get("tag") or "")
        file_type = filters.get("file_type")
        self.type_combo.setCurrentIndex(
            self.type_combo.findText(file_type) if file_type else 0
        )
        if is_enabled("ratings"):
            rating = filters.get("rating")
            self.rating_combo.setCurrentIndex(
                self.rating_combo.findData(rating) if rating is not None else 0
            )
        if is_enabled("shooting_log"):
            log_id = filters.get("log_id")
            self.log_combo.setCurrentIndex(
                self.log_combo.findData(log_id) if log_id else 0
            )
        date_from = filters.get("date_from")
        date_to = filters.get("date_to")
        if date_from or date_to:
            self.date_check.setChecked(True)
            if date_from:
                self.date_from.setDate(QDate.fromString(date_from[:10], "yyyy-MM-dd"))
            if date_to:
                self.date_to.setDate(QDate.fromString(date_to[:10], "yyyy-MM-dd"))
        else:
            self.date_check.setChecked(False)
        self._search()

    def _reset(self):
        self.keyword_edit.clear()
        self.scene_edit.clear()
        self.shot_edit.clear()
        self.type_combo.setCurrentIndex(0)
        self.log_combo.setCurrentIndex(0)
        self.rating_combo.setCurrentIndex(0)
        self.tag_edit.clear()
        self.date_check.setChecked(False)
        self._timeline_period = (None, None)
        self.selector.project_combo.blockSignals(True)
        self.selector.project_combo.setCurrentIndex(0)
        self.selector.project_combo.blockSignals(False)
        self._current_filters = {}
        self._total = 0
        self._page = 0
        self.asset_model.clear()
        sync_empty_state(self.asset_table)
        self.result_label.setText("")
        self.asset_count_label.setText("")
        self.asset_page_bar.setVisible(False)
        self.batch_exif_btn.setEnabled(False)
        self._missing_ids = set()
        self._reset_detail()

    # ===================== 辅助：日志与标签 =====================

    def _refresh_log_combo(self):
        project_id = self.selector.get_current_project_id()
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
        if not log_id or log_id in self._log_cache:
            return
        log = self.db_service.get_shooting_log(log_id)
        if log:
            self._log_cache[log_id] = log

    def _refresh_tag_completer(self):
        try:
            tags = self.db_service.get_all_tags()
        except Exception as e:
            logger.warning(f"加载标签补全失败: {e}")
            tags = []
        completer = QCompleter(tags, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.tag_edit.setCompleter(completer)

    def _refresh_detail_tags_completer(self):
        try:
            tags = self.db_service.get_all_tags()
        except Exception as e:
            logger.warning(f"加载标签补全失败: {e}")
            tags = []
        completer = QCompleter(tags, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.tags_edit.setCompleter(completer)
