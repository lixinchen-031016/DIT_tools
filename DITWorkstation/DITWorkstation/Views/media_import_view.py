"""媒体导入页面"""
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QProgressBar, QGroupBox,
    QFormLayout, QTextEdit, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QListWidget,
    QListWidgetItem, QSplitter, QComboBox, QSpinBox
)
from PySide6.QtCore import Qt, Slot

from DITWorkstation.Models import Project
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Utils import format_size, generate_log_message, WorkerThread, get_db_service, safe_slot


class MediaImportView(QWidget):
    """媒体导入视图"""

    def __init__(self):
        super().__init__()
        # 注入共享 db_service 单例，避免与其它视图各自新建 DatabaseService
        self.db_service = get_db_service()
        self.import_service = MediaImportService(db_service=self.db_service)
        self.current_project: Project = None
        self.pending_files = []
        self.worker = None
        self._cancel_requested = False
        self._setup_ui()
        self._load_projects()
        # 监听全局项目切换，同步本视图项目列表
        from DITWorkstation.Views.main_window import get_data_bus
        get_data_bus().project_focus_changed.connect(self._on_global_project_changed)
        get_data_bus().workspace_focus_changed.connect(self._on_global_workspace_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("媒体导入")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        layout.addWidget(title)

        subtitle = QLabel("将图片、视频、RAW文件导入项目，原文件位置保持不动")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
        layout.addWidget(subtitle)

        main_splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # 工作区选择区
        ws_label = QLabel("工作区")
        ws_label.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(ws_label)

        self.workspace_combo = QComboBox()
        self.workspace_combo.addItem("（全部工作区）", None)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        left_layout.addWidget(self.workspace_combo)

        ws_btn_layout = QHBoxLayout()
        new_ws_btn = QPushButton("+ 新建工作区")
        new_ws_btn.clicked.connect(self._create_workspace)
        ws_btn_layout.addWidget(new_ws_btn)
        left_layout.addLayout(ws_btn_layout)

        # 项目选择区
        proj_label = QLabel("项目")
        proj_label.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(proj_label)

        self.project_list = QListWidget()
        self.project_list.currentRowChanged.connect(self._on_project_selected)
        left_layout.addWidget(self.project_list, 1)

        proj_btn_layout = QHBoxLayout()
        self.new_proj_btn = QPushButton("+ 新建项目")
        self.new_proj_btn.clicked.connect(self._create_project)
        # 未选中具体工作区时禁用新建项目（项目必须属于某个工作区）
        self.new_proj_btn.setEnabled(False)
        proj_btn_layout.addWidget(self.new_proj_btn)
        left_layout.addLayout(proj_btn_layout)

        main_splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        source_group = QGroupBox("导入源")
        source_layout = QVBoxLayout(source_group)

        source_row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择要导入的文件夹...")
        self.source_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览文件夹...")
        browse_btn.clicked.connect(self._select_folder)
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(browse_btn)
        source_layout.addLayout(source_row)

        scan_row = QHBoxLayout()
        self.recursive_check = QCheckBox("递归扫描子文件夹")
        self.recursive_check.setChecked(True)
        scan_row.addWidget(self.recursive_check)

        self.include_images = QCheckBox("图片")
        self.include_images.setChecked(True)
        scan_row.addWidget(self.include_images)

        self.include_videos = QCheckBox("视频")
        self.include_videos.setChecked(True)
        scan_row.addWidget(self.include_videos)

        self.include_raw = QCheckBox("RAW")
        self.include_raw.setChecked(True)
        scan_row.addWidget(self.include_raw)

        scan_row.addStretch()
        scan_btn = QPushButton("🔍 扫描")
        scan_btn.clicked.connect(self._scan_folder)
        scan_row.addWidget(scan_btn)
        source_layout.addLayout(scan_row)

        right_layout.addWidget(source_group)

        files_group = QGroupBox("待导入文件")
        files_layout = QVBoxLayout(files_group)

        self.files_table = QTableWidget()
        self.files_table.setColumnCount(4)
        self.files_table.setHorizontalHeaderLabels(["文件名", "类型", "大小", "路径"])
        self.files_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.files_table.setSelectionBehavior(QTableWidget.SelectRows)
        files_layout.addWidget(self.files_table, 1)

        self.files_label = QLabel("")
        self.files_label.setStyleSheet("color: #86868b; font-size: 12px;")
        files_layout.addWidget(self.files_label)

        right_layout.addWidget(files_group, 1)

        options_group = QGroupBox("导入选项")
        options_layout = QVBoxLayout(options_group)

        opt_row1 = QHBoxLayout()

        self.checksum_check = QCheckBox("计算校验和")
        self.checksum_check.setChecked(True)
        opt_row1.addWidget(self.checksum_check)

        self.copy_mode_check = QCheckBox("复制到工作区")
        self.copy_mode_check.setChecked(False)
        self.copy_mode_check.setToolTip("勾选后将文件复制到 当前工作区目录/项目名/ 下再导入，原文件保持不动")
        opt_row1.addWidget(self.copy_mode_check)

        # 复制目标路径提示（只读，自动基于当前工作区.path/<项目名> 生成）
        self.copy_dest_label = QLabel("")
        self.copy_dest_label.setStyleSheet("color: #86868b; font-size: 12px;")
        self.copy_mode_check.toggled.connect(self._on_copy_check_toggled)
        opt_row1.addWidget(self.copy_dest_label, 1)

        options_layout.addLayout(opt_row1)

        opt_row2 = QHBoxLayout()
        opt_row2.addWidget(QLabel("关联拍摄日志:"))
        self.log_combo = QComboBox()
        self.log_combo.addItem("不关联", None)
        self.log_combo.setEnabled(False)
        self.log_combo.setMinimumWidth(200)
        opt_row2.addWidget(self.log_combo)
        opt_row2.addStretch()
        options_layout.addLayout(opt_row2)

        right_layout.addWidget(options_group)

        action_row = QHBoxLayout()
        self.import_btn = QPushButton("📥 开始导入")
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #34c759;
                color: white;
                padding: 10px 32px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #2db84e; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.import_btn.clicked.connect(self._start_import)
        self.import_btn.setEnabled(False)
        action_row.addWidget(self.import_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_import)
        action_row.addWidget(self.cancel_btn)

        action_row.addStretch()
        right_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        right_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #86868b; font-size: 12px;")
        right_layout.addWidget(self.status_label)

        log_group = QGroupBox("导入日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setStyleSheet("font-family: 'SF Mono', Menlo, monospace; font-size: 11px;")
        log_layout.addWidget(self.log_text)
        right_layout.addWidget(log_group)

        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 4)

        layout.addWidget(main_splitter, 1)

    def _load_workspaces(self):
        """加载工作区下拉，优先选中全局 current_workspace_id"""
        from DITWorkstation.Views.main_window import get_current_workspace_id
        prev_id = get_current_workspace_id()
        self.workspace_combo.blockSignals(True)
        self.workspace_combo.clear()
        self.workspace_combo.addItem("（全部工作区）", None)
        try:
            workspaces = self.db_service.get_workspaces()
        except Exception:
            workspaces = []
        target_index = 0
        for i, ws in enumerate(workspaces, start=1):
            label = ws.name
            if ws.path:
                label += f"  [{ws.path}]"
            self.workspace_combo.addItem(label, ws.workspace_id)
            if prev_id and ws.workspace_id == prev_id:
                target_index = i
        self.workspace_combo.setCurrentIndex(target_index)
        self.workspace_combo.blockSignals(False)
        # 同步"新建项目"按钮启用状态
        self.new_proj_btn.setEnabled(target_index > 0)
        # 同步"复制到工作区"复选框启用状态（path 为空时禁用）
        self._sync_copy_check_state()

    def _load_projects(self):
        # 保留当前选中的项目，避免切换视图后丢失
        prev_id = None
        if self.project_list.currentItem() is not None:
            prev_id = self.project_list.currentItem().data(Qt.UserRole)

        self.project_list.clear()
        # 按本视图工作区下拉的选择过滤（与全局状态保持同步）
        ws_id = self.workspace_combo.currentData() if hasattr(self, 'workspace_combo') else None
        if ws_id is None:
            # 兜底：本视图下拉未初始化时退回全局
            from DITWorkstation.Views.main_window import get_current_workspace_id
            ws_id = get_current_workspace_id()
        projects = self.db_service.get_projects(workspace_id=ws_id)
        restore_row = -1
        for i, p in enumerate(projects):
            item = QListWidgetItem(f"{p.name}\n{p.created_at.strftime('%Y-%m-%d')}")
            item.setData(Qt.UserRole, p.project_id)
            self.project_list.addItem(item)
            if prev_id and p.project_id == prev_id:
                restore_row = i

        if restore_row >= 0:
            self.project_list.setCurrentRow(restore_row)
        else:
            # 本视图无选中时，回退到全局当前项目（切视图无需重选）
            from DITWorkstation.Views.main_window import get_current_project_id
            global_pid = get_current_project_id()
            if global_pid:
                for i in range(self.project_list.count()):
                    if self.project_list.item(i).data(Qt.UserRole) == global_pid:
                        self.project_list.setCurrentRow(i)
                        break

    def showEvent(self, event):
        self._load_workspaces()
        self._load_projects()
        super().showEvent(event)

    @safe_slot("新建项目失败")
    def _create_project(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
        if ok and name:
            from DITWorkstation.Views.main_window import get_current_workspace_id
            project = self.db_service.create_project(name=name, workspace_id=get_current_workspace_id())
            self._load_projects()
            for i in range(self.project_list.count()):
                if self.project_list.item(i).data(Qt.UserRole) == project.project_id:
                    self.project_list.setCurrentRow(i)
                    break

    def _on_project_selected(self, row: int):
        if row < 0:
            self.current_project = None
            self.log_combo.clear()
            self.log_combo.addItem("不关联", None)
            self.log_combo.setEnabled(False)
            self.import_btn.setEnabled(False)
            self._update_copy_dest_label()
            return
        item = self.project_list.item(row)
        project_id = item.data(Qt.UserRole)
        # 同步全局当前项目
        from DITWorkstation.Views.main_window import set_current_project
        set_current_project(project_id)
        self.current_project = self.db_service.get_project(project_id)
        if self.current_project:
            self._log(f"已选择项目: {self.current_project.name}")
            self._load_logs(project_id)
            self.import_btn.setEnabled(len(self.pending_files) > 0)
            self._update_copy_dest_label()

    def _on_global_project_changed(self, project_id):
        """全局项目切换，同步本视图列表选择（避免回调循环）"""
        self.project_list.blockSignals(True)
        target_row = -1
        if project_id is not None:
            for i in range(self.project_list.count()):
                if self.project_list.item(i).data(Qt.UserRole) == project_id:
                    target_row = i
                    break
        self.project_list.setCurrentRow(target_row)
        self.project_list.blockSignals(False)
        # 手动触发一次本视图的项目选择逻辑（刷新 logs 等）
        self._on_project_selected(self.project_list.currentRow())

    def _on_global_workspace_changed(self, _workspace_id):
        """全局工作区切换 -> 同步本视图工作区下拉，连带重载项目列表"""
        # 重新加载工作区下拉会触发 _on_workspace_changed -> _load_projects
        self._load_workspaces()

    def _on_workspace_changed(self, _index: int):
        """本视图工作区下拉切换 -> 广播到全局，并重载项目列表"""
        ws_id = self.workspace_combo.currentData()
        from DITWorkstation.Views.main_window import set_current_workspace
        set_current_workspace(ws_id)
        # set_current_workspace 会触发 _on_global_workspace_changed，但若全局未变（同 ID）则不会，
        # 此处手动重载保证一致性
        self._load_projects()
        # 同步"新建项目"按钮与"复制到工作区"复选框启用状态
        self.new_proj_btn.setEnabled(ws_id is not None)
        self._sync_copy_check_state()

    @safe_slot("新建工作区失败")
    def _create_workspace(self):
        """新建工作区对话框（与项目概览看板共用同一交互模式）"""
        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("新建工作区")
        dlg.setMinimumWidth(420)
        form = QFormLayout(dlg)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("如「2026 春季广告片」")
        path_edit = QLineEdit()
        path_edit.setPlaceholderText("工作区物理目录（建议填写，导入时复制素材到此目录下）")

        path_row = QHBoxLayout()
        path_row.addWidget(path_edit, 1)
        browse_btn = QPushButton("浏览…")
        def _browse():
            d = QFileDialog.getExistingDirectory(dlg, "选择工作区目录", path_edit.text())
            if d:
                path_edit.setText(d)
        browse_btn.clicked.connect(_browse)
        path_row.addWidget(browse_btn)
        path_container = QWidget()
        path_container.setLayout(path_row)
        path_container.layout().setContentsMargins(0, 0, 0, 0)

        desc_edit = QLineEdit()
        desc_edit.setPlaceholderText("描述（可选）")

        form.addRow("名称 *:", name_edit)
        form.addRow("目录:", path_container)
        form.addRow("描述:", desc_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("保存")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "工作区名称不能为空")
            return

        try:
            ws = self.db_service.create_workspace(
                name=name,
                path=path_edit.text().strip(),
                description=desc_edit.text().strip()
            )
            # 新建后自动设为当前工作区
            from DITWorkstation.Views.main_window import set_current_workspace, get_data_bus
            set_current_workspace(ws.workspace_id)
            get_data_bus().emit_data_changed("workspaces_changed")
            self._log(f"已创建工作区: {name} ({ws.workspace_id})")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"创建工作区失败：{e}")

    def _get_current_workspace(self):
        """返回当前选中的工作区对象（无选中返回 None）"""
        ws_id = self.workspace_combo.currentData() if hasattr(self, 'workspace_combo') else None
        if not ws_id:
            return None
        return self.db_service.get_workspace(ws_id)

    def _sync_copy_check_state(self):
        """根据当前工作区是否有 path，启用/禁用「复制到工作区」复选框。

        - 未选具体工作区：禁用（无目标目录）
        - 工作区 path 为空：禁用并提示用户去编辑工作区补充目录
        - 工作区 path 非空：启用
        """
        if not hasattr(self, 'copy_mode_check'):
            return
        ws = self._get_current_workspace()
        if ws is None:
            self.copy_mode_check.setEnabled(False)
            self.copy_mode_check.setChecked(False)
            self.copy_mode_check.setToolTip("请先选择具体工作区")
        elif not ws.path:
            self.copy_mode_check.setEnabled(False)
            self.copy_mode_check.setChecked(False)
            self.copy_mode_check.setToolTip(
                "当前工作区未设置目录，请到「项目概览」看板编辑工作区补充目录"
            )
        else:
            self.copy_mode_check.setEnabled(True)
            self.copy_mode_check.setToolTip(
                f"勾选后将素材复制到：{ws.path}/<项目名>/ 下"
            )

    def _load_logs(self, project_id: str):
        self.log_combo.clear()
        self.log_combo.addItem("不关联", None)
        logs = self.db_service.get_shooting_logs(project_id)
        if logs:
            for log in logs:
                label = f"{log.scene} / {log.shot} / {log.take}"
                if log.description:
                    label += f" - {log.description}"
                self.log_combo.addItem(label, log.log_id)
            self.log_combo.setEnabled(True)
        else:
            self.log_combo.setEnabled(False)

    def _select_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择要导入的文件夹")
        if path:
            self.source_edit.setText(path)

    def _on_copy_check_toggled(self, checked: bool):
        """复制到工作区复选框切换时，更新目标路径提示"""
        self._update_copy_dest_label()

    def _update_copy_dest_label(self):
        """根据当前工作区与项目，更新复制目标路径提示标签"""
        if not hasattr(self, 'copy_dest_label'):
            return
        if not self.copy_mode_check.isChecked():
            self.copy_dest_label.setText("")
            return
        ws = self._get_current_workspace()
        if ws is None or not ws.path:
            self.copy_dest_label.setText("（未选择有效工作区或工作区无目录）")
            self.copy_dest_label.setStyleSheet("color: #ff3b30; font-size: 12px;")
            return
        if self.current_project:
            dest = str(Path(ws.path) / self.current_project.name)
        else:
            dest = str(Path(ws.path) / "<项目名>")
        self.copy_dest_label.setText(f"→ {dest}")
        self.copy_dest_label.setStyleSheet("color: #86868b; font-size: 12px;")

    def _scan_folder(self):
        folder = self.source_edit.text()
        if not folder:
            QMessageBox.warning(self, "提示", "请先选择要导入的文件夹")
            return

        try:
            files = self.import_service.scan_media_folder(
                folder,
                recursive=self.recursive_check.isChecked(),
                include_images=self.include_images.isChecked(),
                include_videos=self.include_videos.isChecked(),
                include_raw=self.include_raw.isChecked()
            )
            self.pending_files = [str(f) for f in files]
            self._display_files(files)
            self.import_btn.setEnabled(len(files) > 0 and self.current_project is not None)
            total_size = sum(f.stat().st_size for f in files if f.exists())
            self._log(f"扫描完成: 发现 {len(files)} 个媒体文件, 总大小 {format_size(total_size)}")
        except Exception as e:
            QMessageBox.critical(self, "扫描错误", str(e))
            self._log(f"扫描失败: {e}")

    def _display_files(self, files):
        self.files_table.setRowCount(len(files))
        for i, f in enumerate(files):
            stat = f.stat()
            self.files_table.setItem(i, 0, QTableWidgetItem(f.name))
            asset_type = self.import_service.classify_media_type(str(f))
            type_map = {
                "image": "🖼️ 图片",
                "video": "🎬 视频",
                "raw": "📷 RAW",
                "audio": "🎵 音频",
                "other": "📄 其他"
            }
            self.files_table.setItem(i, 1, QTableWidgetItem(type_map.get(asset_type.value, asset_type.value)))
            self.files_table.setItem(i, 2, QTableWidgetItem(format_size(stat.st_size)))
            self.files_table.setItem(i, 3, QTableWidgetItem(str(f.parent)))

        self.files_label.setText(f"共 {len(files)} 个文件")

    def _start_import(self):
        if not self.current_project:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return

        if not self.pending_files:
            QMessageBox.warning(self, "提示", "没有可导入的文件")
            return

        copy_to_workspace = self.copy_mode_check.isChecked()
        workspace_dir = None
        if copy_to_workspace:
            # 复制目录自动基于当前工作区.path / 项目名
            ws = self._get_current_workspace()
            if ws is None or not ws.path:
                QMessageBox.warning(
                    self, "提示",
                    "当前工作区未设置目录，请到「项目概览」看板编辑工作区补充目录后再勾选复制模式"
                )
                return
            workspace_dir = str(Path(ws.path) / self.current_project.name)

        log_id = self.log_combo.currentData()
        scene = ""
        shot = ""
        if log_id:
            log = self.db_service.get_shooting_log(log_id)
            if log:
                scene = log.scene
                shot = log.shot

        self.import_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self._cancel_requested = False

        self.worker = WorkerThread(
            self.import_service.import_assets,
            self.current_project.project_id,
            self.pending_files,
            compute_checksum=self.checksum_check.isChecked(),
            copy_to_workspace=copy_to_workspace,
            workspace_dir=workspace_dir,
            log_id=log_id,
            scene=scene,
            shot=shot,
            cancel_check=lambda: self._cancel_requested
        )
        self.worker.finished.connect(self._on_import_finished)
        self.worker.error.connect(self._on_import_error)
        self.worker.progress.connect(self._on_progress)
        self.worker.start()

        self._log("开始导入...")
        self.status_label.setText("正在导入...")

    def _cancel_import(self):
        if self.worker and self.worker.isRunning():
            self._cancel_requested = True
            # service 通过 cancel_check 回调判断取消，无需 Qt 中断标志
            self._log("取消导入...")
            self.status_label.setText("正在取消...")

    @Slot(str, float, str)
    def _on_progress(self, target: str, progress: float, message: str):
        self.progress_bar.setValue(int(progress * 100))
        self.status_label.setText(f"{message} ({int(progress * 100)}%)")

    @Slot(object)
    def _on_import_finished(self, result):
        self.import_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)

        # 防御 result 为 None 或非 dict
        if not isinstance(result, dict):
            self.status_label.setText("❌ 导入返回异常结果")
            self._log(f"导入返回异常: {result!r}")
            QMessageBox.warning(self, "导入异常", "导入任务返回了异常结果，请重试。")
            return

        imported = result.get('imported', 0)
        skipped = result.get('skipped', 0)
        failed = result.get('failed', 0)
        cancelled = result.get('cancelled', False)

        if cancelled:
            self.status_label.setText(
                f"⚠️ 已取消: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个"
            )
            self._log(f"导入已取消: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个")
        else:
            self.status_label.setText(
                f"✅ 导入完成: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个"
            )
            self._log(f"导入完成: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个")

        if failed > 0:
            fail_details = [d for d in result.get('details', []) if d.get('status') == 'failed']
            for d in fail_details[:5]:
                self._log(f"  失败: {d['path']} - {d.get('error', '未知错误')}")

        # 广播数据变更，通知日志/检索/素材信息视图刷新
        if imported > 0:
            from DITWorkstation.Views.main_window import get_data_bus
            get_data_bus().emit_data_changed("assets_changed")

        QMessageBox.information(
            self, "导入完成",
            f"导入完成！\n\n成功: {imported} 个\n跳过: {skipped} 个\n失败: {failed} 个"
        )

    @Slot(str)
    def _on_import_error(self, error: str):
        self.import_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(f"❌ 错误: {error}")
        self._log(f"导入错误: {error}")
        QMessageBox.critical(self, "导入错误", error)

    def _log(self, message: str):
        self.log_text.append(generate_log_message(message))
