"""素材资产 EXIF 信息查看页面"""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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

from DITWorkstation.App.feature_flags import is_enabled
from DITWorkstation.App.session_context import get_data_bus
from DITWorkstation.Models import RATING_LABELS
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
    RADIUS,
    SUBTITLE_QSS,
    TITLE_QSS,
)
from DITWorkstation.Views.Widgets import (
    AssetTableModel,
    RefreshOnShowView,
    WorkspaceProjectSelector,
)
from DITWorkstation.Views.Widgets.empty_state import (
    attach_empty_state,
    sync_empty_state,
)
from DITWorkstation.Views.Widgets.error_dialog import show_error
from DITWorkstation.Views.Widgets.table_factory import make_table_view

# 缺失值占位符
_PLACEHOLDER = "—"

# 值标签样式：有值时深色加粗，缺失时灰色
_STYLE_HAS_VALUE = f"color: {COLOR.TEXT_PRIMARY}; font-size: {FONT_SIZE.BASE}px; font-weight: 500;"
_STYLE_MISSING = f"color: {COLOR.DISABLED}; font-size: {FONT_SIZE.BASE}px; font-style: italic;"

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


class _BatchExifWorker(QThread):
    """批量重新读取 EXIF 的后台线程"""

    progress = Signal(int, int, str)  # current, total, message
    finished_batch = Signal(int, int)  # success_count, total_count
    error = Signal(str)  # 错误信息（确保异常时 UI 能恢复）

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
            for i, asset in enumerate(self._db_service.iter_project_assets(self._project_id), 1):
                if self._cancelled:
                    break
                try:
                    file_path = asset.file_path
                    if not file_path or not Path(file_path).exists():
                        self.progress.emit(i, total, f"跳过：{asset.file_name}（文件不存在）")
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
                        update_fields["video_metadata"] = json.dumps({
                            "codec": vm.codec,
                            "frame_rate": vm.frame_rate,
                            "bit_rate": vm.bit_rate,
                            "audio_codec": vm.audio_codec,
                            "audio_sample_rate": vm.audio_sample_rate,
                        })
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
                        ok = self._db_service.update_media_asset(asset.asset_id, **update_fields)
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


class AssetInfoView(RefreshOnShowView):
    """素材资产信息视图 — 查看导入素材的 EXIF 与元数据详情"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self.metadata_service = get_metadata_service()
        self.thumbnail_service = get_thumbnail_service()
        self.thumbnail_service.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.current_asset = None
        self._batch_worker = None
        self._missing_scan_worker = None
        self._missing_scan_generation = 0
        self._missing_scan_pending = False
        self._assets = []
        self._asset_total = 0
        self._asset_page = 0
        self._asset_page_cursors = [None]
        self._asset_next_cursor = None
        self._asset_load_generation = 0
        self._pending_asset_load = None
        self._asset_page_vm = TaskViewModel(self, task_store=self.db_service)
        self._asset_page_vm.finished.connect(self._on_asset_page_finished)
        self._asset_page_vm.error.connect(self._on_asset_page_error)
        self._export_vm = TaskViewModel(self, task_store=self.db_service)
        self._export_vm.progress.connect(self._on_export_progress)
        self._export_vm.finished.connect(self._on_export_finished)
        self._export_vm.error.connect(self._on_export_error)
        self._export_progress_dialog = None
        self._missing_ids = set()  # 当前项目文件已丢失的素材 asset_id 集合
        self._setup_ui()
        # 项目切换由共享控件广播到全局，本视图仅需监听后刷新素材列表
        self.selector.project_changed.connect(self._on_project_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        self._setup_header(layout)
        content = self._setup_content()
        layout.addWidget(content, 1)

        # === 底部批量操作进度条 ===
        self.batch_progress_frame = QFrame()
        self.batch_progress_frame.setStyleSheet("background: transparent;")
        batch_layout = QVBoxLayout(self.batch_progress_frame)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.setSpacing(4)

        self.batch_status_label = QLabel("")
        self.batch_status_label.setStyleSheet(f"font-size: {FONT_SIZE.SM}px; color: {COLOR.TEXT_SECONDARY};")
        batch_layout.addWidget(self.batch_status_label)

        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        batch_layout.addWidget(self.batch_progress)
        self.batch_progress_frame.setVisible(False)
        layout.addWidget(self.batch_progress_frame)

    def _setup_header(self, layout):
        # === 标题区 ===
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("素材信息")
        title.setStyleSheet(TITLE_QSS)
        title_col.addWidget(title)
        subtitle = QLabel("查看导入素材的 EXIF 拍摄信息与文件元数据详情")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        # 一键重新读取 EXIF 按钮
        self.batch_exif_btn = QPushButton("🔄 一键重新读取全部 EXIF")
        self.batch_exif_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.PRIMARY};
                color: white;
                border: none;
                border-radius: {RADIUS.BUTTON}px;
                padding: 8px 16px;
                font-size: {FONT_SIZE.BASE}px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {COLOR.PRIMARY_HOVER}; }}
            QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}
        """)
        self.batch_exif_btn.clicked.connect(self._batch_refresh_exif)
        self.batch_exif_btn.setEnabled(False)
        header.addWidget(self.batch_exif_btn)

        # 导出素材清单 CSV
        self.export_csv_btn = QPushButton("📤 导出素材清单 CSV")
        self.export_csv_btn.setToolTip("把当前项目的全部素材元数据导出为 CSV（Excel 可直接打开）")
        self.export_csv_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.BG_CARD};
                color: {COLOR.TEXT_PRIMARY};
                border: 1px solid {COLOR.BORDER};
                border-radius: {RADIUS.BUTTON}px;
                padding: 8px 16px;
                font-size: {FONT_SIZE.BASE}px;
            }}
            QPushButton:hover {{ background-color: {COLOR.BORDER_LIGHT}; }}
        """)
        self.export_csv_btn.clicked.connect(self._export_csv)
        header.addWidget(self.export_csv_btn)

        self.relink_missing_btn = QPushButton("🔗 重新链接丢失素材")
        self.relink_missing_btn.setToolTip("选择新根目录，预览并回写可唯一匹配的移动后素材路径")
        self.relink_missing_btn.clicked.connect(self._relink_missing_assets)
        self.relink_missing_btn.setEnabled(False)
        header.addWidget(self.relink_missing_btn)

        # 批量清理已丢失文件对应的素材记录
        self.cleanup_missing_btn = QPushButton("🧹 清理丢失素材")
        self.cleanup_missing_btn.setToolTip("一键删除数据库中所有「文件已丢失」的素材记录（不删除磁盘文件）")
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

    def _setup_content(self):
        # === 主体：左右排列（不使用可拖动分割条，空间不足时整体滚动）===
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        left = self._setup_left_panel()
        left.setMinimumWidth(300)
        right = self._setup_right_panel()
        right.setMinimumWidth(260)
        # 左右两侧按 1:1 分配宽度
        content_layout.addWidget(left, 1)
        content_layout.addWidget(right, 1)
        return content

    def _setup_left_panel(self):
        # --- 左侧：工作区/项目选择 + 素材列表 ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # 共享控件：工作区 + 项目两级选择（combo 模式，适合顶部）
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_new_project=True,
            buttons_below=True,
            db_service=self.db_service,
        )
        left_layout.addWidget(self.selector)

        # 素材计数标签
        self.asset_count_label = QLabel("")
        self.asset_count_label.setStyleSheet(f"font-size: {FONT_SIZE.SM}px; color: {COLOR.TEXT_SECONDARY};")
        left_layout.addWidget(self.asset_count_label)

        self.asset_table = make_table_view(
            sortable=True,
            resize_to_contents_cols=[1, 2, 3, 4],
            selection_mode=QAbstractItemView.ExtendedSelection,
        )
        self.asset_model = AssetTableModel(
            ["文件名", "类型", "大小", "EXIF", "状态"], self._asset_cell, self,
        )
        self.asset_table.setModel(self.asset_model)
        # 表格全局样式由 main.py 注入的 GLOBAL_QSS 统一控制，不再单独 setStyleSheet
        self.asset_table.selectionModel().selectionChanged.connect(self._on_asset_selected)
        # 双击打开所在目录
        self.asset_table.doubleClicked.connect(self._on_asset_double_clicked)
        self.asset_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.asset_table.customContextMenuRequested.connect(self._on_asset_context_menu)
        left_layout.addWidget(self.asset_table, 1)
        attach_empty_state(self.asset_table, "📦", "暂无素材", "请先在「媒体导入」中导入素材")

        self.asset_page_bar = QWidget()
        page_layout = QHBoxLayout(self.asset_page_bar)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.prev_asset_page_btn = QPushButton("上一页")
        self.prev_asset_page_btn.clicked.connect(self._previous_asset_page)
        self.asset_page_label = QLabel("")
        self.next_asset_page_btn = QPushButton("下一页")
        self.next_asset_page_btn.clicked.connect(self._next_asset_page)
        page_layout.addStretch()
        page_layout.addWidget(self.prev_asset_page_btn)
        page_layout.addWidget(self.asset_page_label)
        page_layout.addWidget(self.next_asset_page_btn)
        page_layout.addStretch()
        self.asset_page_bar.setVisible(False)
        left_layout.addWidget(self.asset_page_bar)

        # 批量操作栏：多选后可批量评级 / 批量删除
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
        self.batch_delete_btn = QPushButton("🗑 删除选中")
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
        self.batch_selected_label.setStyleSheet(f"font-size: {FONT_SIZE.SM}px; color: {COLOR.TEXT_SECONDARY};")
        batch_row.addWidget(self.batch_selected_label)
        batch_row.addStretch()
        left_layout.addLayout(batch_row)

        # 个人模式：隐藏批量评级按钮（评级为团队功能；删除操作保留）
        if not is_enabled("ratings"):
            self.batch_label.setVisible(False)
            for btn in self.batch_rating_buttons:
                btn.setVisible(False)

        refresh_btn = QPushButton("🔄 刷新列表")
        refresh_btn.clicked.connect(self._load_assets)
        left_layout.addWidget(refresh_btn)

        return left_widget

    def _setup_right_panel(self):
        # --- 右侧：属性详情面板 ---
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

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
        # 当前选中文件名标题
        self.current_file_label = QLabel("请选择左侧素材查看详情")
        self.current_file_label.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; "
            f"padding: 8px 0 12px 0;"
        )
        props_layout.addWidget(self.current_file_label)

        # 缩略图预览（异步生成，由 ThumbnailService.thumbnail_ready 回填）
        self.thumbnail_label = QLabel("🖼 选择素材后显示缩略图")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setMinimumHeight(180)
        self.thumbnail_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; border: 1px dashed {COLOR.BORDER}; "
            f"border-radius: {RADIUS.INPUT}px; padding: 12px;"
        )
        props_layout.addWidget(self.thumbnail_label)

        # 单个文件刷新按钮
        single_refresh_row = QHBoxLayout()
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
        single_refresh_row.addWidget(self.refresh_exif_btn)
        single_refresh_row.addStretch()
        props_layout.addLayout(single_refresh_row)

    def _setup_missing_banner(self, props_layout):
        """文件已丢失警告横幅（默认隐藏，选中「文件已丢失」素材时显示）。"""
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
        # 镜次评级快捷按钮（评级值与标签来自 Models.RATING_LABELS 单一事实源）
        rating_row = QHBoxLayout()
        self.rating_label = QLabel("⭐ 镜次评级")
        self.rating_label.setStyleSheet(f"font-size: {FONT_SIZE.BASE}px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; padding: 4px 0;")
        rating_row.addWidget(self.rating_label)
        rating_row.addStretch()

        self._rating_buttons = []
        rating_options = sorted(RATING_LABELS.items())
        for value, text in rating_options:
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

        # 个人模式：隐藏镜次评级行（评级数据字段保留，仅不提供操作入口）
        if not is_enabled("ratings"):
            self.rating_label.setVisible(False)
            for _value, btn in self._rating_buttons:
                btn.setVisible(False)

    def _setup_tags_group(self, props_layout):
        """标签与备注编辑分组（可保存到素材记录）"""
        group = QGroupBox("🏷 标签与备注")
        group.setStyleSheet(_GROUP_STYLE)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setContentsMargins(16, 8, 16, 12)
        form.setVerticalSpacing(8)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("多个标签用逗号分隔，如：日戏,主镜头,精修")
        self.tags_edit.setToolTip("自定义标签，可在「素材检索」中按标签筛选")
        self.tags_edit.setEnabled(False)
        self._refresh_tags_completer()
        form.addRow("标签:", self.tags_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("备注信息（可选）")
        self.notes_edit.setMaximumHeight(90)
        self.notes_edit.setEnabled(False)
        form.addRow("备注:", self.notes_edit)

        save_row = QHBoxLayout()
        self.save_tags_btn = QPushButton("💾 保存标签与备注")
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

    def _refresh_tags_completer(self):
        """用数据库已有标签为输入框提供自动补全。"""
        from PySide6.QtWidgets import QCompleter
        try:
            tags = self.db_service.get_all_tags()
        except Exception as e:
            logger.warning(f"加载标签补全失败: {e}")
            tags = []
        completer = QCompleter(tags, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.tags_edit.setCompleter(completer)

    def _setup_props_groups(self, props_layout):
        # 各分组（使用统一的 _create_group 方法）
        self._group_widgets = {}
        self._label_refs = {}

        # 分组 1：基本信息
        self._create_group(props_layout, "basic", "📄 基本信息", [
            ("file_name", "文件名"),
            ("file_path", "文件路径"),
            ("asset_type", "资产类型"),
            ("file_type", "文件类型"),
            ("file_size", "文件大小"),
            ("date_imported", "导入时间"),
            ("original_path", "原始路径"),
            ("is_working_copy", "工作副本"),
        ])

        # 分组 2：拍摄参数
        self._create_group(props_layout, "camera", "📷 拍摄参数 (EXIF)", [
            ("camera_make", "相机制造商"),
            ("camera_model", "相机型号"),
            ("lens_model", "镜头型号"),
            ("focal_length", "焦距"),
            ("date_taken", "拍摄时间"),
        ])

        # 分组 3：图像尺寸
        self._create_group(props_layout, "image", "🖼 图像尺寸", [
            ("dimensions", "尺寸"),
            ("width", "宽度"),
            ("height", "高度"),
        ])

        # 分组 4：视频信息
        self._create_group(props_layout, "video", "🎬 视频信息", [
            ("duration", "时长"),
            ("codec", "视频编码"),
            ("frame_rate", "帧率"),
            ("bit_rate", "比特率"),
            ("audio_codec", "音频编码"),
            ("audio_sample_rate", "音频采样率"),
        ])

        # 分组 5：项目关联
        self._create_group(props_layout, "project", "🔗 项目关联", [
            ("scene", "场景号"),
            ("shot", "镜头号"),
            ("take", "镜次"),
            ("log_id", "关联日志"),
            ("backup_locations", "备份位置"),
        ])

        # 分组 6：完整性校验
        self._create_group(props_layout, "checksum", "🔐 完整性校验", [
            ("checksum_algorithm", "校验算法"),
            ("checksum_value", "校验值"),
        ])

    def _create_group(self, parent_layout, group_key, title, fields):
        """创建一个信息分组"""
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
            # 字段名标签：左对齐 + 固定宽度，保证整齐
            field_label = QLabel(label_text)
            field_label.setFixedWidth(90)
            field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            field_label.setStyleSheet(
                f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px; font-weight: 500;"
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
        """设置某字段的值，自动切换样式（有值/缺失）"""
        lbl = self._label_refs[group_key][key]
        display = value if value else _PLACEHOLDER
        lbl.setText(display)
        lbl.setToolTip(raw if raw else display)
        if value:
            lbl.setStyleSheet(_STYLE_HAS_VALUE)
        else:
            lbl.setStyleSheet(_STYLE_MISSING)

    # ===== 数据加载 =====
    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self.selector.refresh()
        self._load_assets()

    def _on_project_changed(self, _project_id):
        """项目切换（由共享控件广播）→ 刷新素材列表"""
        self._load_assets()

    @safe_slot("导出素材清单失败")
    def _export_csv(self):
        """把当前项目的全部素材元数据导出为 CSV。"""
        project_id = self.selector.get_current_project_id()
        if not project_id or self._asset_total == 0:
            QMessageBox.information(self, "提示", "当前项目没有可导出的素材")
            return
        default_name = f"素材清单_{now_local().strftime('%Y%m%d_%H%M%S')}.csv"
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
            get_report_service().export_assets_csv_iter(
                self.db_service.iter_project_assets(project_id), path,
                total=self._asset_total, cancel_check=cancel_check,
                progress_callback=lambda current, total, message: progress_callback(
                    "export", current / total if total else 0.0, message,
                ),
            )
            return {"path": path, "count": self._asset_total}

        self._export_vm.start(
            export_task, inject_progress=True, inject_cancel_check=True,
            task_name="project_csv_export", project_id=project_id,
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

    def _load_assets(self, reset_page=True):
        project_id = self.selector.get_current_project_id()
        if self._asset_page_vm.is_running():
            # 项目切换或连续翻页时只保留最后一次请求；当前查询完成后立即执行。
            self._pending_asset_load = reset_page
            self._asset_load_generation += 1
            self._asset_page_vm.cancel()
            self.asset_count_label.setText("正在切换素材列表…")
            return
        if reset_page:
            self._missing_scan_generation += 1
            self._asset_page = 0
            self._asset_page_cursors = [None]
            self._asset_next_cursor = None
            self._asset_total = 0
        self._asset_load_generation += 1
        generation = self._asset_load_generation
        self.asset_model.clear()
        sync_empty_state(self.asset_table)
        self._update_batch_state()
        self.current_file_label.setText("请选择左侧素材查看详情")
        self.refresh_exif_btn.setEnabled(False)
        self._clear_properties()
        self.missing_banner.setVisible(False)
        self._missing_ids = set()
        self._missing_scan_pending = bool(project_id)
        if not project_id:
            self.batch_exif_btn.setEnabled(False)
            self._assets = []
            self._asset_total = 0
            self._refresh_missing_summary()
            return

        page_size = 500
        cursor = self._asset_page_cursors[self._asset_page]
        if reset_page:
            self._start_missing_file_scan(project_id)

        def load_page(cancel_check):
            if cancel_check():
                raise InterruptedError("素材列表加载已取消")
            total = self.db_service.count_project_assets(project_id) if reset_page else self._asset_total
            if cancel_check():
                raise InterruptedError("素材列表加载已取消")
            assets, next_cursor = self.db_service.get_project_asset_page(
                project_id, page_size=page_size, cursor=cursor,
            )
            return {
                "generation": generation, "project_id": project_id, "total": total,
                "assets": assets, "next_cursor": next_cursor,
            }

        self.asset_count_label.setText("正在加载素材列表…")
        self.prev_asset_page_btn.setEnabled(False)
        self.next_asset_page_btn.setEnabled(False)
        self._asset_page_vm.start(
            load_page, inject_cancel_check=True, task_name="project_asset_page", project_id=project_id,
        )

    def _on_asset_page_finished(self, result):
        if result.get("generation") != self._asset_load_generation:
            self._start_pending_asset_load()
            return
        if result.get("project_id") != self.selector.get_current_project_id():
            self._start_pending_asset_load()
            return
        self._asset_total = result["total"]
        self._assets = result["assets"]
        self._asset_next_cursor = result["next_cursor"]
        self.asset_model.set_assets(self._assets)
        # 缺失扫描可能先于分页查询结束；此时把已得到的扫描结论回填新页面。
        if not self._missing_scan_pending:
            for asset in self._assets:
                is_missing = asset.asset_id in self._missing_ids
                self.asset_model.set_status(
                    asset.asset_id, "⚠ 文件已丢失" if is_missing else "✓ 正常",
                    QColor(COLOR.DANGER) if is_missing else QColor(COLOR.SUCCESS),
                )
        self.batch_exif_btn.setEnabled(bool(self._assets))
        sync_empty_state(self.asset_table)
        self._refresh_missing_summary()
        self._start_pending_asset_load()

    def _on_asset_page_error(self, error: str):
        logger.warning(f"读取素材分页失败: {error}")
        self.asset_count_label.setText(f"素材列表加载失败：{error}")
        self._refresh_missing_summary()
        self._start_pending_asset_load()

    def _start_pending_asset_load(self):
        if self._pending_asset_load is None:
            return
        reset_page = self._pending_asset_load
        self._pending_asset_load = None
        self._load_assets(reset_page=reset_page)

    def _asset_cell(self, asset, column: int):
        if column == 0:
            return asset.file_name, None
        if column == 1:
            return asset.file_type, None
        if column == 2:
            return format_size(asset.file_size), None
        if column == 3:
            has_exif = bool(asset.camera_make or asset.camera_model or asset.lens_model)
            return ("✓ 有", QColor(COLOR.SUCCESS)) if has_exif else ("— 无", QColor(COLOR.TEXT_SECONDARY))
        default = ("⚠ 文件已丢失", QColor(COLOR.DANGER)) if asset.asset_id in self._missing_ids else (
            "检查中…", QColor(COLOR.TEXT_SECONDARY)
        )
        return self.asset_model.status_for(asset.asset_id, default)

    def _next_asset_page(self):
        if self._asset_next_cursor is None:
            return
        self._asset_page += 1
        if len(self._asset_page_cursors) <= self._asset_page:
            self._asset_page_cursors.append(self._asset_next_cursor)
        else:
            self._asset_page_cursors[self._asset_page] = self._asset_next_cursor
        self._load_assets(reset_page=False)

    def _previous_asset_page(self):
        if self._asset_page <= 0:
            return
        self._asset_page -= 1
        self._load_assets(reset_page=False)

    def _refresh_missing_summary(self):
        """依据 self._missing_ids 刷新素材计数文案与「清理丢失素材」按钮可用状态。"""
        missing_count = len(self._missing_ids)
        text = f"共 {self._asset_total} 个素材"
        if self._asset_total:
            start = self._asset_page * 500 + 1
            end = start + len(self._assets) - 1
            text += f"（当前显示 {start}-{end}）"
        if missing_count:
            text += f"（{missing_count} 个文件已丢失）"
        self.asset_count_label.setText(text)
        total_pages = max(1, (self._asset_total + 499) // 500)
        self.asset_page_bar.setVisible(total_pages > 1)
        self.asset_page_label.setText(f"第 {self._asset_page + 1} / {total_pages} 页")
        self.prev_asset_page_btn.setEnabled(self._asset_page > 0)
        self.next_asset_page_btn.setEnabled(self._asset_next_cursor is not None)
        # 仅当存在丢失素材时才允许一键清理，避免误触空操作
        self.cleanup_missing_btn.setEnabled(
            missing_count > 0 and not self._missing_scan_pending
        )
        self.relink_missing_btn.setEnabled(
            missing_count > 0 and not self._missing_scan_pending
        )

    def _start_missing_file_scan(self, project_id: str, for_cleanup: bool = False):
        """在后台扫描文件存在性；刷新序号用于丢弃过期扫描结果。"""
        generation = self._missing_scan_generation
        worker = WorkerThread(
            self.db_service.get_missing_file_asset_ids, project_id
        )
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
        """接收后台扫描结果并在主线程更新状态列。"""
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
                asset.asset_id, "⚠ 文件已丢失" if is_missing else "✓ 正常",
                QColor(COLOR.DANGER) if is_missing else QColor(COLOR.SUCCESS),
            )
        self._refresh_missing_summary()
        if self.current_asset:
            self._on_asset_selected()

        if getattr(worker, "_missing_for_cleanup", False):
            self._confirm_cleanup_missing(project_id)

    @Slot(str)
    def _on_missing_scan_error(self, error: str):
        """扫描失败时恢复按钮状态并保留明确的状态提示。"""
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
            self.asset_model.set_status(asset.asset_id, "⚠ 检查失败", QColor(COLOR.DANGER))
        logger.warning(f"文件存在性扫描失败: {error}")

    def _on_asset_selected(self):
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
            return
        row = index.row()
        self.current_asset = self.asset_model.asset_at(row)
        asset_id = self.current_asset.asset_id if self.current_asset else None
        if self.current_asset:
            # 使用最近一次后台扫描结果，避免在 UI 线程访问磁盘。
            is_missing = asset_id in self._missing_ids
            self._set_asset_missing(row, asset_id, is_missing)
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

    # ===== 批量操作 =====

    def _selected_asset_ids(self) -> list:
        """返回当前多选行对应的 asset_id 列表。"""
        ids = []
        for index in self.asset_table.selectionModel().selectedRows():
            asset = self.asset_model.asset_at(index.row())
            if asset is not None:
                ids.append(asset.asset_id)
        return ids

    def _update_batch_state(self):
        """刷新批量操作按钮可用状态与已选计数。"""
        count = len(self._selected_asset_ids())
        enabled = count > 0
        for btn in self.batch_rating_buttons:
            btn.setEnabled(enabled)
        self.batch_delete_btn.setEnabled(enabled)
        self.batch_selected_label.setText(f"已选 {count} 个" if count else "")

    def _set_asset_missing(self, row: int, asset_id: str, is_missing: bool):
        """同步某条素材的「文件已丢失」状态到内存集合、表格状态列与汇总文案。

        选中素材时根据最近一次后台扫描结果同步列表与详情面板状态。
        """
        if is_missing:
            self._missing_ids.add(asset_id)
        else:
            self._missing_ids.discard(asset_id)
        self.asset_model.set_status(
            asset_id, "⚠ 文件已丢失" if is_missing else "✓ 正常",
            QColor(COLOR.DANGER) if is_missing else QColor(COLOR.SUCCESS),
        )
        self._refresh_missing_summary()

    @safe_slot("批量评级失败")
    def _batch_set_rating(self, rating: int):
        """对选中素材批量设置评级。"""
        ids = self._selected_asset_ids()
        if not ids:
            return
        ok = 0
        for aid in ids:
            if self.db_service.update_media_asset(aid, rating=rating):
                ok += 1
        self._load_assets()
        QMessageBox.information(
            self, "批量评级", f"已为 {ok}/{len(ids)} 个素材设置评级"
        )
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

    @safe_slot("批量删除失败")
    def _batch_delete(self):
        """从项目移除选中的素材记录（不删除磁盘文件）。"""
        ids = self._selected_asset_ids()
        if not ids:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定从项目移除 {len(ids)} 条素材记录？\n\n"
            "仅移除数据库记录，不会删除磁盘上的源文件。\n"
            "记录将在回收站保留 30 天，可恢复其元数据。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        ok = self.db_service.delete_media_assets(ids)
        self._load_assets()
        QMessageBox.information(
            self, "删除完成", f"已移除 {ok}/{len(ids)} 条素材记录"
        )
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

    @safe_slot("清理丢失素材失败")
    def _batch_cleanup_missing(self):
        """一键删除当前项目所有「文件已丢失」的素材记录（二次确认防误删）。

        仅移除数据库中的失效路径记录，保持数据库与实际文件系统一致，
        绝不删除磁盘上的任何文件；删除前弹出二次确认，避免误删有效数据。
        """
        project_id = self.selector.get_current_project_id()
        if not project_id:
            return
        if self._missing_scan_pending:
            QMessageBox.information(self, "正在检查", "正在检查文件状态，请稍后再试。")
            return

        # 删除前重新扫描，但扫描在后台执行，避免阻塞界面。
        self._missing_scan_pending = True
        self._refresh_missing_summary()
        self._start_missing_file_scan(project_id, for_cleanup=True)

    def _confirm_cleanup_missing(self, project_id: str):
        """在后台复查完成后确认并执行丢失素材清理。"""
        if self.selector.get_current_project_id() != project_id:
            return
        missing_ids = sorted(self._missing_ids)
        if not missing_ids:
            QMessageBox.information(self, "无丢失素材", "当前项目没有文件已丢失的素材记录。")
            return

        # 二次确认：明确仅删除失效记录、不影响正常素材、操作不可撤销
        reply = QMessageBox.question(
            self, "确认清理丢失素材",
            f"即将删除 {len(missing_ids)} 条「文件已丢失」的素材记录。\n\n"
            "• 仅移除数据库中的素材记录，不会删除磁盘上的任何文件\n"
            "• 仅影响文件已丢失的无效记录，正常素材不会被改动\n"
            "• 记录将在回收站保留 30 天，可恢复其元数据\n\n"
            "确定要清理吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        deleted = self.db_service.delete_media_assets(missing_ids)
        self._load_assets()
        QMessageBox.information(
            self, "清理完成", f"已删除 {deleted}/{len(missing_ids)} 条丢失素材记录。"
        )
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

    @safe_slot("重新链接丢失素材失败")
    def _relink_missing_assets(self):
        """让用户选择新根目录，先展示预览摘要再提交唯一匹配。"""
        project_id = self.selector.get_current_project_id()
        if not project_id:
            return
        root = pick_directory(self, "选择素材的新根目录", category="relink_root")
        if not root:
            return
        service = get_asset_relink_service()
        preview = service.preview(project_id, root)
        if preview.matched_count == 0:
            QMessageBox.information(
                self, "未找到可重新链接的素材",
                f"未匹配 {preview.unmatched_count} 个素材；存在冲突 {preview.conflict_count} 个。",
            )
            return
        selections = self._show_relink_preview(preview)
        if selections is None:
            return
        result = service.apply(preview, selections)
        if not result:
            QMessageBox.warning(self, "重新链接未完成", result.message)
            return
        self._load_assets()
        QMessageBox.information(self, "重新链接完成", f"已重新链接 {result.affected_count} 个素材。")
        get_data_bus().emit_data_changed("assets_changed")

    def _show_relink_preview(self, preview):
        """展示重新链接预览，并让用户对重名冲突选择候选文件。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("预览重新链接")
        dialog.resize(920, 520)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"唯一匹配 {preview.matched_count} 个，冲突 {preview.conflict_count} 个，"
            f"未匹配 {preview.unmatched_count} 个。选择后将批量回写数据库路径。"
        ))
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
            status = "冲突" if match.is_conflict else "可链接" if match.selected_path else "未匹配"
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
            for asset_id, combo in selections.items() if combo.currentData()
        ]

    def _on_asset_double_clicked(self, index):
        """双击素材 → 打开所在目录（通过 item 的 UserRole 取 asset_id，排序安全）"""
        if not index.isValid():
            return
        asset = self.asset_model.asset_at(index.row())
        if asset is not None:
            self._open_asset_directory(asset)

    def _on_asset_context_menu(self, pos):
        """素材表右键菜单：打开所在目录 / 复制路径"""
        from PySide6.QtWidgets import QMenu
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
        """在文件管理器中打开素材所在目录"""
        import os
        path = asset.file_path
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "路径不存在", f"文件不存在：\n{path}")
            return
        if not open_in_file_manager(path):
            QMessageBox.warning(self, "打开失败", "无法打开文件管理器，请检查路径权限。")

    def _copy_asset_path(self, asset):
        """复制文件路径到剪贴板"""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(asset.file_path or "")
        logger.info(f"已复制路径到剪贴板: {asset.file_path}")

    def _sync_rating_buttons(self, rating):
        """同步评级按钮的选中/启用状态

        Args:
            rating: None 表示无选中素材（禁用所有按钮）；0-3 表示当前评级值
        """
        for value, btn in self._rating_buttons:
            if rating is None:
                btn.setEnabled(False)
                btn.setChecked(False)
            else:
                btn.setEnabled(True)
                btn.setChecked(value == rating)

    @safe_slot("设置评级失败")
    def _set_rating(self, rating: int):
        """保存评级到数据库并刷新当前素材"""
        if not self.current_asset:
            return
        ok = self.db_service.update_media_asset(
            self.current_asset.asset_id, rating=rating
        )
        if not ok:
            return
        # 重新加载素材以同步内存状态
        self.current_asset = self.db_service.get_media_asset(
            self.current_asset.asset_id
        )
        self._sync_rating_buttons(rating)
        try:
            self.db_service.record_operation(
                "设置评级", f"{self.current_asset.file_name} -> {RATING_LABELS.get(rating, rating)}",
                project_id=self.current_asset.project_id,
            )
        except Exception as e:
            logger.warning(f"记录评级操作日志失败: {e}")
        # 广播素材变更，让看板/检索等视图刷新
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

    # ===== 属性展示 =====
    def _request_thumbnail(self, asset):
        """请求生成/读取素材缩略图（缓存命中同步返回，未命中异步回填）。"""
        self.thumbnail_label.setPixmap(QPixmap())
        self.thumbnail_label.setText("生成缩略图中…")
        pix = self.thumbnail_service.get_thumbnail(
            asset.asset_id, asset.file_path, asset.asset_type, SIZE_LARGE
        )
        if pix is not None:
            self._apply_thumbnail(asset.asset_id, pix)

    @Slot(str, QPixmap)
    def _on_thumbnail_ready(self, cache_key, pixmap):
        """异步缩略图生成完成：仅当与当前选中素材一致时回填。"""
        if self.current_asset and self.current_asset.asset_id == cache_key:
            self._apply_thumbnail(cache_key, pixmap)

    def _apply_thumbnail(self, cache_key, pixmap):
        self.thumbnail_label.setText("")
        self.thumbnail_label.setPixmap(pixmap.scaled(
            320, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def _display_properties(self, asset):
        # 基本信息
        self._set_value("basic", "file_name", asset.file_name)
        self._set_value("basic", "file_path", asset.file_path)
        self._set_value("basic", "asset_type", asset.asset_type)
        self._set_value("basic", "file_type", asset.file_type)
        self._set_value("basic", "file_size",
                        f"{format_size(asset.file_size)}（{asset.file_size:,} 字节）")
        self._set_value("basic", "date_imported",
                        asset.date_imported.strftime("%Y-%m-%d %H:%M:%S") if asset.date_imported else "")
        self._set_value("basic", "original_path", asset.original_path)
        self._set_value("basic", "is_working_copy", "是" if asset.is_working_copy else "否")

        # 拍摄参数
        self._set_value("camera", "camera_make", asset.camera_make)
        self._set_value("camera", "camera_model", asset.camera_model)
        self._set_value("camera", "lens_model", asset.lens_model)
        self._set_value("camera", "focal_length", asset.focal_length)
        self._set_value("camera", "date_taken",
                        asset.date_taken.strftime("%Y-%m-%d %H:%M:%S") if asset.date_taken else "")

        # 图像尺寸
        if asset.width and asset.height:
            self._set_value("image", "dimensions", f"{asset.width} × {asset.height} px")
        else:
            self._set_value("image", "dimensions", "")
        self._set_value("image", "width", f"{asset.width} px" if asset.width else "")
        self._set_value("image", "height", f"{asset.height} px" if asset.height else "")

        # 视频信息
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
        self._set_value("video", "frame_rate",
                        f"{video_info['frame_rate']:.1f} fps" if video_info.get("frame_rate") else "")
        self._set_value("video", "bit_rate",
                        f"{video_info['bit_rate']:,} bps" if video_info.get("bit_rate") else "")
        self._set_value("video", "audio_codec", video_info.get("audio_codec", ""))
        self._set_value("video", "audio_sample_rate",
                        f"{video_info['audio_sample_rate']:,} Hz" if video_info.get("audio_sample_rate") else "")

        # 项目关联
        self._set_value("project", "scene", asset.scene)
        self._set_value("project", "shot", asset.shot)
        self._set_value("project", "take", asset.take)
        # 关联日志显示标签而非裸 UUID
        log_label = ""
        if asset.log_id:
            log = self.db_service.get_shooting_log(asset.log_id)
            if log:
                log_label = f"{log.scene}/{log.shot}/{log.take}"
                if log.description:
                    log_label += f" - {log.description}"
            else:
                # 日志可能已被删除
                log_label = "（日志已删除）"
        self._set_value("project", "log_id", log_label, raw=asset.log_id or "")
        backup_locs = asset.backup_locations if asset.backup_locations else []
        self._set_value("project", "backup_locations",
                        "\n".join(backup_locs) if backup_locs else "")

        # 校验信息
        self._set_value("checksum", "checksum_algorithm", asset.checksum_algorithm)
        self._set_value("checksum", "checksum_value", asset.checksum_value)

        # 标签与备注（可编辑）
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

    @safe_slot("保存标签备注失败")
    def _save_tags_notes(self):
        """保存当前素材的标签与备注。"""
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
        self.current_asset = self.db_service.get_media_asset(self.current_asset.asset_id)
        logger.info(f"已保存素材标签/备注: {self.current_asset.asset_id}")
        try:
            self.db_service.record_operation(
                "更新标签/备注", self.current_asset.file_name,
                project_id=self.current_asset.project_id,
            )
        except Exception as e:
            logger.warning(f"记录标签操作日志失败: {e}")
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

    # ===== 单个文件重新读取 EXIF =====
    @safe_slot("读取元数据失败")
    def _refresh_single_exif(self):
        if not self.current_asset:
            return
        file_path = self.current_asset.file_path
        if not file_path or not Path(file_path).exists():
            QMessageBox.warning(self, "提示", "文件不存在，无法读取元数据信息。")
            return

        update_fields = {}

        # 视频文件读取视频元数据
        if self.current_asset.asset_type == "video":
            vm = self.metadata_service.read_video_metadata(file_path)
            if vm.width:
                update_fields["width"] = vm.width
            if vm.height:
                update_fields["height"] = vm.height
            if vm.duration_seconds:
                update_fields["duration_seconds"] = vm.duration_seconds
            update_fields["video_metadata"] = json.dumps({
                "codec": vm.codec,
                "frame_rate": vm.frame_rate,
                "bit_rate": vm.bit_rate,
                "audio_codec": vm.audio_codec,
                "audio_sample_rate": vm.audio_sample_rate,
            })
        else:
            # 图片/RAW 读取 EXIF
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
            self._display_properties(self.current_asset)
            self._load_assets()
            QMessageBox.information(
                self, "成功",
                f"已更新 {len(update_fields)} 项元数据。"
            )
        else:
            QMessageBox.information(
                self, "提示",
                "未读取到元数据信息（该文件可能不含元数据）。"
            )

    # ===== 一键批量重新读取全部 EXIF =====
    @safe_slot("批量读取 EXIF 失败")
    def _batch_refresh_exif(self):
        # 防止重复启动孤儿线程
        if self._batch_worker is not None and self._batch_worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行批量读取，请等待当前任务完成。")
            return

        project_id = self.selector.get_current_project_id()
        if not project_id:
            return

        total = self.db_service.count_project_assets(project_id)
        if not total:
            QMessageBox.information(self, "提示", "当前项目没有素材。")
            return

        reply = QMessageBox.question(
            self, "确认",
            f"将对项目下 {total} 个素材重新读取 EXIF 信息，是否继续？"
        )
        if reply != QMessageBox.Yes:
            return

        # 禁用按钮，显示进度条
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
        # QThread.finished 在线程真正结束时发射，用于自动释放对象避免泄漏
        self._batch_worker.thread_finished.connect(self._batch_worker.deleteLater)
        self._batch_worker.start()

    def _on_batch_progress(self, current, total, message):
        self.batch_progress.setValue(current)
        self.batch_status_label.setText(f"({current}/{total}) {message}")

    def _on_batch_error(self, error_msg: str):
        """批量任务异常时恢复 UI 状态"""
        self.batch_status_label.setText(f"❌ 出错：{error_msg}")
        self.batch_exif_btn.setEnabled(True)
        if self.current_asset:
            self.refresh_exif_btn.setEnabled(True)
        # worker 已连 deleteLater，这里清空引用避免悬挂
        self._batch_worker = None
        from PySide6.QtCore import QTimer
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
        # worker 已连 deleteLater，这里清空引用避免悬挂
        self._batch_worker = None

        # 刷新列表和当前详情
        self._load_assets()
        if self.current_asset:
            self.current_asset = self.db_service.get_media_asset(
                self.current_asset.asset_id
            )
            if self.current_asset:
                self.current_file_label.setText(self.current_asset.file_name)
                self._display_properties(self.current_asset)

        # 广播 assets_changed，让 search_view / shooting_log_view 等知道 EXIF 已更新
        if success > 0:
            try:
                get_data_bus().emit_data_changed("assets_changed")
            except Exception as e:
                logger.warning(f"批量 EXIF 完成后广播 assets_changed 失败: {e}")

        # 3 秒后隐藏进度条
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self.batch_progress_frame.setVisible(False))
