"""后台任务历史中心。"""
import json

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from DITWorkstation.App import config
from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Utils import get_checksum_service, get_db_service, safe_slot
from DITWorkstation.ViewModels import TaskViewModel
from DITWorkstation.Views.Styles.theme import SECONDARY_BUTTON_QSS

_STATE_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "cancelling": "取消中",
    "cancelled": "已取消",
    "completed": "已完成",
    "failed": "失败",
    "recoverable": "可恢复",
}


class TaskHistoryDialog(QDialog):
    """查看后台任务历史，并重试带备份作业上下文的失败任务。"""

    def __init__(self, parent=None, db_service=None):
        super().__init__(parent)
        self.db_service = db_service or get_db_service()
        checksum_service = (
            get_checksum_service()
            if db_service is None else ChecksumService(db_service=self.db_service)
        )
        self.backup_service = BackupService(
            db_service=self.db_service, checksum_service=checksum_service,
        )
        self.task_vm = TaskViewModel(self, task_store=self.db_service)
        self.task_vm.progress.connect(self._on_retry_progress)
        self.task_vm.finished.connect(self._on_retry_finished)
        self.task_vm.error.connect(self._on_retry_error)
        self._records: list[dict] = []
        self._setup_ui()
        self._reload()

    def _setup_ui(self):
        self.setWindowTitle("任务中心")
        self.resize(980, 620)
        layout = QVBoxLayout(self)

        filters = QHBoxLayout()
        self.project_filter = QComboBox()
        self.project_filter.addItem("全部项目", None)
        for project in self.db_service.get_projects():
            self.project_filter.addItem(project.name, project.project_id)
        self.state_filter = QComboBox()
        self.state_filter.addItem("全部状态", "")
        for state, label in _STATE_LABELS.items():
            self.state_filter.addItem(label, state)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setStyleSheet(SECONDARY_BUTTON_QSS)
        self.refresh_btn.clicked.connect(self._reload)
        filters.addWidget(QLabel("项目:"))
        filters.addWidget(self.project_filter)
        filters.addWidget(QLabel("状态:"))
        filters.addWidget(self.state_filter)
        filters.addWidget(self.refresh_btn)
        filters.addStretch()
        layout.addLayout(filters)

        self.task_table = QTableWidget(0, 6)
        self.task_table.setHorizontalHeaderLabels(
            ["创建时间", "任务", "项目", "状态", "完成时间", "错误摘要"]
        )
        self.task_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.task_table.setSelectionMode(QTableWidget.SingleSelection)
        self.task_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.task_table.verticalHeader().setVisible(False)
        header = self.task_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.task_table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.task_table, 1)

        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        self.detail_edit.setPlaceholderText("选择任务查看参数、错误和恢复信息")
        self.detail_edit.setMaximumHeight(150)
        layout.addWidget(self.detail_edit)

        actions = QHBoxLayout()
        self.retry_btn = QPushButton("重试失败备份")
        self.retry_btn.setToolTip("重试该任务关联备份作业中的失败文件")
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_selected)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(self.retry_btn)
        actions.addStretch()
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    @safe_slot("读取任务历史失败")
    def _reload(self):
        state = self.state_filter.currentData() or ""
        project_id = self.project_filter.currentData()
        records = self.db_service.get_task_history(project_id, limit=500)
        self._records = [record for record in records if not state or record.get("state") == state]
        project_names = {project.project_id: project.name for project in self.db_service.get_projects()}
        self.task_table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            values = [
                self._format_datetime(record.get("created_at")),
                record.get("task_name") or "",
                project_names.get(record.get("project_id"), record.get("project_id") or "—"),
                _STATE_LABELS.get(record.get("state"), record.get("state") or ""),
                self._format_datetime(record.get("completed_at")),
                record.get("error_summary") or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record)
                self.task_table.setItem(row, column, item)
        self.detail_edit.clear()
        self.retry_btn.setEnabled(False)

    @staticmethod
    def _format_datetime(value) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else str(value or "")

    def _selected_record(self) -> dict | None:
        row = self.task_table.currentRow()
        if row < 0 or row >= len(self._records):
            return None
        return self._records[row]

    @Slot()
    def _on_selection_changed(self):
        record = self._selected_record()
        if not record:
            self.detail_edit.clear()
            self.retry_btn.setEnabled(False)
            return
        detail = {
            "任务 ID": record.get("task_id"),
            "参数": record.get("parameters", {}),
            "恢复信息": record.get("recovery", {}),
            "输出": record.get("output", {}),
            "错误": record.get("error_summary") or "",
        }
        self.detail_edit.setPlainText(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
        self.retry_btn.setEnabled(self._retry_job_id(record) is not None)

    @staticmethod
    def _retry_job_id(record: dict) -> str | None:
        if record.get("state") not in {"failed", "recoverable", "cancelled"}:
            return None
        recovery = record.get("recovery") or {}
        job_id = recovery.get("job_id")
        if job_id and "备份" in str(record.get("task_name") or ""):
            return str(job_id)
        return None

    @safe_slot("重试备份任务失败")
    def _retry_selected(self):
        record = self._selected_record()
        job_id = self._retry_job_id(record or {})
        if not job_id:
            return
        if self.task_vm.is_running():
            QMessageBox.information(self, "提示", "已有任务正在重试，请稍候")
            return
        self.retry_btn.setEnabled(False)
        started = self.task_vm.start(
            self.backup_service.retry_failed_files,
            job_id,
            project_id=record.get("project_id"),
            task_name="重试备份",
            recovery_info={"job_id": job_id},
            verify=config.verify_after_copy,
            inject_progress=True,
            inject_file_completed=True,
        )
        if not started:
            self.retry_btn.setEnabled(True)
            return
        self.detail_edit.setPlainText(f"正在重试备份作业：{job_id}")

    def _on_retry_progress(self, _target: str, progress: float, message: str):
        self.detail_edit.setPlainText(f"{message}\n进度：{int(progress * 100)}%")

    def _on_retry_finished(self, _result):
        self._reload()
        self.detail_edit.setPlainText("备份重试已完成，列表已刷新。")

    def _on_retry_error(self, error: str):
        self.retry_btn.setEnabled(True)
        self.detail_edit.setPlainText(f"备份重试失败：{error}")

    def closeEvent(self, event):
        if self.task_vm.is_running():
            QMessageBox.information(self, "提示", "任务正在运行，请等待完成后再关闭任务中心")
            event.ignore()
            return
        super().closeEvent(event)
