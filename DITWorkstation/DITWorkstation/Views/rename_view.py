"""文件重命名页面"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QGroupBox, QFormLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QSpinBox, QComboBox, QProgressBar
)
from PySide6.QtCore import Slot
from pathlib import Path

from DITWorkstation.Models import RenameRule
from DITWorkstation.Services.rename_service import RenameService
from DITWorkstation.Utils import WorkerThread, safe_slot, get_db_service, logger


class RenameView(QWidget):
    """文件重命名视图"""

    def __init__(self):
        super().__init__()
        self.rename_service = RenameService()
        # 共享 db_service 单例，用于重命名后同步 DB 中 asset 的 file_path/file_name
        self.db_service = get_db_service()
        self.selected_files = []
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("文件重命名")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        layout.addWidget(title)

        subtitle = QLabel("按场景/镜头/镜次规则批量重命名，保持文件关联关系")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
        layout.addWidget(subtitle)

        # 文件选择
        file_group = QGroupBox("选择文件")
        file_layout = QHBoxLayout(file_group)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("选择包含素材的文件夹...")
        self.folder_edit.setReadOnly(True)
        folder_btn = QPushButton("浏览...")
        folder_btn.clicked.connect(self._select_folder)
        file_layout.addWidget(self.folder_edit, 1)
        file_layout.addWidget(folder_btn)
        layout.addWidget(file_group)

        # 命名规则
        rule_group = QGroupBox("命名规则")
        rule_layout = QFormLayout(rule_group)

        self.pattern_combo = QComboBox()
        self.pattern_combo.addItems([
            "{scene}_{shot}_{take}_{number}",
            "{scene}_{shot}_{take}_{original}",
            "{prefix}_{scene}_{number}",
            "{date}_{scene}_{shot}_{number}",
        ])
        self.pattern_combo.setEditable(True)
        rule_layout.addRow("命名模板:", self.pattern_combo)

        name_row = QHBoxLayout()
        self.scene_edit = QLineEdit()
        self.scene_edit.setPlaceholderText("如: S001")
        self.shot_edit = QLineEdit()
        self.shot_edit.setPlaceholderText("如: 001A")
        self.take_edit = QLineEdit()
        self.take_edit.setPlaceholderText("如: 01")
        name_row.addWidget(QLabel("场景:"))
        name_row.addWidget(self.scene_edit)
        name_row.addWidget(QLabel("镜头:"))
        name_row.addWidget(self.shot_edit)
        name_row.addWidget(QLabel("镜次:"))
        name_row.addWidget(self.take_edit)
        rule_layout.addRow("", name_row)

        opt_row = QHBoxLayout()
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("前缀（可选）")
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 99999)
        self.start_spin.setValue(1)
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(1, 6)
        self.padding_spin.setValue(3)
        opt_row.addWidget(QLabel("前缀:"))
        opt_row.addWidget(self.prefix_edit)
        opt_row.addWidget(QLabel("起始序号:"))
        opt_row.addWidget(self.start_spin)
        opt_row.addWidget(QLabel("序号位数:"))
        opt_row.addWidget(self.padding_spin)
        rule_layout.addRow("", opt_row)

        layout.addWidget(rule_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.preview_btn = QPushButton("预览")
        self.preview_btn.clicked.connect(self._preview)
        self.rename_btn = QPushButton("执行重命名")
        self.rename_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9500;
                color: white;
                padding: 10px 24px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #e68600; }
            QPushButton:disabled { background-color: #cccccc; }
        """)
        self.rename_btn.clicked.connect(self._execute_rename)
        self.rename_btn.setEnabled(False)
        btn_layout.addWidget(self.preview_btn)
        btn_layout.addWidget(self.rename_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 预览表格
        preview_group = QGroupBox("预览结果")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(["原文件名", "新文件名"])
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        preview_layout.addWidget(self.preview_table)
        layout.addWidget(preview_group, 1)

        # 进度条（重命名执行时显示）
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

    @safe_slot("选择文件夹失败")
    def _select_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if path:
            self.folder_edit.setText(path)
            self._scan_folder(path)

    def _scan_folder(self, path: str):
        """扫描文件夹并填充待重命名文件列表"""
        folder = Path(path)
        self.selected_files = [
            str(f) for f in sorted(folder.iterdir())
            if f.is_file() and not f.name.startswith(".")
        ]
        self.rename_btn.setEnabled(len(self.selected_files) > 0)
        self._preview()

    def _get_rule(self) -> RenameRule:
        return RenameRule(
            pattern=self.pattern_combo.currentText(),
            scene=self.scene_edit.text(),
            shot=self.shot_edit.text(),
            take=self.take_edit.text(),
            prefix=self.prefix_edit.text(),
            start_number=self.start_spin.value(),
            padding=self.padding_spin.value()
        )

    @safe_slot("预览失败")
    def _preview(self):
        if not self.selected_files:
            QMessageBox.warning(self, "提示", "请先选择文件夹")
            return

        rule = self._get_rule()
        pairs = self.rename_service.preview_rename(self.selected_files, rule)

        self.preview_table.setRowCount(len(pairs))
        for i, (old, new) in enumerate(pairs):
            self.preview_table.setItem(i, 0, QTableWidgetItem(Path(old).name))
            self.preview_table.setItem(i, 1, QTableWidgetItem(Path(new).name))

    @safe_slot("重命名失败")
    def _execute_rename(self):
        if not self.selected_files:
            return

        # 防止重复启动
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行重命名，请稍候。")
            return

        reply = QMessageBox.question(
            self, "确认重命名",
            f"确定要重命名 {len(self.selected_files)} 个文件吗？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        rule = self._get_rule()
        # 禁用按钮，显示进度
        self.rename_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 不确定进度

        # 后台线程执行，避免阻塞主线程
        self._worker = WorkerThread(
            self.rename_service.execute_rename, self.selected_files, rule
        )
        self._worker.finished.connect(self._on_rename_finished)
        self._worker.error.connect(self._on_rename_error)
        self._worker.start()

    @Slot(object)
    def _on_rename_finished(self, results):
        self._restore_ui()

        # 数据闭环：把重命名结果回写到 DB 的 media_assets.file_path/file_name
        synced = 0
        unmatched = 0
        for old_path, new_path in results:
            try:
                new_name = Path(new_path).name
                ok = self.db_service.update_asset_path_by_old_path(
                    old_path, new_path, new_name
                )
                if ok:
                    synced += 1
                else:
                    unmatched += 1
            except Exception as e:
                logger.error(f"同步重命名到 DB 失败 {old_path}: {e}")

        msg = f"成功重命名 {len(results)} 个文件"
        if synced or unmatched:
            msg += f"\n\n数据库同步：{synced} 个已入库素材路径已更新"
            if unmatched:
                msg += f"，{unmatched} 个未入库（仅文件系统重命名）"
        QMessageBox.information(self, "重命名完成", msg)

        # 广播 assets_changed，让 asset_info_view / search_view 刷新路径
        if synced > 0:
            try:
                from DITWorkstation.Views.main_window import get_data_bus
                get_data_bus().emit_data_changed("assets_changed")
            except Exception as e:
                logger.error(f"广播重命名完成事件失败: {e}")

        # 刷新文件列表（静默重扫）
        path = self.folder_edit.text()
        if path:
            self._scan_folder(path)

    @Slot(str)
    def _on_rename_error(self, error: str):
        self._restore_ui()
        QMessageBox.critical(self, "重命名出错", error)

    def _restore_ui(self):
        """重命名结束后恢复按钮与进度条状态"""
        self.rename_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
