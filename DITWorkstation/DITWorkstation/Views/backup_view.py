"""数据备份页面 - 安全拷贝与多重备份"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QProgressBar, QComboBox,
    QGroupBox, QFormLayout, QTextEdit, QListWidget,
    QListWidgetItem, QMessageBox, QCheckBox, QSplitter
)
from PySide6.QtCore import Qt, Slot

from pathlib import Path

from DITWorkstation.Models import ChecksumAlgorithm, BackupJob
from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Utils import WorkerThread, format_size, generate_log_message, safe_slot, get_db_service


class BackupView(QWidget):
    """数据备份视图"""

    def __init__(self):
        super().__init__()
        # 注入共享 db_service，使备份完成后能持久化 job + 回写 asset.backup_locations
        self.db_service = get_db_service()
        self.backup_service = BackupService(db_service=self.db_service)
        self.current_job: BackupJob = None
        self.worker: WorkerThread = None
        self._setup_ui()
        # 项目下拉变化时同步全局（"不关联"即 None 不广播，避免覆盖全局项目）
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        # 监听全局项目切换，同步本视图下拉
        from DITWorkstation.Views.main_window import get_data_bus
        get_data_bus().project_focus_changed.connect(self._on_global_project_changed)
        get_data_bus().workspace_focus_changed.connect(self._on_global_workspace_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("数据备份")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        layout.addWidget(title)

        subtitle = QLabel("从存储卡安全拷贝素材，支持多目标并行备份与校验和验证")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
        layout.addWidget(subtitle)

        # 关联项目（可选，但建议选择以便回写 asset.backup_locations）
        project_group = QGroupBox("关联项目（可选）")
        project_layout = QHBoxLayout(project_group)
        project_layout.addWidget(QLabel("当前备份归属的项目:"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(300)
        self.project_combo.addItem("（不关联项目，仅做文件备份）", None)
        project_layout.addWidget(self.project_combo, 1)
        project_layout.addStretch()
        layout.addWidget(project_group)

        # 源路径选择
        source_group = QGroupBox("源路径（存储卡）")
        source_layout = QHBoxLayout(source_group)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择存储卡路径...")
        self.source_edit.setReadOnly(True)
        source_btn = QPushButton("浏览...")
        source_btn.clicked.connect(self._select_source)
        source_layout.addWidget(self.source_edit, 1)
        source_layout.addWidget(source_btn)
        layout.addWidget(source_group)

        # 目标路径列表
        target_group = QGroupBox("备份目标（支持多目标）")
        target_layout = QVBoxLayout(target_group)

        self.target_list = QListWidget()
        self.target_list.setMaximumHeight(120)
        target_layout.addWidget(self.target_list)

        target_btn_layout = QHBoxLayout()
        add_target_btn = QPushButton("+ 添加目标")
        add_target_btn.clicked.connect(self._add_target)
        remove_target_btn = QPushButton("- 移除选中")
        remove_target_btn.clicked.connect(self._remove_target)
        target_btn_layout.addWidget(add_target_btn)
        target_btn_layout.addWidget(remove_target_btn)
        target_btn_layout.addStretch()
        target_layout.addLayout(target_btn_layout)
        layout.addWidget(target_group)

        # 配置选项
        config_group = QGroupBox("备份配置")
        config_layout = QFormLayout(config_group)

        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItems(["XXHash64（推荐，高性能）", "MD5（兼容性好）"])
        config_layout.addRow("校验和算法:", self.algorithm_combo)

        self.verify_check = QCheckBox("拷贝后验证完整性")
        self.verify_check.setChecked(True)
        config_layout.addRow("", self.verify_check)

        layout.addWidget(config_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始备份")
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #0a84ff;
                color: white;
                padding: 10px 24px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0070e0; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.start_btn.clicked.connect(self._start_backup)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_backup)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 进度区域
        progress_group = QGroupBox("备份进度")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #86868b; font-size: 12px;")
        progress_layout.addWidget(self.status_label)

        layout.addWidget(progress_group)

        # 日志输出
        log_group = QGroupBox("操作日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setStyleSheet("font-family: 'SF Mono', Menlo, monospace; font-size: 11px;")
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

        layout.addStretch()

    def showEvent(self, event):
        """每次显示时刷新项目下拉"""
        super().showEvent(event)
        self._load_projects()

    @safe_slot("加载项目失败")
    def _load_projects(self):
        """加载项目到下拉（优先同步全局当前项目，其次保留之前的选择）"""
        prev_id = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("（不关联项目，仅做文件备份）", None)
        try:
            from DITWorkstation.Views.main_window import get_current_workspace_id
            ws_id = get_current_workspace_id()
            projects = self.db_service.get_projects(workspace_id=ws_id)
        except Exception:
            projects = []
        for p in projects:
            self.project_combo.addItem(f"{p.name} ({p.project_id})", p.project_id)
        # 优先同步全局当前项目；若全局为 None，回退到本视图之前的选择
        from DITWorkstation.Views.main_window import get_current_project_id
        target_id = get_current_project_id() or prev_id
        if target_id:
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == target_id:
                    self.project_combo.setCurrentIndex(i)
                    break
        self.project_combo.blockSignals(False)
        # blockSignals 期间未触发 currentIndexChanged，手动同步一次全局（不关联时不广播）
        self._on_project_changed(self.project_combo.currentIndex())

    def _on_project_changed(self, index: int):
        """本视图项目下拉变化时同步全局（"不关联"即 None 不广播，避免覆盖全局项目）"""
        project_id = self.project_combo.currentData()
        if project_id is not None:
            from DITWorkstation.Views.main_window import set_current_project
            set_current_project(project_id)

    def _on_global_project_changed(self, project_id):
        """全局项目切换，同步本视图下拉；None 不强制覆盖（保留"不关联"语义）"""
        if project_id is None:
            return
        self.project_combo.blockSignals(True)
        for i in range(self.project_combo.count()):
            if self.project_combo.itemData(i) == project_id:
                self.project_combo.setCurrentIndex(i)
                break
        self.project_combo.blockSignals(False)
        # 手动触发一次本视图的 _on_project_changed 逻辑
        self._on_project_changed(self.project_combo.currentIndex())

    def _on_global_workspace_changed(self, _workspace_id):
        """全局工作区切换 -> 重新加载项目列表（按新工作区过滤）"""
        self._load_projects()

    def _select_source(self):
        path = QFileDialog.getExistingDirectory(self, "选择存储卡路径")
        if path:
            self.source_edit.setText(path)
            # 扫描文件信息
            try:
                files = self.backup_service.scan_source(path)
                total_size = sum(f["size"] for f in files)
                self._log(f"已选择源路径: {path}")
                self._log(f"发现 {len(files)} 个文件，总大小 {format_size(total_size)}")
            except Exception as e:
                self._log(f"扫描失败: {e}")

    def _add_target(self):
        path = QFileDialog.getExistingDirectory(self, "选择备份目标路径")
        if path:
            self.target_list.addItem(path)
            self._log(f"添加备份目标: {path}")

    def _remove_target(self):
        row = self.target_list.currentRow()
        if row >= 0:
            self.target_list.takeItem(row)

    @safe_slot("启动备份失败")
    def _start_backup(self):
        source = self.source_edit.text()
        if not source:
            QMessageBox.warning(self, "提示", "请先选择源路径")
            return

        targets = []
        for i in range(self.target_list.count()):
            targets.append(self.target_list.item(i).text())

        if not targets:
            QMessageBox.warning(self, "提示", "请至少添加一个备份目标")
            return

        # 确定算法
        algorithm = ChecksumAlgorithm.XXHASH64 if self.algorithm_combo.currentIndex() == 0 else ChecksumAlgorithm.MD5

        # 当前关联的项目（可空）
        project_id = self.project_combo.currentData()

        # 创建备份作业
        self.current_job = self.backup_service.create_backup_job(source, targets, algorithm)
        self._log(f"创建备份作业 [{self.current_job.job_id}]: {len(targets)} 个目标, "
                  f"{self.current_job.total_files} 个文件"
                  + (f"，关联项目 {project_id}" if project_id else ""))

        # 启动后台线程
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)

        # 把 project_id 通过 lambda 传给 execute_backup
        self.worker = WorkerThread(
            self.backup_service.execute_backup,
            self.current_job,
            None,   # progress_callback（worker 自身通过 emit 转发）
            None,   # file_completed_callback
            project_id
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.file_completed.connect(self._on_file_completed)
        self.worker.start()

    def _cancel_backup(self):
        self.backup_service.cancel()
        self._log("用户取消备份操作")
        self.cancel_btn.setEnabled(False)

    @Slot(str, object)
    def _on_file_completed(self, target: str, task):
        """单文件拷贝完成回调"""
        try:
            name = Path(task.source_path).name
        except Exception:
            name = task.source_path
        self._log(f"  ✓ {name} -> {target}")

    @Slot(str, float, str)
    def _on_progress(self, target: str, progress: float, message: str):
        self.progress_bar.setValue(int(progress * 1000))
        percent = int(progress * 100)
        self.status_label.setText(f"[{target}] {message} ({percent}%)")

    @Slot(object)
    def _on_finished(self, result):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(1000)

        job = result
        if job.status.value == "completed":
            self.status_label.setText("✅ 备份完成，所有目标验证通过")
            self._log(f"备份完成! 状态: {job.status.value}")
        elif job.status.value == "partial":
            self.status_label.setText("⚠️ 部分备份完成")
            self._log(f"部分完成，请检查失败目标")
        else:
            self.status_label.setText("❌ 备份失败")
            self._log(f"备份失败: {job.status.value}")

        for target in job.targets:
            status_icon = "✅" if target.status.value == "completed" else "❌"
            self._log(f"  {status_icon} {target.name}: {target.status.value} "
                      f"({target.completed_files}/{target.total_files} 文件)")

        # 广播 assets_changed，让 asset_info_view / search_view 知道 backup_locations 已更新
        if job.status.value in ("completed", "partial"):
            try:
                from DITWorkstation.Views.main_window import get_data_bus
                get_data_bus().emit_data_changed("assets_changed")
                self._log("已通知其他视图刷新素材备份位置信息")
            except Exception as e:
                self._log(f"通知其他视图失败（不影响备份结果）: {e}")

    @Slot(str)
    def _on_error(self, error: str):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(f"❌ 错误: {error}")
        self._log(f"错误: {error}")
        QMessageBox.critical(self, "备份错误", error)

    def _log(self, message: str):
        self.log_text.append(generate_log_message(message))
