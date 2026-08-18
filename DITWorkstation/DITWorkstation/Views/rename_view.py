"""文件重命名页面"""
from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from DITWorkstation.App.session_context import get_data_bus
from DITWorkstation.Models import RenameRule
from DITWorkstation.Services.rename_service import RenameService
from DITWorkstation.Utils import get_db_service, logger, pick_directory, safe_slot
from DITWorkstation.ViewModels import TaskViewModel
from DITWorkstation.Views.Styles.theme import (
    PRIMARY_BUTTON_QSS,
    SUBTITLE_QSS,
    TITLE_QSS,
)
from DITWorkstation.Views.Widgets.empty_state import (
    attach_empty_state,
    sync_empty_state,
)
from DITWorkstation.Views.Widgets.error_dialog import show_error
from DITWorkstation.Views.Widgets.status_panel import StatusPanel
from DITWorkstation.Views.Widgets.table_factory import make_table


class RenameView(QWidget):
    """文件重命名视图"""

    # 跨线程进度信号（current, total, filename），在工作线程发射、主线程消费
    _progress_sig = Signal(int, int, str)

    def __init__(self):
        super().__init__()
        self.rename_service = RenameService()
        # 共享 db_service 单例，用于重命名后同步 DB 中 asset 的 file_path/file_name
        self.db_service = get_db_service()
        self.selected_files = []
        self.task_vm = TaskViewModel(self, task_store=self.db_service)
        self.task_vm.finished.connect(self._on_task_finished)
        self.task_vm.error.connect(self._on_rename_error)
        self._task_kind = None
        self._last_rename_id = None
        self._setup_ui()
        self._progress_sig.connect(self._on_progress)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("文件重命名")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel("按场景/镜头/镜次规则批量重命名，保持文件关联关系")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # 上下两段：上段配置区，下段结果区（不使用可拖动分割条，
        # 空间不足时由整个视图的外层滚动条滚动）
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(16)

        # 文件选择
        file_group = QGroupBox("选择文件")
        file_layout = QHBoxLayout(file_group)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("选择包含素材的文件夹...")
        self.folder_edit.setReadOnly(True)
        folder_btn = QPushButton("浏览…")
        folder_btn.clicked.connect(self._select_folder)
        file_layout.addWidget(self.folder_edit, 1)
        file_layout.addWidget(folder_btn)
        config_layout.addWidget(file_group)

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
        self.pattern_combo.setToolTip(
            "命名模板支持以下变量：\n"
            "• {scene} - 场景编号（如 S001）\n"
            "• {shot} - 镜头编号（如 001A）\n"
            "• {take} - 镜次（如 01）\n"
            "• {number} - 序号（从起始值递增）\n"
            "• {date} - 拍摄日期\n"
            "• {camera} - 相机型号\n"
            "示例：{scene}_{shot}_{take}_{number} → S001_001A_01_0001"
        )
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

        config_layout.addWidget(rule_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.preview_btn = QPushButton("预览")
        self.preview_btn.setToolTip("预览重命名结果")
        self.preview_btn.clicked.connect(self._preview)
        self.rename_btn = QPushButton("执行重命名")
        self.rename_btn.setToolTip("执行批量重命名")
        self.rename_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        self.rename_btn.clicked.connect(self._execute_rename)
        self.rename_btn.setEnabled(False)
        self.undo_btn = QPushButton("↩ 回退上次重命名")
        self.undo_btn.setToolTip("仅在文件未被后续修改且原路径未被占用时可安全回退")
        self.undo_btn.clicked.connect(self._undo_last_rename)
        self.undo_btn.setEnabled(False)
        btn_layout.addWidget(self.preview_btn)
        btn_layout.addWidget(self.rename_btn)
        btn_layout.addWidget(self.undo_btn)
        btn_layout.addStretch()
        config_layout.addLayout(btn_layout)
        config_layout.addStretch()
        layout.addWidget(config_widget)

        # 下段：结果区
        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)

        # 预览表格
        preview_group = QGroupBox("预览结果")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_table = make_table(["原文件名", "新文件名"])
        preview_layout.addWidget(self.preview_table)
        attach_empty_state(self.preview_table, "📝", "暂无预览", "选择文件夹并设置规则后点击「预览」")
        result_layout.addWidget(preview_group)

        # 进度条（重命名执行时显示）
        self.status_panel = StatusPanel(show_log=False, status_text="")
        result_layout.addWidget(self.status_panel)
        # 保留别名以减少方法体内 self.progress_bar / status_label 的改动
        self.progress_bar = self.status_panel.progress_bar
        self.status_label = self.status_panel.status_label
        # 默认隐藏，执行重命名时再显示
        self.status_panel.setVisible(False)
        self.progress_bar.setVisible(False)
        self.status_label.setVisible(False)

        result_widget.setMinimumHeight(260)
        layout.addWidget(result_widget, 1)

    @safe_slot("选择文件夹失败")
    def _select_folder(self):
        path = pick_directory(self, "选择素材文件夹", category="rename_source")
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
        sync_empty_state(self.preview_table)
        for i, (old, new) in enumerate(pairs):
            self.preview_table.setItem(i, 0, QTableWidgetItem(Path(old).name))
            self.preview_table.setItem(i, 1, QTableWidgetItem(Path(new).name))

    @safe_slot("重命名失败")
    def _execute_rename(self):
        if not self.selected_files:
            return

        # 防止重复启动
        if self.task_vm.is_running():
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
        self.status_panel.setVisible(True)
        self.progress_bar.setVisible(True)
        self.status_label.setVisible(True)
        self.progress_bar.setRange(0, 100)  # 确定模式，按百分比推进
        self.progress_bar.setValue(0)
        self.status_label.setText("正在重命名...")

        # 后台线程执行，避免阻塞主线程
        # 通过 lambda 将服务回调桥接到跨线程信号（current, total, filename）
        self._task_kind = "rename"
        self.task_vm.start(
            self.rename_service.execute_rename,
            self.selected_files,
            rule,
            progress_callback=lambda c, t, f: self._progress_sig.emit(c, t, f),
            task_name="批量重命名",
            recovery_info={"folder": self.folder_edit.text(), "file_count": len(self.selected_files)},
        )

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, filename: str):
        """更新进度条与状态文本（current 从1开始）"""
        percent = int(current * 100 / total) if total > 0 else 0
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"正在重命名 ({current}/{total}): {filename}")

    @Slot(object)
    def _on_rename_finished(self, results):
        self.progress_bar.setValue(100)
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

        # 持久化映射；回退会先确认全部文件状态再执行，避免覆盖后续变更。
        try:
            history = self.db_service.create_rename_history(results)
            if history:
                self._last_rename_id = history.recovery_id
                self.undo_btn.setEnabled(True)
        except Exception as e:
            logger.warning(f"保存重命名回退记录失败: {e}")

        # 广播 assets_changed，让 asset_info_view / search_view 刷新路径
        if synced > 0:
            try:
                get_data_bus().emit_data_changed("assets_changed")
            except Exception as e:
                logger.error(f"广播重命名完成事件失败: {e}")

        # 刷新文件列表（静默重扫）
        path = self.folder_edit.text()
        if path:
            self._scan_folder(path)

    @safe_slot("回退重命名失败")
    def _undo_last_rename(self):
        if not self._last_rename_id:
            return
        reply = QMessageBox.question(
            self, "确认回退",
            "将回退上一次重命名。若文件已被后续修改或原路径被占用，操作会安全取消。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.undo_btn.setEnabled(False)
        self._task_kind = "undo"
        self.task_vm.start(
            self.rename_service.rollback_rename, self.db_service, self._last_rename_id,
            task_name="回退重命名",
            recovery_info={"rename_id": self._last_rename_id},
        )

    @Slot(object)
    def _on_task_finished(self, result):
        kind, self._task_kind = self._task_kind, None
        if kind == "rename":
            self._on_rename_finished(result)
        elif kind == "undo":
            self._on_undo_finished(result)

    @Slot(object)
    def _on_undo_finished(self, result):
        if not result:
            QMessageBox.warning(self, "无法回退", result.message)
            self.undo_btn.setEnabled(True)
            return
        QMessageBox.information(self, "回退完成", f"已回退 {result.affected_count} 个文件。")
        self._last_rename_id = None
        try:
            get_data_bus().emit_data_changed("assets_changed")
        except Exception as e:
            logger.warning(f"广播重命名回退事件失败: {e}")
        path = self.folder_edit.text()
        if path:
            self._scan_folder(path)

    @Slot(str)
    def _on_rename_error(self, error: str):
        self._task_kind = None
        self._restore_ui()
        show_error(
            title="重命名出错",
            description=error,
            details=error,
            parent=self,
        )

    def _restore_ui(self):
        """重命名结束后恢复按钮与进度条状态"""
        self.rename_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.status_panel.setVisible(False)
        self.progress_bar.setVisible(False)
        self.status_label.setVisible(False)
