"""素材资产 EXIF 信息查看页面"""
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QGroupBox, QFormLayout, QScrollArea,
    QMessageBox, QAbstractItemView, QProgressBar, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal

from DITWorkstation.Services.metadata_service import MetadataService
from DITWorkstation.Utils import format_size, get_db_service, safe_slot, logger, open_in_file_manager
from DITWorkstation.Views.Widgets import RefreshOnShowView
from DITWorkstation.Views.Widgets.empty_state import attach_empty_state, sync_empty_state

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


class AssetInfoView(RefreshOnShowView):
    """素材资产信息视图 — 查看导入素材的 EXIF 与元数据详情"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self.metadata_service = MetadataService()
        self.current_asset = None
        self._batch_worker = None
        self._assets = []
        self._setup_ui()
        # 项目切换由共享控件广播到全局，本视图仅需监听后刷新素材列表
        self.selector.project_changed.connect(self._on_project_changed)

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

        # --- 左侧：工作区/项目选择 + 素材列表 ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # 共享控件：工作区 + 项目两级选择（combo 模式，适合顶部）
        from DITWorkstation.Views.Widgets import WorkspaceProjectSelector
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_new_project=True,
            db_service=self.db_service,
        )
        left_layout.addWidget(self.selector)

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
        self.asset_table.verticalHeader().setDefaultSectionSize(32)
        self.asset_table.setSortingEnabled(True)
        # 表格全局样式由 main.py 注入的 GLOBAL_QSS 统一控制，不再单独 setStyleSheet
        self.asset_table.itemSelectionChanged.connect(self._on_asset_selected)
        # 双击打开所在目录
        self.asset_table.doubleClicked.connect(self._on_asset_double_clicked)
        self.asset_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.asset_table.customContextMenuRequested.connect(self._on_asset_context_menu)
        left_layout.addWidget(self.asset_table, 1)
        attach_empty_state(self.asset_table, "📦", "暂无素材", "请先在「媒体导入」中导入素材")

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

        # 镜次评级快捷按钮（评级值与标签来自 Models.RATING_LABELS 单一事实源）
        rating_row = QHBoxLayout()
        rating_label = QLabel("⭐ 镜次评级")
        rating_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #1d1d1f; padding: 4px 0;")
        rating_row.addWidget(rating_label)
        rating_row.addStretch()

        self._rating_buttons = []
        from DITWorkstation.Models import RATING_LABELS
        rating_options = sorted(RATING_LABELS.items())
        for value, text in rating_options:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setEnabled(False)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #f0f0f0;
                    border: 1px solid #d1d1d6;
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 12px;
                    color: #1d1d1f;
                }
                QPushButton:hover { background-color: #e5e5ea; }
                QPushButton:checked {
                    background-color: #0a84ff;
                    color: white;
                    border-color: #0a84ff;
                }
                QPushButton:disabled { color: #c7c7cc; border-color: #e5e5ea; }
            """)
            btn.clicked.connect(lambda checked, v=value: self._set_rating(v))
            self._rating_buttons.append((value, btn))
            rating_row.addWidget(btn)
        props_layout.addLayout(rating_row)

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
    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self.selector.refresh()
        self._load_assets()

    def _on_project_changed(self, _project_id):
        """项目切换（由共享控件广播）→ 刷新素材列表"""
        self._load_assets()

    def _load_assets(self):
        project_id = self.selector.get_current_project_id()
        self.asset_table.setRowCount(0)
        sync_empty_state(self.asset_table)
        self.current_file_label.setText("请选择左侧素材查看详情")
        self.refresh_exif_btn.setEnabled(False)
        self._clear_properties()
        if not project_id:
            self.batch_exif_btn.setEnabled(False)
            self.asset_count_label.setText("")
            return

        assets = self.db_service.get_media_assets(project_id)
        self._assets = assets
        self.asset_table.setRowCount(len(assets))
        sync_empty_state(self.asset_table)
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
            self._sync_rating_buttons(None)
            self.current_file_label.setText("请选择左侧素材查看详情")
            self._clear_properties()
            return
        asset_id = self.asset_table.item(row, 0).data(Qt.UserRole)
        self.current_asset = self.db_service.get_media_asset(asset_id)
        if self.current_asset:
            self.refresh_exif_btn.setEnabled(True)
            self._sync_rating_buttons(self.current_asset.rating)
            self.current_file_label.setText(self.current_asset.file_name)
            self._display_properties(self.current_asset)

    def _on_asset_double_clicked(self, index):
        """双击素材 → 打开所在目录（通过 item 的 UserRole 取 asset_id，排序安全）"""
        if not index.isValid():
            return
        name_item = self.asset_table.item(index.row(), 0)
        if name_item is None:
            return
        asset_id = name_item.data(Qt.UserRole)
        if not asset_id:
            return
        asset = self.db_service.get_media_asset(asset_id)
        if asset is not None:
            self._open_asset_directory(asset)

    def _on_asset_context_menu(self, pos):
        """素材表右键菜单：打开所在目录 / 复制路径"""
        from PySide6.QtWidgets import QMenu
        item = self.asset_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        name_item = self.asset_table.item(row, 0)
        asset_id = name_item.data(Qt.UserRole) if name_item else None
        asset = self.db_service.get_media_asset(asset_id) if asset_id else None
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
        # 广播素材变更，让看板/检索等视图刷新
        try:
            from DITWorkstation.App.session_context import get_data_bus
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播 assets_changed 失败: {e}")

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

        project_id = self.selector.get_current_project_id()
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
        # QThread.finished 在线程真正结束时发射，用于自动释放对象避免泄漏
        self._batch_worker.finished.connect(self._batch_worker.deleteLater)
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
        QMessageBox.critical(self, "批量读取出错", error_msg)

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
                from DITWorkstation.App.session_context import get_data_bus
                get_data_bus().emit_data_changed("assets_changed")
            except Exception as e:
                logger.warning(f"批量 EXIF 完成后广播 assets_changed 失败: {e}")

        # 3 秒后隐藏进度条
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self.batch_progress_frame.setVisible(False))
