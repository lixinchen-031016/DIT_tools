"""数据备份页面 - 安全拷贝与多重备份"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QComboBox,
    QGroupBox, QFormLayout, QTextEdit,
    QMessageBox, QCheckBox, QScrollArea, QFrame
)
from PySide6.QtCore import Slot, Qt

from pathlib import Path
from typing import Optional

from DITWorkstation.Models import ChecksumAlgorithm, BackupJob
from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Utils import WorkerThread, format_size, generate_log_message, safe_slot, get_db_service, pick_directory


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
        # 备份目标路径列表（内联删除按钮式，替代 QListWidget）
        self._target_paths: list[str] = []
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

        # 目标路径列表（内联删除按钮式，每行一个路径 + 独立删除按钮）
        target_group = QGroupBox("备份目标（支持多目标）")
        target_layout = QVBoxLayout(target_group)

        # 可滚动容器，路径多时自动出现滚动条
        self.target_scroll = QScrollArea()
        self.target_scroll.setWidgetResizable(True)
        self.target_scroll.setMinimumHeight(80)
        self.target_scroll.setMaximumHeight(200)
        self.target_scroll.setFrameShape(QFrame.NoFrame)
        self.target_scroll.setStyleSheet("""
            QScrollArea {
                background-color: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 6px;
            }
        """)

        # 内部容器，动态添加路径行
        self.target_container = QWidget()
        self.target_container.setStyleSheet("background-color: #ffffff;")
        self.target_container_layout = QVBoxLayout(self.target_container)
        self.target_container_layout.setContentsMargins(0, 0, 0, 0)
        self.target_container_layout.setSpacing(2)
        self.target_container_layout.addStretch()
        self.target_scroll.setWidget(self.target_container)
        target_layout.addWidget(self.target_scroll)

        # 空状态提示
        self.target_empty_label = QLabel("（暂无备份目标，点击下方「添加目标」按钮）")
        self.target_empty_label.setStyleSheet("color: #86868b; font-size: 12px; padding: 8px;")
        target_layout.addWidget(self.target_empty_label)

        target_btn_layout = QHBoxLayout()
        add_target_btn = QPushButton("+ 添加目标")
        add_target_btn.clicked.connect(self._add_target)
        clear_target_btn = QPushButton("清空全部")
        clear_target_btn.clicked.connect(self._clear_targets)
        target_btn_layout.addWidget(add_target_btn)
        target_btn_layout.addWidget(clear_target_btn)
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
        self.log_text.setMinimumHeight(80)
        self.log_text.setMaximumHeight(220)
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
        """统一的目录选择对话框（委托给共享工具函数，处理打包兼容）。"""
        return pick_directory(self, title)

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
            self._target_paths.append(path)
            self._rebuild_target_rows()
            self._log(f"添加备份目标: {path}")
        else:
            self._log("未选择目录")

    def _remove_target_at(self, index: int):
        """移除指定索引的备份目标"""
        if 0 <= index < len(self._target_paths):
            removed = self._target_paths.pop(index)
            self._rebuild_target_rows()
            self._log(f"已移除备份目标: {removed}")

    def _rebuild_target_rows(self):
        """根据 _target_paths 重建内联行控件。

        每行：序号 + 路径（可选中复制）+ 独立删除按钮。
        空列表时隐藏容器，显示空状态提示。
        """
        # 清空旧行（保留末尾 stretch）
        while self.target_container_layout.count() > 1:
            item = self.target_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        has_targets = len(self._target_paths) > 0
        self.target_scroll.setVisible(has_targets)
        self.target_empty_label.setVisible(not has_targets)

        for i, path in enumerate(self._target_paths):
            row = QWidget()
            row.setStyleSheet("""
                QWidget {
                    background-color: #f5f5f7;
                    border-radius: 4px;
                }
            """)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 4, 4)
            row_layout.setSpacing(8)

            idx_label = QLabel(f"{i + 1}.")
            idx_label.setFixedWidth(24)
            idx_label.setStyleSheet("color: #86868b; font-weight: bold; background: transparent;")
            row_layout.addWidget(idx_label)

            path_label = QLabel(path)
            path_label.setToolTip(path)
            path_label.setStyleSheet("color: #1d1d1f; background: transparent;")
            path_label.setWordWrap(False)
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row_layout.addWidget(path_label, 1)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(24, 24)
            del_btn.setToolTip("移除此目标")
            del_btn.setStyleSheet("""
                QPushButton {
                    background-color: #e8e8ed;
                    border: none;
                    border-radius: 4px;
                    color: #86868b;
                    font-size: 12px;
                }
                QPushButton:hover { background-color: #ff453a; color: white; }
            """)
            # 用默认参数捕获当前索引，避免闭包延迟绑定
            del_btn.clicked.connect(lambda _, idx=i: self._remove_target_at(idx))
            row_layout.addWidget(del_btn)

            self.target_container_layout.insertWidget(self.target_container_layout.count() - 1, row)

    def _clear_targets(self):
        """清空所有备份目标"""
        if not self._target_paths:
            return
        reply = QMessageBox.question(
            self, "确认", f"确定清空全部 {len(self._target_paths)} 个备份目标？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._target_paths.clear()
            self._rebuild_target_rows()
            self._log("已清空所有备份目标")

    @safe_slot("启动备份失败")
    def _start_backup(self):
        source = self.source_edit.text()
        if not source:
            QMessageBox.warning(self, "提示", "请先选择源路径")
            return

        targets = list(self._target_paths)

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
