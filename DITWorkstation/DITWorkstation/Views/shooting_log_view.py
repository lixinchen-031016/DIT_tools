"""拍摄日志管理页面"""
import uuid
from typing import List
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QComboBox,
    QTextEdit, QSplitter, QListWidget, QListWidgetItem,
    QDialog, QDialogButtonBox, QAbstractItemView, QTabWidget
)
from PySide6.QtCore import Qt, Slot

from DITWorkstation.Models import Project, ShootingLog
from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Utils import format_size


class ShootingLogView(QWidget):
    """拍摄日志视图"""

    def __init__(self):
        super().__init__()
        self.db_service = DatabaseService()
        self.current_project: Project = None
        self.current_log_id: str = None
        self._pending_asset_ids: List[str] = []
        self._setup_ui()
        self._load_projects()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 左侧：项目列表
        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("项目列表"))

        self.project_list = QListWidget()
        self.project_list.currentRowChanged.connect(self._on_project_selected)
        left_panel.addWidget(self.project_list, 1)

        proj_btn_layout = QHBoxLayout()
        new_proj_btn = QPushButton("+ 新建项目")
        new_proj_btn.clicked.connect(self._create_project)
        del_proj_btn = QPushButton("删除")
        del_proj_btn.clicked.connect(self._delete_project)
        proj_btn_layout.addWidget(new_proj_btn)
        proj_btn_layout.addWidget(del_proj_btn)
        left_panel.addLayout(proj_btn_layout)

        layout.addLayout(left_panel, 1)

        # 右侧：上下 Splitter（上：日志管理，下：关联素材）
        right_splitter = QSplitter(Qt.Vertical)

        # 上半部分：日志管理
        top_widget = QWidget()
        right_panel = QVBoxLayout(top_widget)
        right_panel.setContentsMargins(0, 0, 0, 0)

        title = QLabel("拍摄日志")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        right_panel.addWidget(title)

        # 新建日志表单
        form_group = QGroupBox("记录拍摄信息")
        form_layout = QFormLayout(form_group)

        info_row1 = QHBoxLayout()
        self.scene_edit = QLineEdit()
        self.scene_edit.setPlaceholderText("场景号 如: S001")
        self.shot_edit = QLineEdit()
        self.shot_edit.setPlaceholderText("镜头号 如: 001A")
        self.take_edit = QLineEdit()
        self.take_edit.setPlaceholderText("镜次 如: 01")
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
        self.aperture_edit = QLineEdit()
        self.aperture_edit.setPlaceholderText("光圈 如: f/2.8")
        self.shutter_edit = QLineEdit()
        self.shutter_edit.setPlaceholderText("快门 如: 1/48s")
        info_row3.addWidget(QLabel("ISO:"))
        info_row3.addWidget(self.iso_edit)
        info_row3.addWidget(QLabel("光圈:"))
        info_row3.addWidget(self.aperture_edit)
        info_row3.addWidget(QLabel("快门:"))
        info_row3.addWidget(self.shutter_edit)
        form_layout.addRow("", info_row3)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("镜头描述...")
        form_layout.addRow("描述:", self.desc_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("备注信息...")
        self.notes_edit.setMaximumHeight(60)
        form_layout.addRow("备注:", self.notes_edit)

        asset_select_row = QHBoxLayout()
        self.select_assets_btn = QPushButton("选择关联素材")
        self.select_assets_btn.clicked.connect(self._pick_assets_for_new_log)
        self.selected_assets_label = QLabel("未选择")
        self.selected_assets_label.setStyleSheet("color: #86868b;")
        asset_select_row.addWidget(self.select_assets_btn)
        asset_select_row.addWidget(self.selected_assets_label, 1)
        form_layout.addRow("", asset_select_row)

        add_log_btn = QPushButton("添加日志")
        add_log_btn.setStyleSheet("""
            QPushButton {
                background-color: #0a84ff;
                color: white;
                padding: 8px 20px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0070e0; }
        """)
        add_log_btn.clicked.connect(self._add_log)
        form_layout.addRow("", add_log_btn)

        right_panel.addWidget(form_group)

        # 日志列表
        log_group = QGroupBox("拍摄日志列表")
        log_layout = QVBoxLayout(log_group)

        self.log_table = QTableWidget()
        self.log_table.setColumnCount(6)
        self.log_table.setHorizontalHeaderLabels(
            ["场景", "镜头", "镜次", "描述", "摄影机", "时间"]
        )
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.log_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.log_table.itemSelectionChanged.connect(self._on_log_selected)
        log_layout.addWidget(self.log_table)

        del_log_btn = QPushButton("删除选中日志")
        del_log_btn.clicked.connect(self._delete_log)
        log_layout.addWidget(del_log_btn)

        right_panel.addWidget(log_group, 1)

        right_splitter.addWidget(top_widget)

        # 下半部分：标签页（关联素材 / 项目素材）
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        self.bottom_tabs = QTabWidget()

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
        self.asset_count_label.setStyleSheet("color: #86868b;")
        asset_btn_row.addWidget(self.asset_count_label)
        asset_btn_row.addStretch()
        linked_layout.addLayout(asset_btn_row)

        self.asset_table = QTableWidget()
        self.asset_table.setColumnCount(5)
        self.asset_table.setHorizontalHeaderLabels(
            ["文件名", "类型", "大小", "场景", "镜头"]
        )
        self.asset_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.asset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.asset_table.setAlternatingRowColors(True)
        self.asset_table.itemSelectionChanged.connect(self._on_asset_selection_changed)
        linked_layout.addWidget(self.asset_table, 1)

        self.bottom_tabs.addTab(linked_tab, "关联素材")

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
        self.proj_asset_count_label.setStyleSheet("color: #86868b;")
        proj_btn_row.addWidget(self.proj_asset_count_label)
        proj_btn_row.addStretch()
        proj_layout.addLayout(proj_btn_row)

        self.proj_asset_table = QTableWidget()
        self.proj_asset_table.setColumnCount(6)
        self.proj_asset_table.setHorizontalHeaderLabels(
            ["文件名", "类型", "大小", "场景", "镜头", "关联状态"]
        )
        self.proj_asset_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.proj_asset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.proj_asset_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.proj_asset_table.setAlternatingRowColors(True)
        self.proj_asset_table.itemSelectionChanged.connect(self._on_proj_asset_selection_changed)
        proj_layout.addWidget(self.proj_asset_table, 1)

        self.bottom_tabs.addTab(proj_tab, "项目素材")

        bottom_layout.addWidget(self.bottom_tabs)
        right_splitter.addWidget(bottom_widget)

        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 2)

        layout.addWidget(right_splitter, 3)

    def _load_projects(self):
        self.project_list.clear()
        projects = self.db_service.get_projects()
        for p in projects:
            item = QListWidgetItem(f"{p.name}\n{p.created_at.strftime('%Y-%m-%d')}")
            item.setData(Qt.UserRole, p.project_id)
            self.project_list.addItem(item)

    def showEvent(self, event):
        self._load_projects()
        super().showEvent(event)

    def _create_project(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
        if ok and name:
            project = self.db_service.create_project(name=name)
            self._load_projects()
            # 选中新项目
            for i in range(self.project_list.count()):
                if self.project_list.item(i).data(Qt.UserRole) == project.project_id:
                    self.project_list.setCurrentRow(i)
                    break

    def _delete_project(self):
        row = self.project_list.currentRow()
        if row < 0:
            return
        item = self.project_list.item(row)
        project_id = item.data(Qt.UserRole)
        reply = QMessageBox.question(self, "确认", "确定删除该项目及所有关联数据？")
        if reply == QMessageBox.Yes:
            self.db_service.delete_project(project_id)
            self._load_projects()

    def _on_project_selected(self, row: int):
        if row < 0:
            return
        item = self.project_list.item(row)
        project_id = item.data(Qt.UserRole)
        self.current_project = self.db_service.get_project(project_id)
        self._load_logs()
        self._load_project_assets()

    def _load_logs(self):
        if not self.current_project:
            return
        logs = self.db_service.get_shooting_logs(self.current_project.project_id)
        self.log_table.setRowCount(len(logs))
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

        log = ShootingLog(
            log_id=str(uuid.uuid4())[:8],
            project_id=self.current_project.project_id,
            scene=scene,
            shot=shot,
            take=take,
            description=self.desc_edit.text(),
            camera=self.camera_edit.text(),
            lens=self.lens_edit.text(),
            iso=int(self.iso_edit.text()) if self.iso_edit.text().isdigit() else 0,
            aperture=self.aperture_edit.text(),
            shutter_speed=self.shutter_edit.text(),
            notes=self.notes_edit.toPlainText()
        )

        pending = list(self._pending_asset_ids)
        self.db_service.create_log_with_assets(log, pending, sync_scene_shot=True)
        self._load_logs()
        self._load_project_assets()

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

    def _delete_log(self):
        row = self.log_table.currentRow()
        if row < 0:
            return
        log_id = self.log_table.item(row, 0).data(Qt.UserRole)
        reply = QMessageBox.question(self, "确认", "确定删除该拍摄日志？关联素材不会被删除。")
        if reply != QMessageBox.Yes:
            return
        self.db_service.delete_shooting_log(log_id)
        self.current_log_id = None
        self.link_asset_btn.setEnabled(False)
        self.unlink_asset_btn.setEnabled(False)
        self.asset_table.setRowCount(0)
        self.asset_count_label.setText("")
        self._load_logs()
        self._load_project_assets()

    def _on_log_selected(self):
        row = self.log_table.currentRow()
        if row < 0:
            self.current_log_id = None
            self.link_asset_btn.setEnabled(False)
            self.asset_table.setRowCount(0)
            self.asset_count_label.setText("")
            return
        log_id = self.log_table.item(row, 0).data(Qt.UserRole)
        self.current_log_id = log_id
        self.link_asset_btn.setEnabled(self.current_project is not None)
        self._load_assets_for_log(log_id)

    def _load_assets_for_log(self, log_id: str):
        assets = self.db_service.get_assets_by_log_id(log_id)
        self.asset_table.setRowCount(len(assets))
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
                QMessageBox.information(self, "成功", f"已关联 {len(selected_ids)} 个素材")

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

    def _load_project_assets(self):
        if not self.current_project:
            self.proj_asset_table.setRowCount(0)
            self.proj_asset_count_label.setText("")
            return

        assets = self.db_service.get_media_assets(self.current_project.project_id)
        linked_count = sum(1 for a in assets if a.log_id)
        self.proj_asset_table.setRowCount(len(assets))
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
        self.search_edit.textChanged.connect(self._filter)
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

    def _filter(self, text: str):
        text = text.lower()
        filtered = [a for a in self._assets if text in a.file_name.lower()]
        self._populate(filtered)

    def get_selected_asset_ids(self):
        ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole))
        return ids
