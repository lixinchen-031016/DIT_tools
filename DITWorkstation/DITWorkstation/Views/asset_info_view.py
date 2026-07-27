"""素材资产 EXIF 信息查看页面"""
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QGroupBox, QFormLayout, QScrollArea,
    QMessageBox, QAbstractItemView, QProgressBar, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal

from DITWorkstation.Services.rename_service import MetadataService
from DITWorkstation.Utils import format_size, get_db_service, safe_slot

# 缺失值占位符
_PLACEHOLDER = "—"

# 值标签样式：有值时深色加粗，缺失时灰色
_STYLE_HAS_VALUE = "color: #1d1d1f; font-size: 13px; font-weight: 500;"
_STYLE_MISSING = "color: #c7c7cc; font-size: 13px; font-style: italic;"

# 分组标题样式
_GROUP_STYLE = """
    QGroupBox {
        font-size: 14px;
        font-weight: 600;
        color: #1d1d1f;
        border: 1px solid #e5e5ea;
        border-radius: 10px;
        margin-top: 12px;
        padding-top: 16px;
        background-color: #fafafa;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 16px;
        padding: 0 6px;
    }
"""

# 表格样式
_TABLE_STYLE = """
    QTableWidget {
        border: 1px solid #e5e5ea;
        border-radius: 8px;
        background-color: #ffffff;
        gridline-color: #f0f0f0;
        font-size: 13px;
    }
    QTableWidget::item {
        padding: 6px 8px;
    }
    QTableWidget::item:selected {
        background-color: #0a84ff;
        color: #ffffff;
    }
    QHeaderView::section {
        background-color: #f5f5f7;
        padding: 6px 8px;
        border: none;
        border-bottom: 1px solid #e5e5ea;
        font-weight: 600;
        font-size: 12px;
        color: #86868b;
    }
"""


class _BatchExifWorker(QThread):
    """批量重新读取 EXIF 的后台线程"""

    progress = Signal(int, int, str)  # current, total, message
    finished_batch = Signal(int, int)  # success_count, total_count
    error = Signal(str)  # 错误信息（确保异常时 UI 能恢复）

    def __init__(self, db_service, asset_ids):
        super().__init__()
        self._db_service = db_service
        self._asset_ids = asset_ids
        self._metadata_service = MetadataService()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self._asset_ids)
        success = 0
        try:
            for i, asset_id in enumerate(self._asset_ids):
                if self._cancelled:
                    break
                try:
                    asset = self._db_service.get_media_asset(asset_id)
                    if not asset:
                        self.progress.emit(i + 1, total, f"跳过：{asset_id}（未找到）")
                        continue

                    file_path = asset.file_path
                    if not file_path or not Path(file_path).exists():
                        self.progress.emit(i + 1, total, f"跳过：{asset.file_name}（文件不存在）")
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
                        ok = self._db_service.update_media_asset(asset_id, **update_fields)
                        if ok:
                            success += 1
                            self.progress.emit(i + 1, total, f"已更新：{asset.file_name}")
                        else:
                            self.progress.emit(i + 1, total, f"更新失败：{asset.file_name}")
                    else:
                        self.progress.emit(i + 1, total, f"无元数据：{asset.file_name}")
                except Exception as e:
                    # 单个文件异常不中断整体流程
                    self.progress.emit(i + 1, total, f"出错：{e}")

            self.finished_batch.emit(success, total)
        except Exception as e:
            self.error.emit(str(e))


class AssetInfoView(QWidget):
    """素材资产信息视图 — 查看导入素材的 EXIF 与元数据详情"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self.metadata_service = MetadataService()
        self.current_asset = None
        self._batch_worker = None
        self._setup_ui()
        self._load_projects()
        # 监听全局项目切换，同步本视图下拉
        from DITWorkstation.Views.main_window import get_data_bus
        get_data_bus().project_focus_changed.connect(self._on_global_project_changed)
        get_data_bus().workspace_focus_changed.connect(self._on_global_workspace_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # === 标题区 ===
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("素材信息")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        title_col.addWidget(title)
        subtitle = QLabel("查看导入素材的 EXIF 拍摄信息与文件元数据详情")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        # 一键重新读取 EXIF 按钮
        self.batch_exif_btn = QPushButton("🔄 一键重新读取全部 EXIF")
        self.batch_exif_btn.setStyleSheet("""
            QPushButton {
                background-color: #0a84ff;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #0071e3; }
            QPushButton:disabled { background-color: #c7c7cc; }
        """)
        self.batch_exif_btn.clicked.connect(self._batch_refresh_exif)
        self.batch_exif_btn.setEnabled(False)
        header.addWidget(self.batch_exif_btn)

        layout.addLayout(header)

        # === 主体：左右分栏 ===
        splitter = QSplitter(Qt.Horizontal)

        # --- 左侧：项目选择 + 素材列表 ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        proj_row = QHBoxLayout()
        proj_label = QLabel("项目")
        proj_label.setStyleSheet("font-weight: 600; color: #1d1d1f;")
        proj_row.addWidget(proj_label)
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(200)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        proj_row.addWidget(self.project_combo, 1)
        left_layout.addLayout(proj_row)

        # 素材计数标签
        self.asset_count_label = QLabel("")
        self.asset_count_label.setStyleSheet("font-size: 12px; color: #86868b;")
        left_layout.addWidget(self.asset_count_label)

        self.asset_table = QTableWidget()
        self.asset_table.setColumnCount(4)
        self.asset_table.setHorizontalHeaderLabels(["文件名", "类型", "大小", "EXIF"])
        self.asset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.asset_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.asset_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.asset_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.asset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.asset_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.asset_table.setAlternatingRowColors(True)
        self.asset_table.setStyleSheet(_TABLE_STYLE)
        self.asset_table.itemSelectionChanged.connect(self._on_asset_selected)
        left_layout.addWidget(self.asset_table, 1)

        refresh_btn = QPushButton("🔄 刷新列表")
        refresh_btn.clicked.connect(self._load_assets)
        left_layout.addWidget(refresh_btn)

        splitter.addWidget(left_widget)

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

        # 当前选中文件名标题
        self.current_file_label = QLabel("请选择左侧素材查看详情")
        self.current_file_label.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #1d1d1f; "
            "padding: 8px 0 12px 0;"
        )
        props_layout.addWidget(self.current_file_label)

        # 单个文件刷新按钮
        single_refresh_row = QHBoxLayout()
        self.refresh_exif_btn = QPushButton("重新读取当前文件 EXIF")
        self.refresh_exif_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #d1d1d6;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                color: #1d1d1f;
            }
            QPushButton:hover { background-color: #e5e5ea; }
            QPushButton:disabled { color: #c7c7cc; border-color: #e5e5ea; }
        """)
        self.refresh_exif_btn.clicked.connect(self._refresh_single_exif)
        self.refresh_exif_btn.setEnabled(False)
        single_refresh_row.addWidget(self.refresh_exif_btn)
        single_refresh_row.addStretch()
        props_layout.addLayout(single_refresh_row)

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

        props_layout.addStretch()
        right_scroll.setWidget(props_widget)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        # === 底部批量操作进度条 ===
        self.batch_progress_frame = QFrame()
        self.batch_progress_frame.setStyleSheet("background: transparent;")
        batch_layout = QVBoxLayout(self.batch_progress_frame)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.setSpacing(4)

        self.batch_status_label = QLabel("")
        self.batch_status_label.setStyleSheet("font-size: 12px; color: #86868b;")
        batch_layout.addWidget(self.batch_status_label)

        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        batch_layout.addWidget(self.batch_progress)
        self.batch_progress_frame.setVisible(False)
        layout.addWidget(self.batch_progress_frame)

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
                "color: #86868b; font-size: 12px; font-weight: 500;"
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
    def _load_projects(self):
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("请选择项目...", None)
        from DITWorkstation.Views.main_window import get_current_workspace_id
        ws_id = get_current_workspace_id()
        projects = self.db_service.get_projects(workspace_id=ws_id)
        for p in projects:
            self.project_combo.addItem(p.name, p.project_id)
        # 同步全局当前项目（切视图无需重选）
        from DITWorkstation.Views.main_window import get_current_project_id
        global_pid = get_current_project_id()
        if global_pid:
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == global_pid:
                    self.project_combo.setCurrentIndex(i)
                    break
        self.project_combo.blockSignals(False)
        # blockSignals 期间未触发，手动刷新一次素材列表
        self._on_project_changed(self.project_combo.currentIndex())

    def showEvent(self, event):
        self._load_projects()
        super().showEvent(event)

    def _on_project_changed(self, index: int):
        # 同步全局当前项目
        from DITWorkstation.Views.main_window import set_current_project
        set_current_project(self.project_combo.currentData())
        self._load_assets()

    def _on_global_project_changed(self, project_id):
        """全局项目切换，同步本视图下拉（避免回调循环）"""
        self.project_combo.blockSignals(True)
        if project_id is None:
            self.project_combo.setCurrentIndex(0)
        else:
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == project_id:
                    self.project_combo.setCurrentIndex(i)
                    break
        self.project_combo.blockSignals(False)
        # 手动触发一次本视图的 _on_project_changed 逻辑（刷新素材列表）
        self._on_project_changed(self.project_combo.currentIndex())

    def _on_global_workspace_changed(self, _workspace_id):
        """全局工作区切换 -> 重新加载项目列表（按新工作区过滤）"""
        self._load_projects()

    def _load_assets(self):
        project_id = self.project_combo.currentData()
        self.asset_table.setRowCount(0)
        self.current_file_label.setText("请选择左侧素材查看详情")
        self.refresh_exif_btn.setEnabled(False)
        self._clear_properties()
        if not project_id:
            self.batch_exif_btn.setEnabled(False)
            self.asset_count_label.setText("")
            return

        assets = self.db_service.get_media_assets(project_id)
        self.asset_table.setRowCount(len(assets))
        self.asset_count_label.setText(f"共 {len(assets)} 个素材")
        self.batch_exif_btn.setEnabled(len(assets) > 0)

        for i, asset in enumerate(assets):
            name_item = QTableWidgetItem(asset.file_name)
            name_item.setData(Qt.UserRole, asset.asset_id)
            self.asset_table.setItem(i, 0, name_item)
            self.asset_table.setItem(i, 1, QTableWidgetItem(asset.file_type))
            self.asset_table.setItem(i, 2, QTableWidgetItem(format_size(asset.file_size)))
            # EXIF 状态标识
            has_exif = bool(asset.camera_make or asset.camera_model or asset.lens_model)
            exif_item = QTableWidgetItem("✓ 有" if has_exif else "— 无")
            if has_exif:
                exif_item.setForeground(Qt.darkGreen)
            else:
                exif_item.setForeground(Qt.gray)
            self.asset_table.setItem(i, 3, exif_item)

    def _on_asset_selected(self):
        row = self.asset_table.currentRow()
        if row < 0:
            self.current_asset = None
            self.refresh_exif_btn.setEnabled(False)
            self.current_file_label.setText("请选择左侧素材查看详情")
            self._clear_properties()
            return
        asset_id = self.asset_table.item(row, 0).data(Qt.UserRole)
        self.current_asset = self.db_service.get_media_asset(asset_id)
        if self.current_asset:
            self.refresh_exif_btn.setEnabled(True)
            self.current_file_label.setText(self.current_asset.file_name)
            self._display_properties(self.current_asset)

    # ===== 属性展示 =====
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
            except (json.JSONDecodeError, TypeError):
                pass
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

    def _clear_properties(self):
        for group_labels in self._label_refs.values():
            for lbl in group_labels.values():
                lbl.setText(_PLACEHOLDER)
                lbl.setStyleSheet(_STYLE_MISSING)
                lbl.setToolTip("")

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

        project_id = self.project_combo.currentData()
        if not project_id:
            return

        assets = self.db_service.get_media_assets(project_id)
        if not assets:
            QMessageBox.information(self, "提示", "当前项目没有素材。")
            return

        reply = QMessageBox.question(
            self, "确认",
            f"将对项目下 {len(assets)} 个素材重新读取 EXIF 信息，是否继续？"
        )
        if reply != QMessageBox.Yes:
            return

        # 禁用按钮，显示进度条
        self.batch_exif_btn.setEnabled(False)
        self.refresh_exif_btn.setEnabled(False)
        self.batch_progress_frame.setVisible(True)
        self.batch_progress.setVisible(True)
        self.batch_progress.setRange(0, len(assets))
        self.batch_progress.setValue(0)
        self.batch_status_label.setText("正在读取 EXIF 信息...")

        asset_ids = [a.asset_id for a in assets]
        self._batch_worker = _BatchExifWorker(self.db_service, asset_ids)
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.finished_batch.connect(self._on_batch_finished)
        self._batch_worker.error.connect(self._on_batch_error)
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
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self.batch_progress_frame.setVisible(False))
        QMessageBox.critical(self, "批量读取出错", error_msg)

    def _on_batch_finished(self, success, total):
        self.batch_status_label.setText(
            f"完成：{success}/{total} 个素材成功更新 EXIF 信息"
        )
        self.batch_exif_btn.setEnabled(True)
        if self.current_asset:
            self.refresh_exif_btn.setEnabled(True)

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
                from DITWorkstation.Views.main_window import get_data_bus
                get_data_bus().emit_data_changed("assets_changed")
            except Exception:
                pass

        # 3 秒后隐藏进度条
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self.batch_progress_frame.setVisible(False))
