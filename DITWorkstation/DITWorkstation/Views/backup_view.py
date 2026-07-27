"""数据备份页面 - 安全拷贝与多重备份"""
import sys
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QProgressBar, QComboBox,
    QGroupBox, QFormLayout, QTextEdit, QListWidget,
    QMessageBox, QCheckBox
)
from PySide6.QtCore import Slot

from pathlib import Path
from typing import Optional

from DITWorkstation.Models import ChecksumAlgorithm, BackupJob
from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Utils import WorkerThread, format_size, generate_log_message, safe_slot, get_db_service


class BackupView(QWidget):
    """数据备份视图"""

    def __init__(self):
        super().__init__()
        # 注入共享 db_service，使备份完成后能持久化 job + 回写 asset.backup_locations
        self.db_service = get_db_service()
        self.backup_service = BackupService(db_service=self.db_service)
        # 复用 db_service 与 checksum_service，与备份共享哈希缓存
        self.import_service = MediaImportService(db_service=self.db_service)
        self.current_job: BackupJob = None
        self.worker: WorkerThread = None
        # 本次备份关联的项目（在 _start_backup 时锁定，_on_finished 时用于自动导入）
        self._backup_project_id: Optional[str] = None
        self._setup_ui()
        # 项目切换由共享控件处理（broadcast_none=False 保留"不关联"语义）

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
        project_group = QGroupBox("关联项目（可选，不选则仅做文件备份）")
        project_layout = QHBoxLayout(project_group)
        # 共享控件：broadcast_none=False 保留"不关联"语义（选 None 不清空全局项目）
        from DITWorkstation.Views.Widgets import WorkspaceProjectSelector
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_new_project=True,
            none_label="（不关联项目，仅做文件备份）",
            broadcast_none=False,
            db_service=self.db_service,
        )
        project_layout.addWidget(self.selector, 1)
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
        """每次显示时刷新工作区/项目列表"""
        super().showEvent(event)
        try:
            self.selector.refresh()
        except Exception as e:
            self._log(f"刷新工作区/项目列表失败: {e}")

    def _pick_directory(self, title: str) -> str:
        """统一的目录选择对话框。

        Windows 打包后原生 QFileDialog 可能因应用 manifest / 临时目录问题
        返回空字符串（即使选中了目录），此处先尝试原生对话框，失败则回退到
        Qt 非原生对话框，确保路径能正确返回。
        """
        path = QFileDialog.getExistingDirectory(self, title)
        if path:
            return path
        # 回退：非原生对话框（Windows 打包后更可靠）
        path = QFileDialog.getExistingDirectory(
            self, title, options=QFileDialog.Option.DontUseNativeDialog
        )
        return path or ""

    def _select_source(self):
        path = self._pick_directory("选择存储卡路径")
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
        path = self._pick_directory("选择备份目标路径")
        if path:
            self.target_list.addItem(path)
            self._log(f"添加备份目标: {path}")
        else:
            self._log("未选择目录")

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
        project_id = self.selector.get_current_project_id()
        # 锁定本次备份关联的项目，避免备份过程中用户切换项目导致导入到错误项目
        self._backup_project_id = project_id

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

        # 数据闭环：备份关联项目时，把未入库的源文件自动导入为 asset，
        # 并把成功的备份目标回写到 asset.backup_locations。
        # execute_backup 内部已尝试回写 backup_locations，但只对已存在的 asset 生效；
        # 因此这里先导入（幂等：已存在的会跳过），再对刚入库的 asset 补写 backup_locations。
        project_id = self._backup_project_id
        if project_id and job.status.value in ("completed", "partial"):
            try:
                files = getattr(job, '_files_cache', None)
                if files is None:
                    files = self.backup_service.scan_source(job.source_path)
                source_paths = [f["path"] for f in files]

                self._log(f"正在将 {len(source_paths)} 个源文件登记到项目...")
                import_result = self.import_service.import_assets(
                    project_id=project_id,
                    file_paths=source_paths,
                    compute_checksum=True,
                    read_metadata=True,
                    copy_to_workspace=False,  # 引用模式：文件已被备份，不再二次复制
                )

                imported = import_result.get("imported", 0)
                skipped = import_result.get("skipped", 0)
                failed = import_result.get("failed", 0)
                self._log(f"登记完成: 新增 {imported}, 跳过 {skipped}（已存在）, 失败 {failed}")

                # 对刚入库的 asset 补写 backup_locations（execute_backup 内部那次回写
                # 发生在导入之前，未覆盖新入库 asset）
                if imported > 0:
                    for t in job.targets:
                        if t.status.value == "completed":
                            self.db_service.add_backup_location_to_assets(
                                source_paths, t.path, project_id=project_id
                            )
            except Exception as e:
                self._log(f"自动登记素材失败（不影响备份结果）: {e}")

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
