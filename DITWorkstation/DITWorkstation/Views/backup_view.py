"""数据备份页面 - 安全拷贝与多重备份"""
import shutil

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QComboBox,
    QGroupBox, QTextEdit,
    QMessageBox, QCheckBox, QScrollArea, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Signal, Slot, Qt

from pathlib import Path
from typing import Optional

from DITWorkstation.Models import ChecksumAlgorithm, BackupJob, BackupTemplate
from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Utils import (
    format_size, safe_slot, get_db_service,
    pick_directory, pick_save_file, find_overwrite_conflicts, logger,
)
from DITWorkstation.App.session_context import get_data_bus
from DITWorkstation.App.navigation import get_nav_index
from DITWorkstation.Views.Widgets import (
    RefreshOnShowView, WorkspaceProjectSelector, edit_backup_template,
)
from DITWorkstation.Views.Widgets.status_panel import StatusPanel
from DITWorkstation.Views.Widgets.error_dialog import show_error
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS, TITLE_QSS, SUBTITLE_QSS, PRIMARY_BUTTON_QSS, MONO_FONT_QSS
from DITWorkstation.ViewModels import TaskViewModel


class BackupView(RefreshOnShowView):
    """数据备份视图"""

    # 独立校验已有备份的进度信号：(current, total, message)
    _verify_progress = Signal(int, int, str)

    def __init__(self, db_service=None):
        super().__init__()
        # 注入共享 db_service，使备份完成后能持久化 job + 回写 asset.backup_locations
        self.db_service = db_service or get_db_service()
        self.backup_service = BackupService(db_service=self.db_service)
        # 复用 db_service 与 checksum_service，与备份共享哈希缓存
        self.import_service = MediaImportService(db_service=self.db_service)
        self.current_job: BackupJob = None
        self.task_vm = TaskViewModel(self)
        self.task_vm.progress.connect(self._on_progress)
        self.task_vm.file_completed.connect(self._on_file_completed)
        self.task_vm.finished.connect(self._on_task_finished)
        self.task_vm.error.connect(self._on_task_error)
        self._task_kind = None
        # 本次备份关联的项目（在 _start_backup 时锁定，_on_finished 时用于自动导入）
        self._backup_project_id: Optional[str] = None
        # 备份目标路径列表（内联删除按钮式，替代 QListWidget）
        self._target_paths: list[str] = []
        self._backup_templates = []
        self._setup_ui()
        self._verify_progress.connect(self._on_verify_progress)
        # 项目切换由共享控件处理（broadcast_none=False 保留"不关联"语义）

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("数据备份")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel("从存储卡安全拷贝素材，支持多目标并行备份与校验和验证")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # 上下两段：上段配置区，下段执行状态区（不使用可拖动分割条，
        # 空间不足时由整个视图的外层滚动条滚动）
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(16)

        # 关联项目（可选，但建议选择以便回写 asset.backup_locations）
        project_group = QGroupBox("关联项目（可选，不选则仅做文件备份）")
        project_layout = QHBoxLayout(project_group)
        # 共享控件：broadcast_none=False 保留"不关联"语义（选 None 不清空全局项目）
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_new_project=True,
            none_label="（不关联项目，仅做文件备份）",
            broadcast_none=False,
            db_service=self.db_service,
        )
        project_layout.addWidget(self.selector, 1)
        config_layout.addWidget(project_group)

        # 源路径选择
        source_group = QGroupBox("源路径（存储卡）")
        source_layout = QHBoxLayout(source_group)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择存储卡路径...")
        self.source_edit.setReadOnly(True)
        source_btn = QPushButton("浏览…")
        source_btn.clicked.connect(self._select_source)
        source_layout.addWidget(self.source_edit, 1)
        source_layout.addWidget(source_btn)
        config_layout.addWidget(source_group)

        # 目标路径列表（内联删除按钮式，每行一个路径 + 独立删除按钮）
        target_group = QGroupBox("备份目标（支持多目标）")
        target_layout = QVBoxLayout(target_group)

        # 可滚动容器，路径多时自动出现滚动条
        self.target_scroll = QScrollArea()
        self.target_scroll.setWidgetResizable(True)
        self.target_scroll.setMinimumHeight(80)
        self.target_scroll.setFrameShape(QFrame.NoFrame)
        self.target_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {COLOR.BG_CARD};
                border: 1px solid {COLOR.BORDER};
                border-radius: {RADIUS.INPUT}px;
            }}
        """)

        # 内部容器，动态添加路径行
        self.target_container = QWidget()
        self.target_container.setStyleSheet(f"background-color: {COLOR.BG_CARD};")
        self.target_container_layout = QVBoxLayout(self.target_container)
        self.target_container_layout.setContentsMargins(0, 0, 0, 0)
        self.target_container_layout.setSpacing(2)
        self.target_container_layout.addStretch()
        self.target_scroll.setWidget(self.target_container)
        target_layout.addWidget(self.target_scroll)

        # 空状态提示
        self.target_empty_label = QLabel("（暂无备份目标，点击下方「添加目标」按钮）")
        self.target_empty_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px; padding: 8px;")
        target_layout.addWidget(self.target_empty_label)

        target_btn_layout = QHBoxLayout()
        add_target_btn = QPushButton("+ 添加目标")
        add_target_btn.setToolTip("添加一个备份目标目录")
        add_target_btn.clicked.connect(self._add_target)
        clear_target_btn = QPushButton("清空全部")
        clear_target_btn.setToolTip("清空所有备份目标")
        clear_target_btn.clicked.connect(self._clear_targets)
        target_btn_layout.addWidget(add_target_btn)
        target_btn_layout.addWidget(clear_target_btn)
        target_btn_layout.addStretch()
        target_layout.addLayout(target_btn_layout)
        config_layout.addWidget(target_group)

        # 备份方案模板
        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("备份方案:"))
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(240)
        self.template_combo.currentIndexChanged.connect(self._apply_selected_template)
        template_row.addWidget(self.template_combo)
        self.save_template_btn = QPushButton("保存当前方案")
        self.save_template_btn.setToolTip("把当前备份目标、校验算法和验证选项保存为可复用模板")
        self.save_template_btn.clicked.connect(self._save_backup_template)
        template_row.addWidget(self.save_template_btn)
        self.delete_template_btn = QPushButton("删除方案")
        self.delete_template_btn.clicked.connect(self._delete_backup_template)
        template_row.addWidget(self.delete_template_btn)
        template_row.addStretch()
        config_layout.addLayout(template_row)

        # 配置选项（内联，不再单独 GroupBox）
        config_row = QHBoxLayout()
        config_row.addWidget(QLabel("校验和:"))
        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItems(["XXHash64（推荐）", "MD5（兼容）"])
        self.algorithm_combo.setToolTip(
            "校验和用于验证文件完整性，检测拷贝是否产生位错误。\n"
            "• XXHash64（推荐）：速度极快，适合大文件批量校验，现代算法\n"
            "• MD5（兼容）：通用性最广，部分老系统/工作流要求使用"
        )
        config_row.addWidget(self.algorithm_combo)
        self.verify_check = QCheckBox("拷贝后验证完整性")
        self.verify_check.setChecked(True)
        self.verify_check.setToolTip(
            "拷贝完成后重新计算目标文件校验和，与源文件比对，确保拷贝无误差。建议始终勾选。"
        )
        config_row.addWidget(self.verify_check)
        config_row.addStretch()
        config_layout.addLayout(config_row)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("📦 开始备份")
        self.start_btn.setToolTip("开始备份（将文件复制到所有目标目录）")
        self.start_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        self.start_btn.clicked.connect(self._start_backup)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setToolTip("取消正在进行的备份任务")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_backup)

        self.mhl_btn = QPushButton("📄 导出 MHL 校验清单")
        self.mhl_btn.setToolTip("把本次备份的源文件哈希列表导出为 ASC MHL XML 文件")
        self.mhl_btn.setEnabled(False)
        self.mhl_btn.clicked.connect(self._export_mhl)

        self.verify_backup_btn = QPushButton("🔍 校验已有备份")
        self.verify_backup_btn.setToolTip(
            "对已关联项目素材的 backup_locations 做完整性校验："
            "检查备份文件是否存在，并与入库时的校验和比对，检测位错误/丢失"
        )
        self.verify_backup_btn.clicked.connect(self._verify_existing_backup)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.mhl_btn)
        btn_layout.addWidget(self.verify_backup_btn)
        btn_layout.addStretch()
        config_layout.addLayout(btn_layout)

        config_layout.addStretch()
        layout.addWidget(config_widget)

        # 下段容器：执行状态区
        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)

        # 执行状态与日志（合并进度条 + 状态标签 + 日志输出）
        status_group = QGroupBox("执行状态")
        status_layout = QVBoxLayout(status_group)

        self.status_panel = StatusPanel(progress_range=(0, 1000), log_min_height=80)
        status_layout.addWidget(self.status_panel)
        # 保留别名以减少方法体内 self.progress_bar / status_label / log_text 的改动
        self.progress_bar = self.status_panel.progress_bar
        self.status_label = self.status_panel.status_label
        self.log_text = self.status_panel.log_text
        result_layout.addWidget(status_group)

        # 备份历史：断点续传 / 失败重试入口
        history_group = QGroupBox("备份历史（可断点续传 / 重试失败文件）")
        history_layout = QVBoxLayout(history_group)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "项目", "源路径", "目标数", "状态", "失败文件"]
        )
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SingleSelection)
        self.history_table.setShowGrid(False)
        self.history_table.verticalHeader().setVisible(False)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.history_table.setMaximumHeight(150)
        self.history_table.itemSelectionChanged.connect(
            self._on_history_selection_changed
        )
        history_layout.addWidget(self.history_table)

        history_detail = QHBoxLayout()
        self.history_failed_label = QLabel(
            "失败文件：选中上方记录后显示（可断点续传 / 重试）"
        )
        self.history_failed_label.setWordWrap(True)
        self.history_failed_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
        )
        history_detail.addWidget(self.history_failed_label, 1)
        self.retry_btn = QPushButton("↻ 重试失败文件")
        self.retry_btn.setToolTip(
            "只重新拷贝选中作业中上次失败的文件到对应目标；"
            "目标中已存在且校验一致的文件自动跳过（断点续传）"
        )
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_failed_files)
        history_detail.addWidget(self.retry_btn)
        history_layout.addLayout(history_detail)
        result_layout.addWidget(history_group)

        result_widget.setMinimumHeight(300)
        layout.addWidget(result_widget, 1)

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        try:
            self.selector.refresh()
            self._load_backup_history()
            self._load_backup_templates()
        except Exception as e:
            self._log(f"刷新工作区/项目列表失败: {e}")

    def _load_backup_templates(self):
        try:
            self._backup_templates = self.db_service.get_backup_templates()
        except Exception as e:
            self._log(f"加载备份方案失败: {e}")
            self._backup_templates = []
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem("（手动配置）", None)
        for template in self._backup_templates:
            label = template.name
            if template.description:
                label += f" - {template.description}"
            self.template_combo.addItem(label, template.template_id)
        self.template_combo.blockSignals(False)
        self.delete_template_btn.setEnabled(bool(self._backup_templates))

    def _apply_selected_template(self, index: int):
        template_id = self.template_combo.itemData(index)
        if not template_id:
            return
        template = next((t for t in self._backup_templates if t.template_id == template_id), None)
        if template is None:
            return
        self._target_paths = list(template.target_paths)
        self._rebuild_target_rows()
        self.algorithm_combo.setCurrentIndex(0 if template.algorithm == ChecksumAlgorithm.XXHASH64 else 1)
        self.verify_check.setChecked(template.verify_after_copy)
        self._log(f"已应用备份方案: {template.name}")

    def _save_backup_template(self):
        if not self._target_paths:
            QMessageBox.warning(self, "提示", "请先添加至少一个备份目标")
            return
        draft = BackupTemplate(
            template_id="",
            name="",
            target_paths=list(self._target_paths),
            algorithm=(
                ChecksumAlgorithm.XXHASH64
                if self.algorithm_combo.currentIndex() == 0
                else ChecksumAlgorithm.MD5
            ),
            verify_after_copy=self.verify_check.isChecked(),
        )
        values = edit_backup_template(self, draft)
        if not values:
            return
        try:
            template = self.db_service.create_backup_template(**values)
            self._load_backup_templates()
            index = self.template_combo.findData(template.template_id)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
            self._log(f"已保存备份方案: {template.name}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存备份方案失败：{e}")

    def _delete_backup_template(self):
        template_id = self.template_combo.currentData()
        if not template_id:
            QMessageBox.information(self, "提示", "请选择要删除的备份方案")
            return
        template = next((t for t in self._backup_templates if t.template_id == template_id), None)
        if template is None:
            return
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除备份方案「{template.name}」？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if self.db_service.delete_backup_template(template_id):
            self._load_backup_templates()
            self._log(f"已删除备份方案: {template.name}")

    def _load_backup_history(self):
        """加载备份作业历史（最近 20 条），供断点续传 / 重试选择。"""
        try:
            jobs = self.db_service.get_backup_jobs()
        except Exception as e:
            self._log(f"加载备份历史失败: {e}")
            return
        self.history_table.setRowCount(0)
        status_text = {
            "completed": "✅ 完成",
            "partial": "⚠️ 部分",
            "failed": "❌ 失败",
            "running": "🔄 进行中",
        }
        for raw in jobs[:20]:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            failed_total = sum(
                len(t.get("failed_files", [])) for t in raw["targets"]
            )
            values = [
                raw["created_at"].strftime("%m-%d %H:%M"),
                raw["project_id"] or "—",
                raw["source_path"],
                str(len(raw["targets"])),
                status_text.get(raw["status"], raw["status"]),
                str(failed_total),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if col == 0:
                    item.setData(Qt.UserRole, raw["job_id"])
                self.history_table.setItem(row, col, item)

    def _on_history_selection_changed(self):
        """选中历史作业 → 显示失败文件列表并启用重试按钮。"""
        self.retry_btn.setEnabled(False)
        self.history_failed_label.setText(
            "失败文件：选中上方记录后显示（可断点续传 / 重试）"
        )
        if self.task_vm.is_running():
            return
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        job_id_item = self.history_table.item(row, 0)
        if job_id_item is None:
            return
        job_id = job_id_item.data(Qt.UserRole)
        try:
            job = self.backup_service.load_job(job_id)
        except Exception as e:
            self._log(f"加载备份作业失败 {job_id}: {e}")
            return
        if job is None:
            return
        failed = [
            f"{t.name} / {rel}"
            for t in job.targets
            for rel in t.failed_files
        ]
        if failed:
            shown = "\n".join(failed[:50])
            if len(failed) > 50:
                shown += f"\n…（共 {len(failed)} 个文件）"
            self.history_failed_label.setText(shown)
            if job.status.value in ("partial", "failed"):
                self.retry_btn.setEnabled(True)
        else:
            self.history_failed_label.setText("该作业无失败文件记录")

    @safe_slot("重试失败文件失败")
    def _retry_failed_files(self):
        """重试选中备份作业的失败文件（断点续传语义）。"""
        if self.task_vm.is_running():
            QMessageBox.warning(self, "提示", "已有后台任务正在运行，请稍候")
            return
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        job_id_item = self.history_table.item(row, 0)
        if job_id_item is None:
            return
        job_id = job_id_item.data(Qt.UserRole)
        job = self.backup_service.load_job(job_id)
        if job is None:
            QMessageBox.warning(self, "提示", "找不到该备份作业记录")
            return
        failed_total = sum(len(t.failed_files) for t in job.targets)
        detail = "\n".join(
            f"· {t.name}: {len(t.failed_files)} 个文件"
            for t in job.targets if t.failed_files
        )
        reply = QMessageBox.question(
            self, "重试失败文件",
            f"将重新拷贝该作业中失败的 {failed_total} 个文件到对应目标：\n\n"
            f"{detail}\n\n"
            "目标中已存在且校验一致的文件会自动跳过（断点续传）。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        raw = self.db_service.get_backup_job(job_id)
        project_id = raw["project_id"] if raw else None
        self._backup_project_id = project_id

        self.start_btn.setEnabled(False)
        self.retry_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("正在重试失败文件…")
        self._log(
            f"开始重试备份作业 [{job_id}]：{failed_total} 个失败文件"
            + (f"，关联项目 {project_id}" if project_id else "")
        )

        self._task_kind = "backup"
        self.task_vm.start(
            self.backup_service.retry_failed_files,
            job_id,
            project_id=project_id,
            verify=self.verify_check.isChecked(),
            inject_progress=True,
            inject_file_completed=True,
        )

    def _pick_directory(self, title: str, category: str = "default") -> str:
        """统一的目录选择对话框（委托给共享工具函数，处理打包兼容）。"""
        return pick_directory(self, title, category=category)

    def _select_source(self):
        path = self._pick_directory("选择存储卡路径", category="backup_source")
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
        path = self._pick_directory("选择备份目标路径", category="backup_target")
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
            row.setStyleSheet(f"""
                QWidget {{
                    background-color: {COLOR.BG_APP};
                    border-radius: {RADIUS.ROW}px;
                }}
            """)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 4, 4)
            row_layout.setSpacing(8)

            idx_label = QLabel(f"{i + 1}.")
            idx_label.setFixedWidth(24)
            idx_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-weight: bold; background: transparent;")
            row_layout.addWidget(idx_label)

            path_label = QLabel(path)
            path_label.setToolTip(path)
            path_label.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY}; background: transparent;")
            path_label.setWordWrap(False)
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row_layout.addWidget(path_label, 1)

            # 显示该目标的磁盘剩余空间（预检提示）
            try:
                usage = shutil.disk_usage(path)
                space_text = f"剩余 {format_size(usage.free)}"
            except Exception:
                space_text = "剩余 —"
            space_label = QLabel(space_text)
            space_label.setToolTip("备份前会校验目标剩余空间是否足够")
            space_label.setStyleSheet(
                f"color: {COLOR.TEXT_SECONDARY}; background: transparent; font-size: {FONT_SIZE.SM}px;"
            )
            row_layout.addWidget(space_label)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(24, 24)
            del_btn.setToolTip("移除此目标")
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR.BORDER_LIGHT};
                    border: none;
                    border-radius: {RADIUS.ROW}px;
                    color: {COLOR.TEXT_SECONDARY};
                    font-size: {FONT_SIZE.SM}px;
                }}
                QPushButton:hover {{ background-color: {COLOR.DANGER_HOVER}; color: white; }}
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

        # 覆盖确认：扫描源目录与各目标目录的同名文件冲突
        try:
            from pathlib import Path as _Path
            src_path = _Path(source)
            if src_path.is_dir():
                source_file_names = [p.name for p in src_path.iterdir() if p.is_file()]
                conflicts = find_overwrite_conflicts(source_file_names, targets)
                if conflicts:
                    detail_lines = []
                    total = 0
                    for t_dir, names in conflicts.items():
                        total += len(names)
                        preview = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
                        detail_lines.append(f"  · {t_dir}（{len(names)} 个）: {preview}")
                    detail = "\n".join(detail_lines)
                    reply = QMessageBox.question(
                        self, "存在同名文件",
                        f"以下目标目录中已存在 {total} 个同名文件，继续备份将覆盖：\n\n{detail}\n\n是否继续？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        self._log("用户取消备份：存在同名文件冲突")
                        return
        except Exception as e:
            # 冲突检测失败不阻塞备份流程，仅记日志
            self._log(f"（警告）覆盖冲突检测失败: {e}")

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

        # 磁盘空间预检：任一目标剩余空间不足则拒绝启动
        space_results = self.backup_service.check_target_space(
            targets, self.current_job.total_bytes
        )
        for r in space_results:
            state = "充足" if r["sufficient"] else "不足"
            self._log(
                f"  空间预检 {r['path']}: 需要 {format_size(r['needed'])}，"
                f"剩余 {format_size(r['free'])}（{state}）"
            )
        insufficient = [r for r in space_results if not r["sufficient"]]
        if insufficient:
            detail_lines = []
            for r in insufficient:
                error_note = f"（{r['error']}）" if r.get("error") else ""
                detail_lines.append(
                    f"  · {r['path']}: 需要 {format_size(r['needed'])}，"
                    f"剩余 {format_size(r['free'])} {error_note}"
                )
            QMessageBox.warning(
                self, "目标磁盘空间不足",
                f"以下 {len(insufficient)} 个备份目标剩余空间不足，已取消备份：\n\n"
                + "\n".join(detail_lines)
                + "\n\n请更换目标磁盘或清理空间后重试。"
            )
            self._log("备份已取消：目标磁盘空间不足")
            return

        # 启动后台线程
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)

        # 显式契约：声明注入 progress / file_completed 信号转发回调，
        # 由 WorkerThread 在 run() 中以关键字参数注入 execute_backup
        self._task_kind = "backup"
        self.task_vm.start(
            self.backup_service.execute_backup,
            self.current_job,
            project_id=project_id,
            verify=self.verify_check.isChecked(),
            inject_progress=True,
            inject_file_completed=True,
        )
        self.mhl_btn.setEnabled(True)

    @safe_slot("校验已有备份失败")
    def _verify_existing_backup(self):
        """独立校验已关联项目素材的备份完整性（存在性 + 校验和比对）。"""
        project_id = self.selector.get_current_project_id()
        if not project_id:
            QMessageBox.warning(
                self, "提示",
                "请先在「关联项目」中选择要校验的项目。\n"
                "仅当备份时关联了项目，素材的备份位置才会被记录。"
            )
            return
        if self.task_vm.is_running():
            QMessageBox.warning(self, "提示", "已有后台任务正在运行，请稍候")
            return

        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.verify_backup_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("正在校验已有备份…")
        self._log(f"开始校验项目备份完整性: {project_id}")

        self._task_kind = "verify"
        self.task_vm.start(
            self.backup_service.verify_backup,
            project_id,
            progress_callback=lambda cur, tot, msg: self._verify_progress.emit(cur, tot, msg),
            inject_cancel_check=True,
        )

    @Slot(int, int, str)
    def _on_verify_progress(self, current: int, total: int, message: str):
        if total > 0:
            self.progress_bar.setValue(int(current / total * 1000))
        self.status_label.setText(message)

    @Slot(object)
    def _on_verify_finished(self, stats):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.verify_backup_btn.setEnabled(True)
        self.progress_bar.setValue(1000)
        self.status_label.setText("✅ 备份校验完成")
        self._log(
            f"备份校验完成: 检查 {stats['checked']}，一致 {stats['matched']}，"
            f"不一致 {stats['mismatch']}，缺失 {stats['missing']}"
        )

        parts = [
            f"共检查 {stats['checked']} 个备份文件",
            f"✅ 校验和一致：{stats['matched']}",
            f"❌ 校验和不一致：{stats['mismatch']}",
            f"🕳 文件缺失：{stats['missing']}",
            f"ℹ️ 无校验和（仅检查存在）：{stats['unhashable']}",
        ]
        if stats["errors"]:
            parts.append(f"⚠️ 其他异常：{len(stats['errors'])} 条")
        QMessageBox.information(self, "备份校验结果", "\n".join(parts))

    @safe_slot("导出 MHL 失败")
    def _export_mhl(self):
        """导出当前备份作业的 ASC MHL 校验清单（XML）。"""
        if not self.current_job:
            return
        default_name = f"MHL_{self.current_job.job_id}.xml"
        path = pick_save_file(
            self, "导出 MHL 校验清单", default_name,
            "MHL 文件 (*.xml);;所有文件 (*)"
        )
        if not path:
            return
        content = self.backup_service.generate_mhl_report(self.current_job)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self._log(f"✅ MHL 校验清单已导出: {path}")

    def _cancel_backup(self):
        self.task_vm.cancel()
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
        # worker 已连 deleteLater，这里清空引用避免悬挂

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

        # 失败/部分完成时，历史区提供断点续传入口
        if job.status.value in ("partial", "failed"):
            self._log(
                "提示：可在下方「备份历史」选中该作业，点击「重试失败文件」"
                "只补拷失败的文件"
            )

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
                get_data_bus().emit_data_changed("assets_changed")
                self._log("已通知其他视图刷新素材备份位置信息")
            except Exception as e:
                self._log(f"通知其他视图失败（不影响备份结果）: {e}")

        # 备份完成提示，提供跳转拍摄日志的快捷入口
        if job.status.value in ("completed", "partial"):
            status_text = "所有目标验证通过" if job.status.value == "completed" else "部分目标未完成，请检查失败目标"
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setWindowTitle("备份完成")
            msg_box.setText(f"备份完成！\n\n{status_text}")
            goto_log_btn = msg_box.addButton("去拍摄日志", QMessageBox.AcceptRole)
            ok_btn = msg_box.addButton("确定", QMessageBox.RejectRole)
            msg_box.setDefaultButton(ok_btn)
            msg_box.exec()
            if msg_box.clickedButton() is goto_log_btn:
                try:
                    main_window = self.window()
                    if hasattr(main_window, 'nav_list'):
                        main_window.nav_list.setCurrentRow(get_nav_index("log"))
                except Exception as e:
                    logger.warning(f"跳转日志视图失败: {e}")

        # 刷新备份历史表（含失败文件与最新状态）
        try:
            self._load_backup_history()
        except Exception as e:
            self._log(f"刷新备份历史失败: {e}")

    @Slot(str)
    def _on_error(self, error: str):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.verify_backup_btn.setEnabled(True)
        self.status_label.setText(f"❌ 错误: {error}")
        self._log(f"错误: {error}")
        show_error(
            title="备份错误",
            description=error,
            details=error,
            parent=self,
        )

    @Slot(object)
    def _on_task_finished(self, result):
        """统一任务完成入口，避免每次启动任务重复连接信号。"""
        task_kind = self._task_kind
        self._task_kind = None
        if task_kind == "verify":
            self._on_verify_finished(result)
        elif task_kind == "backup":
            self._on_finished(result)

    @Slot(str)
    def _on_task_error(self, error: str):
        """统一任务错误入口，避免重复弹出错误提示。"""
        self._task_kind = None
        self._on_error(error)

    def _log(self, message: str):
        self.status_panel.log(message)
