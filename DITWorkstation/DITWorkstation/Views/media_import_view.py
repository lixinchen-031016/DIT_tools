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
from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Utils import format_size, generate_log_message, WorkerThread


class MediaImportView(QWidget):
    """媒体导入视图"""

    def __init__(self):
        super().__init__()
        self.import_service = MediaImportService()
        self.db_service = DatabaseService()
        self.current_project: Project = None
        self.pending_files = []
        self.worker = None
        self._cancel_requested = False
        self._setup_ui()
        self._load_projects()

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

        proj_label = QLabel("选择项目")
        proj_label.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(proj_label)

        self.project_list = QListWidget()
        self.project_list.currentRowChanged.connect(self._on_project_selected)
        left_layout.addWidget(self.project_list, 1)

        proj_btn_layout = QHBoxLayout()
        new_proj_btn = QPushButton("+ 新建")
        new_proj_btn.clicked.connect(self._create_project)
        proj_btn_layout.addWidget(new_proj_btn)
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
        self.copy_mode_check.setToolTip("勾选后将文件复制到项目工作区再导入，原文件保持不动")
        opt_row1.addWidget(self.copy_mode_check)

        self.workspace_edit = QLineEdit()
        self.workspace_edit.setPlaceholderText("工作区目录（复制模式必填）")
        self.workspace_edit.setReadOnly(True)
        self.workspace_edit.setEnabled(False)
        ws_browse_btn = QPushButton("选择...")
        ws_browse_btn.setEnabled(False)
        ws_browse_btn.clicked.connect(self._select_workspace)
        self.copy_mode_check.toggled.connect(self.workspace_edit.setEnabled)
        self.copy_mode_check.toggled.connect(ws_browse_btn.setEnabled)
        opt_row1.addWidget(self.workspace_edit, 1)
        opt_row1.addWidget(ws_browse_btn)

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
            return
        item = self.project_list.item(row)
        project_id = item.data(Qt.UserRole)
        self.current_project = self.db_service.get_project(project_id)
        if self.current_project:
            self._log(f"已选择项目: {self.current_project.name}")
            if self.current_project.base_path:
                ws_path = Path(self.current_project.base_path) / "DIT_Workspace"
                self.workspace_edit.setText(str(ws_path))
            self._load_logs(project_id)
            self.import_btn.setEnabled(len(self.pending_files) > 0)

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

    def _select_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if path:
            self.workspace_edit.setText(path)

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
        workspace_dir = self.workspace_edit.text() if copy_to_workspace else None

        if copy_to_workspace and not workspace_dir:
            QMessageBox.warning(self, "提示", "复制模式请选择工作区目录")
            return

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
            self.worker.requestInterruption()
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
