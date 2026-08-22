"""从备份恢复素材向导对话框。

引导用户选择：
1. 项目（备选所有项目）
2. 备份位置（可选从哪个备份卷恢复，默认全部）
3. 目标目录
4. 是否开启校验和验证
5. 确认后执行恢复，显示进度/结果。
"""
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from DITWorkstation.Services.backup_restore_service import BackupRestoreService
from DITWorkstation.Utils import get_db_service, logger, pick_directory
from DITWorkstation.Views.Styles.theme import COLOR


class _ProgressBridge(QObject):
    """把工作线程里的进度回调安全地桥接到 GUI 线程。

    WorkerThread 在后台线程中直接调用 restore_project 的 progress_callback，
    而 Qt 控件只能在主线程更新。这里用 Qt 信号机制转发：emit 发生在工作线程，
    由于接收槽位于主线程，Qt 会以 QueuedConnection 把更新投递到事件循环，
    保证 UI 更新始终在主线程执行。
    """
    progress = Signal(int, int, str)  # current, total, message

    def __call__(self, current: int, total: int, message: str) -> None:
        self.progress.emit(current, total, message)


class RestoreWizard(QDialog):
    """从备份恢复素材向导。"""

    def __init__(self, parent=None, db_service=None):
        super().__init__(parent)
        self.setWindowTitle("从备份恢复素材")
        self.resize(600, 520)
        self.db_service = db_service or get_db_service()
        self._step = 0
        self._setup_ui()

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(12)

        title = QLabel("从备份恢复素材")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        main.addWidget(title)

        desc = QLabel("选择项目、备份位置与目标目录，应用会从备份卷将素材复制回工作盘。")
        desc.setWordWrap(True)
        main.addWidget(desc)

        # 项目选择
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(300)
        try:
            for proj in self.db_service.get_projects():
                self.project_combo.addItem(proj.name, proj.project_id)
        except Exception as e:
            logger.warning("加载项目列表失败: %s", e)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        main.addWidget(QLabel("选择项目:"))
        main.addWidget(self.project_combo)

        # 备份位置
        self.loc_combo = QComboBox()
        self.loc_combo.addItem("全部备份位置", "")
        self.loc_combo.setMinimumWidth(300)
        main.addWidget(QLabel("备份来源（可选）:"))
        main.addWidget(self.loc_combo)

        # 目标目录
        dest_row = QHBoxLayout()
        self.dest_path = QLabel("（未选择）")
        self.dest_path.setStyleSheet("color: gray;")
        dest_row.addWidget(self.dest_path, 1)
        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.browse_btn)
        main.addWidget(QLabel("恢复目标目录:"))
        main.addLayout(dest_row)

        # 校验选项
        self.verify_check = QCheckBox("校验和验证（推荐）")
        self.verify_check.setChecked(True)
        main.addWidget(self.verify_check)

        # 操作按钮
        btn_row = QHBoxLayout()
        self.restore_btn = QPushButton("开始恢复")
        self.restore_btn.setStyleSheet(f"background: {COLOR.PRIMARY}; color: white; padding: 8px 20px; border-radius: 6px; font-weight: bold;")
        self.restore_btn.clicked.connect(self._start_restore)
        self.restore_btn.setEnabled(False)
        btn_row.addStretch()
        btn_row.addWidget(self.restore_btn)
        self.cancel_btn = QPushButton("关闭")
        self.cancel_btn.clicked.connect(self.close)
        btn_row.addWidget(self.cancel_btn)
        main.addLayout(btn_row)

        # 进度 + 日志
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        main.addWidget(self.progress)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(200)
        self.log_output.setVisible(False)
        main.addWidget(self.log_output, 1)

        self._on_project_changed(0)

    def _on_project_changed(self, idx):
        """切换项目时刷新备份位置下拉。"""
        self.loc_combo.clear()
        self.loc_combo.addItem("全部备份位置", "")
        project_id = self.project_combo.currentData()
        if not project_id:
            return
        seen = set()
        try:
            for asset in self.db_service.iter_project_assets(project_id):
                for loc in (asset.backup_locations or []):
                    if loc not in seen:
                        seen.add(loc)
                        self.loc_combo.addItem(loc, loc)
        except Exception as e:
            logger.warning("加载备份位置失败: %s", e)
        self.restore_btn.setEnabled(True)

    def _browse_dest(self):
        path = pick_directory(self, "选择恢复目标目录")
        if path:
            self.dest_path.setText(path)

    def _start_restore(self):
        project_id = self.project_combo.currentData()
        source_loc = self.loc_combo.currentData() or None
        dest = self.dest_path.text().strip()
        if not project_id or dest in ("", "（未选择）"):
            QMessageBox.warning(self, "参数不完整", "请选择项目并指定恢复目标目录。")
            return
        self.restore_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log_output.setVisible(True)
        self.log_output.clear()
        self.log_output.append("开始恢复...")

        from DITWorkstation.Utils.workers import WorkerThread
        restore_service = BackupRestoreService(self.db_service)

        # 线程安全桥接：进度回调在工作线程触发，UI 更新经信号投递到主线程
        self._progress_bridge = _ProgressBridge(self)
        self._progress_bridge.progress.connect(self._on_progress)

        self._worker = WorkerThread(
            restore_service.restore_project,
            project_id, dest,
            source_locations=[source_loc] if source_loc else None,
            verify=self.verify_check.isChecked(),
            progress_callback=self._progress_bridge,
        )
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.thread_finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_progress(self, current: int, total: int, message: str):
        """主线程中更新进度条与日志（由 _ProgressBridge 信号投递）。"""
        if total > 0:
            self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.log_output.append(message)

    def _on_finished(self, stats):
        if isinstance(stats, dict):
            self.log_output.append("恢复完成！")
            for k, v in stats.items():
                if isinstance(v, int):
                    self.log_output.append(f"  {k}: {v}")
                elif isinstance(v, list) and v:
                    self.log_output.append(f"  {k}: {len(v)} 项")
        self.restore_btn.setEnabled(True)

    def _on_error(self, error):
        self.log_output.append(f"恢复失败: {error}")
        self.restore_btn.setEnabled(True)
