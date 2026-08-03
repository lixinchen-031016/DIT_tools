"""拍摄日志管理页面"""
import uuid
from typing import List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QTextEdit, QSplitter,
    QDialog, QDialogButtonBox, QAbstractItemView, QTabWidget,
    QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Slot, QTimer

from DITWorkstation.App.session_context import get_data_bus
from DITWorkstation.Models import Project, ShootingLog, MediaAsset
from DITWorkstation.Services.metadata_service import MetadataService
from DITWorkstation.Utils import format_size, get_db_service, safe_slot, open_in_file_manager
from DITWorkstation.Views.Widgets import WorkspaceProjectSelector, RefreshOnShowView
from DITWorkstation.Views.Widgets.empty_state import attach_empty_state, sync_empty_state
from DITWorkstation.Views.Widgets.table_factory import make_table
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS, TITLE_QSS


class ShootingLogView(RefreshOnShowView):
    """拍摄日志视图"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self.metadata_service = MetadataService()
        self.current_project: Project = None
        self.current_log_id: str = None
        self._pending_asset_ids: List[str] = []
        self._logs = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        self._setup_selector(layout)

        right_splitter = self._setup_right_splitter()
        layout.addWidget(right_splitter, 3)

    def _setup_selector(self, layout):
        # 左侧：工作区 + 项目两级选择控件（封装下拉/列表/新建对话框/全局信号同步）
        self.selector = WorkspaceProjectSelector(
            project_widget="list",
            show_edit_workspace=False,
            show_new_project=True,
            show_delete_project=True,
            db_service=self.db_service,
        )
        layout.addWidget(self.selector, 1)
        # 监听选择控件的项目切换，做本视图业务联动
        # （工作区/项目下拉与全局信号同步已由 WorkspaceProjectSelector 内部处理）
        self.selector.project_changed.connect(self._on_project_changed)

    def _setup_right_splitter(self):
        # 右侧：上下 Splitter（上：日志管理，下：关联素材）
        right_splitter = QSplitter(Qt.Vertical)

        # 上半部分：日志管理
        top_widget = QWidget()
        right_panel = QVBoxLayout(top_widget)
        right_panel.setContentsMargins(0, 0, 0, 0)

        self._setup_header(right_panel)
        right_panel.addWidget(self._setup_form_group())
        right_panel.addWidget(self._setup_log_group(), 1)

        right_splitter.addWidget(top_widget)

        # 下半部分：标签页（关联素材 / 项目素材）
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(self._setup_bottom_tabs())
        right_splitter.addWidget(bottom_widget)

        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 3)

        return right_splitter

    def _setup_header(self, layout):
        title = QLabel("拍摄日志")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

    def _setup_form_group(self):
        # 新建日志表单
        form_group = QGroupBox("记录拍摄信息")
        form_layout = QFormLayout(form_group)

        self._build_form_basic_fields(form_layout)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("镜头描述...")
        form_layout.addRow("描述:", self.desc_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("备注信息...")
        self.notes_edit.setMinimumHeight(60)
        form_layout.addRow("备注:", self.notes_edit)

        self._build_form_buttons(form_layout)

        # 表单区套 QScrollArea，矮窗口下表单可滚动而不被挤压
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.NoFrame)
        form_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        form_scroll.setWidget(form_group)
        return form_scroll

    def _build_form_basic_fields(self, form_layout):
        info_row1 = QHBoxLayout()
        self.scene_edit = QLineEdit()
        self.scene_edit.setPlaceholderText("场景号 如: S001")
        self.scene_edit.setToolTip("场景编号，如 S001、S002。同一场景下包含多个镜头。")
        self.shot_edit = QLineEdit()
        self.shot_edit.setPlaceholderText("镜头号 如: 001A")
        self.shot_edit.setToolTip("镜头编号，如 001A（A 表示该镜头的第一种机位/取景）。同一镜头可能有多次拍摄。")
        self.take_edit = QLineEdit()
        self.take_edit.setPlaceholderText("镜次 如: 01")
        self.take_edit.setToolTip("镜次（拍摄次数），如 01、02。同一镜头重拍多次时用镜次区分。")
        info_row1.addWidget(QLabel("场景:"))
        info_row1.addWidget(self.scene_edit)
        info_row1.addWidget(QLabel("镜头:"))
        info_row1.addWidget(self.shot_edit)
        info_row1.addWidget(QLabel("镜次:"))
        info_row1.addWidget(self.take_edit)
        form_layout.addRow("", info_row1)

        info_row2 = QHBoxLayout()
        self.camera_edit = QLineEdit()
        self.camera_edit.setPlaceholderText("摄影机型号")
        self.lens_edit = QLineEdit()
        self.lens_edit.setPlaceholderText("镜头型号")
        info_row2.addWidget(QLabel("摄影机:"))
        info_row2.addWidget(self.camera_edit)
        info_row2.addWidget(QLabel("镜头:"))
        info_row2.addWidget(self.lens_edit)
        form_layout.addRow("", info_row2)

        info_row3 = QHBoxLayout()
        self.iso_edit = QLineEdit()
        self.iso_edit.setPlaceholderText("ISO")
        self.iso_edit.setToolTip("感光度。数值越高感光越强但噪点越多，如 100/400/800/1600。")
        self.aperture_edit = QLineEdit()
        self.aperture_edit.setPlaceholderText("光圈 如: f/2.8")
        self.aperture_edit.setToolTip("光圈值，如 f/2.8、f/4、f/5.6。数值越小光圈越大，进光量越多。")
        self.shutter_edit = QLineEdit()
        self.shutter_edit.setPlaceholderText("快门 如: 1/48s")
        self.shutter_edit.setToolTip("快门速度，如 1/50、1/100。表示感光元件曝光时间。")
        info_row3.addWidget(QLabel("ISO:"))
        info_row3.addWidget(self.iso_edit)
        info_row3.addWidget(QLabel("光圈:"))
        info_row3.addWidget(self.aperture_edit)
        info_row3.addWidget(QLabel("快门:"))
        info_row3.addWidget(self.shutter_edit)
        form_layout.addRow("", info_row3)

    def _build_form_buttons(self, form_layout):
        asset_select_row = QHBoxLayout()
        self.select_assets_btn = QPushButton("选择关联素材")
        self.select_assets_btn.clicked.connect(self._pick_assets_for_new_log)
        self.selected_assets_label = QLabel("未选择")
        self.selected_assets_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")
        asset_select_row.addWidget(self.select_assets_btn)
        asset_select_row.addWidget(self.selected_assets_label, 1)
        form_layout.addRow("", asset_select_row)

        # 从代表素材填充 EXIF：选择项目内一个素材，自动带出相机/镜头/ISO/光圈/快门
        exif_row = QHBoxLayout()
        self.fill_exif_btn = QPushButton("📷 从代表素材填充 EXIF")
        self.fill_exif_btn.setToolTip("选择项目内一个素材，自动带出相机/镜头/ISO/光圈/快门")
        self.fill_exif_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.BORDER_LIGHT};
                border: 1px solid {COLOR.BORDER};
                border-radius: {RADIUS.INPUT}px;
                padding: 6px 12px;
                font-size: {FONT_SIZE.SM}px;
                color: {COLOR.TEXT_PRIMARY};
            }}
            QPushButton:hover {{ background-color: {COLOR.BORDER}; }}
            QPushButton:disabled {{ color: {COLOR.DISABLED}; border-color: {COLOR.BORDER}; }}
        """)
        self.fill_exif_btn.clicked.connect(self._fill_exif_from_asset)
        exif_row.addWidget(self.fill_exif_btn)
        exif_row.addStretch()
        form_layout.addRow("", exif_row)

        add_log_btn = QPushButton("添加日志")
        add_log_btn.setToolTip("新建拍摄日志")
        add_log_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.PRIMARY};
                color: white;
                padding: 8px 20px;
                border-radius: {RADIUS.INPUT}px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {COLOR.PRIMARY_HOVER}; }}
        """)
        add_log_btn.clicked.connect(self._add_log)
        form_layout.addRow("", add_log_btn)

    def _setup_log_group(self):
        # 日志列表
        log_group = QGroupBox("拍摄日志列表")
        log_layout = QVBoxLayout(log_group)

        self.log_table = make_table(
            ["场景", "镜头", "镜次", "描述", "摄影机", "时间"],
            selection_mode=QAbstractItemView.SingleSelection,
        )
        self.log_table.itemSelectionChanged.connect(self._on_log_selected)
        self.log_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_table.customContextMenuRequested.connect(self._on_log_context_menu)
        log_layout.addWidget(self.log_table)
        attach_empty_state(self.log_table, "📋", "暂无拍摄日志", "在上方填写场景/镜头/镜次后点击「添加日志」")

        del_log_btn = QPushButton("删除选中日志")
        del_log_btn.setToolTip("删除选中的拍摄日志")
        del_log_btn.clicked.connect(self._delete_log)
        log_layout.addWidget(del_log_btn)

        return log_group

    def _setup_bottom_tabs(self):
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self._setup_linked_tab(), "关联素材")
        self.bottom_tabs.addTab(self._setup_project_tab(), "项目素材")
        return self.bottom_tabs

    def _setup_linked_tab(self):
        # 标签页 1：关联素材（绑定到选中日志）
        linked_tab = QWidget()
        linked_layout = QVBoxLayout(linked_tab)
        linked_layout.setContentsMargins(0, 0, 0, 0)

        asset_btn_row = QHBoxLayout()
        self.link_asset_btn = QPushButton("+ 关联素材")
        self.link_asset_btn.clicked.connect(self._link_assets_dialog)
        self.link_asset_btn.setEnabled(False)
        asset_btn_row.addWidget(self.link_asset_btn)

        self.unlink_asset_btn = QPushButton("解除关联")
        self.unlink_asset_btn.clicked.connect(self._unlink_selected_assets)
        self.unlink_asset_btn.setEnabled(False)
        asset_btn_row.addWidget(self.unlink_asset_btn)

        self.asset_count_label = QLabel("")
        self.asset_count_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")
        asset_btn_row.addWidget(self.asset_count_label)
        asset_btn_row.addStretch()
        linked_layout.addLayout(asset_btn_row)

        self.asset_table = make_table(
            ["文件名", "类型", "大小", "场景", "镜头"],
            sortable=True,
        )
        self.asset_table.itemSelectionChanged.connect(self._on_asset_selection_changed)
        # 双击打开所在目录
        self.asset_table.doubleClicked.connect(self._on_linked_asset_double_clicked)
        linked_layout.addWidget(self.asset_table, 1)
        attach_empty_state(self.asset_table, "🔗", "暂无关联素材", "选择日志后点击「+ 关联素材」")

        return linked_tab

    def _setup_project_tab(self):
        # 标签页 2：项目素材（多选后创建日志）
        proj_tab = QWidget()
        proj_layout = QVBoxLayout(proj_tab)
        proj_layout.setContentsMargins(0, 0, 0, 0)

        proj_btn_row = QHBoxLayout()
        self.log_selected_btn = QPushButton("为选中素材创建日志")
        self.log_selected_btn.clicked.connect(self._batch_create_log_for_selected)
        self.log_selected_btn.setEnabled(False)
        proj_btn_row.addWidget(self.log_selected_btn)

        self.proj_asset_count_label = QLabel("")
        self.proj_asset_count_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")
        proj_btn_row.addWidget(self.proj_asset_count_label)
        proj_btn_row.addStretch()
        proj_layout.addLayout(proj_btn_row)

        self.proj_asset_table = make_table(
            ["文件名", "类型", "大小", "场景", "镜头", "关联状态"],
            sortable=True,
            selection_mode=QAbstractItemView.ExtendedSelection,
        )
        # 双击打开所在目录
        self.proj_asset_table.doubleClicked.connect(self._on_proj_asset_double_clicked)
        self.proj_asset_table.itemSelectionChanged.connect(self._on_proj_asset_selection_changed)
        proj_layout.addWidget(self.proj_asset_table, 1)
        attach_empty_state(self.proj_asset_table, "📂", "暂无项目素材", "请先在「媒体导入」中导入素材")

        return proj_tab

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self.selector.refresh()

    @Slot(object)
    def _on_project_changed(self, project_id):
        """选择控件的项目切换 → 加载日志列表与项目素材

        全局项目广播已由 WorkspaceProjectSelector 内部处理，本方法只做本视图业务联动。
        """
        if project_id is None:
            self.current_project = None
            self.current_log_id = None
            self.log_table.setRowCount(0)
            sync_empty_state(self.log_table)
            self.asset_table.setRowCount(0)
            sync_empty_state(self.asset_table)
            self.proj_asset_table.setRowCount(0)
            sync_empty_state(self.proj_asset_table)
            self.asset_count_label.setText("")
            self.proj_asset_count_label.setText("")
            self.link_asset_btn.setEnabled(False)
            self.unlink_asset_btn.setEnabled(False)
            self.log_selected_btn.setEnabled(False)
            return
        self.current_project = self.db_service.get_project(project_id)
        if self.current_project:
            self._load_logs()
            self._load_project_assets()

    def _load_logs(self):
        if not self.current_project:
            return
        logs = self.db_service.get_shooting_logs(self.current_project.project_id)
        self._logs = logs
        self.log_table.setRowCount(len(logs))
        sync_empty_state(self.log_table)
        for i, log in enumerate(logs):
            self.log_table.setItem(i, 0, QTableWidgetItem(log.scene))
            self.log_table.setItem(i, 1, QTableWidgetItem(log.shot))
            self.log_table.setItem(i, 2, QTableWidgetItem(log.take))
            self.log_table.setItem(i, 3, QTableWidgetItem(log.description))
            self.log_table.setItem(i, 4, QTableWidgetItem(log.camera))
            self.log_table.setItem(i, 5, QTableWidgetItem(
                log.created_at.strftime("%m-%d %H:%M")
            ))
            # 存储log_id
            self.log_table.item(i, 0).setData(Qt.UserRole, log.log_id)

    @safe_slot("添加日志失败")
    def _add_log(self):
        if not self.current_project:
            QMessageBox.warning(self, "提示", "请先选择或创建项目")
            return

        scene = self.scene_edit.text()
        shot = self.shot_edit.text()
        take = self.take_edit.text()

        if not scene or not shot or not take:
            QMessageBox.warning(self, "提示", "请填写场景、镜头、镜次信息")
            return

        # ISO 输入校验：非空时必须为合法整数，避免静默归零
        iso_text = self.iso_edit.text().strip()
        iso_value = 0
        if iso_text:
            try:
                iso_value = int(iso_text)
                if iso_value < 0:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "输入错误", "ISO 必须为非负整数，请检查输入。")
                self.iso_edit.setFocus()
                return

        log = ShootingLog(
            log_id=str(uuid.uuid4())[:8],
            project_id=self.current_project.project_id,
            scene=scene,
            shot=shot,
            take=take,
            description=self.desc_edit.text(),
            camera=self.camera_edit.text(),
            lens=self.lens_edit.text(),
            iso=iso_value,
            aperture=self.aperture_edit.text(),
            shutter_speed=self.shutter_edit.text(),
            notes=self.notes_edit.toPlainText()
        )

        pending = list(self._pending_asset_ids)
        self.db_service.create_log_with_assets(log, pending, sync_scene_shot=True)
        self._load_logs()
        self._load_project_assets()

        # 广播：日志和素材关联均变更
        bus = get_data_bus()
        bus.emit_data_changed("logs_changed")
        if pending:
            bus.emit_data_changed("assets_changed")

        # 清空表单与待关联素材
        self.scene_edit.clear()
        self.shot_edit.clear()
        self.take_edit.clear()
        self.desc_edit.clear()
        self.notes_edit.clear()
        self._pending_asset_ids = []
        self.selected_assets_label.setText("未选择")

        # 自动选中新日志，触发底部「关联素材」面板显示刚关联的素材
        for i in range(self.log_table.rowCount()):
            if self.log_table.item(i, 0).data(Qt.UserRole) == log.log_id:
                self.log_table.selectRow(i)
                break

        if pending:
            QMessageBox.information(self, "成功", f"已创建日志并关联 {len(pending)} 个素材")

    @safe_slot("删除日志失败")
    def _delete_log(self):
        row = self.log_table.currentRow()
        if row < 0:
            return
        log_id = self.log_table.item(row, 0).data(Qt.UserRole)
        # 构造带场景标识的确认文案，防止误删
        log = next((l for l in self._logs if l.log_id == log_id), None)
        if log:
            scene_tag = f"场景 {log.scene} / 镜头 {log.shot} / 镜次 {log.take}"
            if log.description:
                scene_tag += f"（{log.description}）"
            msg = f"确定删除拍摄日志？\n\n  {scene_tag}\n\n关联素材不会被删除，仅解除关联。"
        else:
            msg = "确定删除该拍摄日志？关联素材不会被删除。"
        reply = QMessageBox.question(self, "确认删除", msg)
        if reply != QMessageBox.Yes:
            return
        self.db_service.delete_shooting_log(log_id)
        self.current_log_id = None
        self.link_asset_btn.setEnabled(False)
        self.unlink_asset_btn.setEnabled(False)
        self.asset_table.setRowCount(0)
        sync_empty_state(self.asset_table)
        self.asset_count_label.setText("")
        self._load_logs()
        self._load_project_assets()

        # 广播：日志删除会级联清空关联素材的 log_id
        bus = get_data_bus()
        bus.emit_data_changed("logs_changed")
        bus.emit_data_changed("assets_changed")

    def _on_log_context_menu(self, pos):
        """日志表右键菜单：删除此日志 / 复制场景信息"""
        from PySide6.QtWidgets import QMenu, QApplication
        item = self.log_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        log = self._logs[row] if row < len(self._logs) else None
        if not log:
            return

        menu = QMenu(self)
        action_delete = menu.addAction("删除此日志")
        action_copy_scene = menu.addAction("复制场景信息")

        action = menu.exec(self.log_table.viewport().mapToGlobal(pos))
        if action == action_delete:
            self.log_table.selectRow(row)
            self._delete_log()
        elif action == action_copy_scene:
            scene_info = f"{log.scene}/{log.shot}/{log.take}"
            if log.description:
                scene_info += f" - {log.description}"
            QApplication.clipboard().setText(scene_info)

    def _on_log_selected(self):
        row = self.log_table.currentRow()
        if row < 0:
            self.current_log_id = None
            self.link_asset_btn.setEnabled(False)
            self.asset_table.setRowCount(0)
            sync_empty_state(self.asset_table)
            self.asset_count_label.setText("")
            return
        log_id = self.log_table.item(row, 0).data(Qt.UserRole)
        self.current_log_id = log_id
        self.link_asset_btn.setEnabled(self.current_project is not None)
        self._load_assets_for_log(log_id)

    def _load_assets_for_log(self, log_id: str):
        assets = self.db_service.get_assets_by_log_id(log_id)
        self.asset_table.setRowCount(len(assets))
        sync_empty_state(self.asset_table)
        self.asset_count_label.setText(f"共 {len(assets)} 个关联素材")
        for i, asset in enumerate(assets):
            self.asset_table.setItem(i, 0, QTableWidgetItem(asset.file_name))
            self.asset_table.item(i, 0).setData(Qt.UserRole, asset.asset_id)
            self.asset_table.setItem(i, 1, QTableWidgetItem(asset.file_type))
            self.asset_table.setItem(i, 2, QTableWidgetItem(format_size(asset.file_size)))
            self.asset_table.setItem(i, 3, QTableWidgetItem(asset.scene))
            self.asset_table.setItem(i, 4, QTableWidgetItem(asset.shot))

    def _on_asset_selection_changed(self):
        has_selection = len(self.asset_table.selectedItems()) > 0
        self.unlink_asset_btn.setEnabled(has_selection)

    def _on_linked_asset_double_clicked(self, index):
        """双击关联素材 → 打开所在目录"""
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
            open_in_file_manager(asset.file_path)

    @safe_slot("关联素材失败")
    def _link_assets_dialog(self):
        if not self.current_project or not self.current_log_id:
            return

        all_assets = self.db_service.get_media_assets(self.current_project.project_id)
        linked_ids = {a.asset_id for a in self.db_service.get_assets_by_log_id(self.current_log_id)}
        unlinked = [a for a in all_assets if a.asset_id not in linked_ids]

        if not unlinked:
            QMessageBox.information(self, "提示", "该项目下没有可关联的素材，请先导入素材。")
            return

        dialog = _AssetLinkDialog(unlinked, self)
        if dialog.exec() == QDialog.Accepted:
            selected_ids = dialog.get_selected_asset_ids()
            if selected_ids:
                for aid in selected_ids:
                    self.db_service.update_media_asset_log_id(aid, self.current_log_id)
                self._load_assets_for_log(self.current_log_id)
                # 广播素材关联变更
                get_data_bus().emit_data_changed("assets_changed")
                QMessageBox.information(self, "成功", f"已关联 {len(selected_ids)} 个素材")

    @safe_slot("解除关联失败")
    def _unlink_selected_assets(self):
        if not self.current_log_id:
            return
        rows = set()
        for item in self.asset_table.selectedItems():
            rows.add(item.row())
        if not rows:
            return

        reply = QMessageBox.question(self, "确认", f"确定解除 {len(rows)} 个素材的关联？")
        if reply != QMessageBox.Yes:
            return

        for row in rows:
            asset_id = self.asset_table.item(row, 0).data(Qt.UserRole)
            self.db_service.update_media_asset_log_id(asset_id, None)

        self._load_assets_for_log(self.current_log_id)
        # 广播素材关联变更
        get_data_bus().emit_data_changed("assets_changed")

    # ===== 媒体文件驱动的日志记录 =====

    def _pick_assets_for_new_log(self):
        if not self.current_project:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return

        all_assets = self.db_service.get_media_assets(self.current_project.project_id)
        if not all_assets:
            QMessageBox.information(self, "提示", "该项目下还没有导入素材，请先在媒体导入界面导入素材。")
            return

        dialog = _AssetLinkDialog(all_assets, self)
        if dialog.exec() == QDialog.Accepted:
            self._pending_asset_ids = dialog.get_selected_asset_ids()
            if self._pending_asset_ids:
                self.selected_assets_label.setText(f"已选 {len(self._pending_asset_ids)} 个素材")
            else:
                self.selected_assets_label.setText("未选择")

    @safe_slot("填充 EXIF 失败")
    def _fill_exif_from_asset(self):
        """从项目内选择一个代表素材，读取其 EXIF 自动填充表单的相机/镜头/ISO/光圈/快门。

        降低用户手动录入成本：从已导入素材中选一个代表帧，EXIF 即可整组带出。
        """
        asset = self._pick_representative_asset()
        if asset is None:
            return

        filled = self._apply_exif_to_form(asset)
        if filled is None:
            return

        if filled:
            QMessageBox.information(
                self, "已填充 EXIF",
                f"从 {asset.file_name} 读取并填充：{ '、'.join(filled) }"
            )
        else:
            QMessageBox.information(
                self, "提示",
                "未填充任何字段。\n可能原因：\n"
                "  • 该素材没有 EXIF 信息\n"
                "  • 表单中相关字段已被填写（不会覆盖）"
            )

    def _pick_representative_asset(self) -> Optional[MediaAsset]:
        """弹对话框让用户从项目素材中选择一个代表素材，返回选中的素材（取消则 None）。"""
        if not self.current_project:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return None

        all_assets = self.db_service.get_media_assets(self.current_project.project_id)
        if not all_assets:
            QMessageBox.information(self, "提示", "该项目下还没有导入素材，无法填充 EXIF。")
            return None

        # 单选对话框：用 QListWidget 让用户挑一个代表素材
        from PySide6.QtWidgets import QDialog as _QDialog, QListWidget as _QListWidget, \
            QDialogButtonBox as _QBB, QVBoxLayout as _QVL
        dlg = _QDialog(self)
        dlg.setWindowTitle("选择代表素材以填充 EXIF")
        dlg.resize(520, 400)
        dl = _QVL(dlg)
        pick_list = _QListWidget()
        pick_list.setSelectionMode(_QListWidget.SingleSelection)
        # 文件名 (asset_id) 格式，便于用户识别
        for a in all_assets:
            pick_list.addItem(f"{a.file_name}  [{a.file_type}]  {format_size(a.file_size)}")
        if all_assets:
            pick_list.setCurrentRow(0)
        dl.addWidget(pick_list)
        bb = _QBB(_QBB.Ok | _QBB.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        dl.addWidget(bb)
        if dlg.exec() != _QDialog.Accepted:
            return None

        row = pick_list.currentRow()
        if row < 0:
            return None
        return all_assets[row]

    def _apply_exif_to_form(self, asset) -> Optional[List[str]]:
        """读取 asset 的 EXIF/视频元数据，仅在表单字段为空时填充。

        返回已填充字段名列表；文件不存在或读取失败时弹出警告并返回 None。
        """
        file_path = asset.file_path

        from pathlib import Path
        if not file_path or not Path(file_path).exists():
            QMessageBox.warning(self, "提示", f"素材文件不存在：\n{file_path}")
            return None

        # 读取 EXIF（图片/RAW）或视频元数据
        camera_make = ""
        camera_model = ""
        lens_model = ""
        iso = ""
        aperture = ""
        shutter = ""

        try:
            if asset.asset_type == "video":
                vm = self.metadata_service.read_video_metadata(file_path)
                camera_model = vm.codec or ""  # 视频没有相机概念，codec 占位
            else:
                meta = self.metadata_service.read_metadata(file_path)
                camera_make = meta.camera_make or ""
                camera_model = meta.camera_model or ""
                lens_model = meta.lens_model or ""
                if meta.iso:
                    iso = str(meta.iso)
                aperture = meta.aperture or ""
                shutter = meta.shutter_speed or ""
        except Exception as e:
            QMessageBox.warning(self, "提示", f"读取 EXIF 失败：{e}")
            return None

        # 合并 make+model 为 camera 字段（与 ShootingLog.camera 语义一致）
        camera_value = " ".join(p for p in (camera_make, camera_model) if p).strip()

        # 仅在字段为空时填充，避免覆盖用户已输入的内容
        filled = []
        if camera_value and not self.camera_edit.text().strip():
            self.camera_edit.setText(camera_value)
            filled.append("摄影机")
        if lens_model and not self.lens_edit.text().strip():
            self.lens_edit.setText(lens_model)
            filled.append("镜头")
        if iso and not self.iso_edit.text().strip():
            self.iso_edit.setText(iso)
            filled.append("ISO")
        if aperture and not self.aperture_edit.text().strip():
            self.aperture_edit.setText(aperture)
            filled.append("光圈")
        if shutter and not self.shutter_edit.text().strip():
            self.shutter_edit.setText(shutter)
            filled.append("快门")

        return filled

    def _load_project_assets(self):
        if not self.current_project:
            self.proj_asset_table.setRowCount(0)
            sync_empty_state(self.proj_asset_table)
            self.proj_asset_count_label.setText("")
            return

        assets = self.db_service.get_media_assets(self.current_project.project_id)
        linked_count = sum(1 for a in assets if a.log_id)
        self.proj_asset_table.setRowCount(len(assets))
        sync_empty_state(self.proj_asset_table)
        self.proj_asset_count_label.setText(
            f"共 {len(assets)} 个素材（{linked_count} 已关联）"
        )
        for i, asset in enumerate(assets):
            name_item = QTableWidgetItem(asset.file_name)
            name_item.setData(Qt.UserRole, asset.asset_id)
            self.proj_asset_table.setItem(i, 0, name_item)
            self.proj_asset_table.setItem(i, 1, QTableWidgetItem(asset.file_type))
            self.proj_asset_table.setItem(i, 2, QTableWidgetItem(format_size(asset.file_size)))
            self.proj_asset_table.setItem(i, 3, QTableWidgetItem(asset.scene))
            self.proj_asset_table.setItem(i, 4, QTableWidgetItem(asset.shot))
            status = "已关联" if asset.log_id else "未关联"
            status_item = QTableWidgetItem(status)
            if asset.log_id:
                status_item.setForeground(Qt.gray)
            self.proj_asset_table.setItem(i, 5, status_item)

    def _on_proj_asset_selection_changed(self):
        has = len(self.proj_asset_table.selectedItems()) > 0
        self.log_selected_btn.setEnabled(has and self.current_project is not None)

    def _on_proj_asset_double_clicked(self, index):
        """双击项目素材 → 打开所在目录"""
        if not index.isValid():
            return
        name_item = self.proj_asset_table.item(index.row(), 0)
        if name_item is None:
            return
        asset_id = name_item.data(Qt.UserRole)
        if not asset_id:
            return
        asset = self.db_service.get_media_asset(asset_id)
        if asset is not None:
            open_in_file_manager(asset.file_path)

    def _batch_create_log_for_selected(self):
        if not self.current_project:
            return
        rows = set()
        for item in self.proj_asset_table.selectedItems():
            rows.add(item.row())
        if not rows:
            return

        ids = []
        for row in rows:
            ids.append(self.proj_asset_table.item(row, 0).data(Qt.UserRole))
        self._pending_asset_ids = ids
        self.selected_assets_label.setText(f"已选 {len(ids)} 个素材")
        self.bottom_tabs.setCurrentIndex(0)
        QMessageBox.information(
            self, "已选定素材",
            f"已选定 {len(ids)} 个素材，请在上方填写场景/镜头/镜次等信息后点击「添加日志」。"
        )


class _AssetLinkDialog(QDialog):
    """素材关联选择对话框"""

    def __init__(self, assets, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要关联的素材")
        self.resize(600, 500)
        self._assets = assets

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入文件名过滤...")
        # 防抖：250ms 内连续输入只触发一次过滤，避免大素材量卡顿
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._do_filter)
        self.search_edit.textChanged.connect(self._on_text_changed)
        search_row.addWidget(self.search_edit, 1)
        layout.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["", "文件名", "类型", "大小"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._populate(assets)

    def _on_text_changed(self, _text: str):
        """输入变化时启动防抖定时器，而非立即过滤"""
        self._filter_timer.start()

    def _do_filter(self):
        """定时器触发后执行真正的过滤"""
        text = self.search_edit.text().lower()
        filtered = [a for a in self._assets if text in a.file_name.lower()]
        self._populate(filtered)

    def _populate(self, assets):
        self.table.setRowCount(len(assets))
        for i, asset in enumerate(assets):
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(checkbox_item.flags() | Qt.ItemIsUserCheckable)
            checkbox_item.setCheckState(Qt.Unchecked)
            checkbox_item.setData(Qt.UserRole, asset.asset_id)
            self.table.setItem(i, 0, checkbox_item)
            self.table.setItem(i, 1, QTableWidgetItem(asset.file_name))
            self.table.setItem(i, 2, QTableWidgetItem(asset.file_type))
            self.table.setItem(i, 3, QTableWidgetItem(format_size(asset.file_size)))

    def get_selected_asset_ids(self):
        ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole))
        return ids
