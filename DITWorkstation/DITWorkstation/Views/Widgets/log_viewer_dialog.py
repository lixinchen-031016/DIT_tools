"""日志查看器对话框：同时支持查看系统日志文件与应用操作审计日志。"""
import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView, QComboBox,
    QLineEdit, QFileDialog,
)

from DITWorkstation.App import config
from DITWorkstation.Utils import logger, format_size, get_db_service
from DITWorkstation.Views.Styles.theme import (
    COLOR, FONT_SIZE, MONO_FONT_QSS, SECONDARY_BUTTON_QSS,
)

# 操作结果状态 → 友好文案
_STATUS_LABELS = {
    "success": "成功",
    "not_found": "未找到",
    "conflict": "冲突",
    "invalid": "无效",
    "error": "失败",
    "cancelled": "已取消",
}

# 操作日志默认加载条数
_LATEST_OPERATIONS_LIMIT = 500


def _collect_log_files(log_dir: Path) -> list:
    """返回日志目录下按时间从旧到新排列的日志文件列表。

    轮转文件命名为 dit_workstation.log.1 ~ .10（数字越大越旧），
    当前文件为 dit_workstation.log（最新）。按旧→新合并拼接显示。
    """
    if not log_dir.is_dir():
        return []
    files = list(log_dir.glob("dit_workstation.log*"))
    if not files:
        return []
    # 分离当前文件与轮转文件：轮转文件按数字降序（.10 → .1）拼接，当前文件最后
    current = [p for p in files if p.name == "dit_workstation.log"]
    rotated = [p for p in files if p.name != "dit_workstation.log"]
    rotated.sort(key=lambda p: _rotation_index(p), reverse=True)
    return rotated + current


def _rotation_index(path: Path) -> int:
    """从轮转文件名提取序号（dit_workstation.log.3 → 3），无序号返回 0。"""
    suffix = path.name.rsplit(".", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return 0


def _read_log_files(log_dir: Path, max_chars: int = 3 * 1024 * 1024) -> str:
    """读取日志文件内容（旧→新合并），并限制总读取量避免内存膨胀。"""
    files = _collect_log_files(log_dir)
    if not files:
        return ""
    chunks = []
    total = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            logger.warning(f"读取日志文件失败 {path}: {e}")
            continue
        if total + len(content) > max_chars:
            chunks.append(f"\n...（日志过大，已截断，仅显示前 {format_size(max_chars)}）...\n")
            break
        chunks.append(content)
        total += len(content)
    return "".join(chunks)


class LogViewerDialog(QDialog):
    """在应用内查看系统日志文件与操作审计日志。"""

    object_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日志查看器")
        self.resize(960, 640)
        self._setup_ui()
        self._reload()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # ===== 标签一：系统日志 =====
        self.system_log_text = QPlainTextEdit()
        self.system_log_text.setReadOnly(True)
        self.system_log_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.system_log_text.setStyleSheet(MONO_FONT_QSS)
        self.tabs.addTab(self.system_log_text, "系统日志")

        # ===== 标签二：操作日志 =====
        operation_page = QDialog()
        operation_layout = QVBoxLayout(operation_page)
        operation_layout.setContentsMargins(0, 0, 0, 0)

        filters = QHBoxLayout()
        self.project_filter = QComboBox()
        self.project_filter.addItem("全部项目", "")
        self.event_filter = QLineEdit()
        self.event_filter.setPlaceholderText("事件")
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部结果", "")
        for key, label in _STATUS_LABELS.items():
            self.status_filter.addItem(label, key)
        self.object_filter = QLineEdit()
        self.object_filter.setPlaceholderText("对象类型")
        self.date_from_filter = QLineEdit()
        self.date_from_filter.setPlaceholderText("起始日期 YYYY-MM-DD")
        self.date_to_filter = QLineEdit()
        self.date_to_filter.setPlaceholderText("结束日期 YYYY-MM-DD")
        for widget in (self.project_filter, self.event_filter, self.status_filter,
                       self.object_filter, self.date_from_filter, self.date_to_filter):
            filters.addWidget(widget)
        apply_filters_btn = QPushButton("筛选")
        apply_filters_btn.clicked.connect(self._reload_operation_logs)
        filters.addWidget(apply_filters_btn)
        operation_layout.addLayout(filters)

        self.operation_table = QTableWidget(0, 6)
        self.operation_table.setHorizontalHeaderLabels(
            ["时间", "项目", "事件", "详情", "状态", "对象"]
        )
        self.operation_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.operation_table.setSelectionMode(QTableWidget.SingleSelection)
        self.operation_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # 全部列使用 Interactive 模式：允许用户在这两列及任意列之间自由拖动调整宽度。
        # 若某一列设为 Stretch 会吸收全部剩余空间，导致其右侧边界（详情↔状态）无法
        # 拖动，且状态/对象列被推到最右、拖动行为异常。
        header = self.operation_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in ((0, 150), (1, 120), (2, 100), (3, 320), (4, 70), (5, 140)):
            self.operation_table.setColumnWidth(col, width)
        self.operation_table.itemDoubleClicked.connect(self._request_selected_object)
        operation_layout.addWidget(self.operation_table, 1)

        operation_actions = QHBoxLayout()
        operation_actions.addStretch()
        self.open_object_btn = QPushButton("打开关联对象")
        self.open_object_btn.setToolTip("跳转到关联对象所在的工作区；对象 ID 会复制到剪贴板供定位")
        self.open_object_btn.clicked.connect(self._request_selected_object)
        operation_actions.addWidget(self.open_object_btn)
        self.export_operations_btn = QPushButton("导出 CSV")
        self.export_operations_btn.clicked.connect(self._export_operations)
        operation_actions.addWidget(self.export_operations_btn)
        operation_layout.addLayout(operation_actions)
        self.tabs.addTab(operation_page, "操作日志")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        # ===== 底部信息栏 + 按钮 =====
        self.info_label = QLabel("")
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        layout.addWidget(self.info_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setStyleSheet(SECONDARY_BUTTON_QSS)
        refresh_btn.setToolTip("重新加载当前标签页内容")
        refresh_btn.clicked.connect(self._reload)
        buttons.addWidget(refresh_btn)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(SECONDARY_BUTTON_QSS)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _on_tab_changed(self, _index: int):
        self._reload()

    def _reload(self):
        if self.tabs.currentIndex() == 0:
            self._reload_system_logs()
        else:
            self._reload_operation_logs()

    def _reload_system_logs(self):
        log_dir = Path(config.log_dir)
        files = _collect_log_files(log_dir)
        if not files:
            self.info_label.setText(f"日志目录：{log_dir}（无日志文件）")
            self.system_log_text.setPlainText("当前没有可显示的日志文件。")
            return
        content = _read_log_files(log_dir)
        total = sum(p.stat().st_size for p in files if p.exists())
        self.info_label.setText(
            f"日志目录：{log_dir}\n共 {len(files)} 个文件，占用 {format_size(total)}"
        )
        self.system_log_text.setPlainText(content)
        self.system_log_text.moveCursor(
            self.system_log_text.textCursor().MoveOperation.End
        )

    def _reload_operation_logs(self):
        try:
            db = get_db_service()
            self._populate_projects(db)
            date_from = self._parse_date(self.date_from_filter.text(), end=False)
            date_to = self._parse_date(self.date_to_filter.text(), end=True)
            operations = db.get_recent_operations(
                limit=_LATEST_OPERATIONS_LIMIT,
                project_id=self.project_filter.currentData() or None,
                event=self.event_filter.text().strip(),
                status=self.status_filter.currentData() or "",
                object_type=self.object_filter.text().strip(),
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as e:
            logger.warning(f"读取操作日志失败: {e}")
            self.info_label.setText("读取操作日志失败，请稍后重试。")
            self.operation_table.setRowCount(0)
            return

        # 项目名缓存：避免为每条记录重复查询
        project_names = {}
        self.operation_table.setRowCount(len(operations))
        for row, op in enumerate(operations):
            project_id = op.get("project_id") or ""
            if project_id not in project_names:
                try:
                    project = db.get_project(project_id)
                    project_names[project_id] = project.name if project else project_id
                except Exception:
                    project_names[project_id] = project_id
            created = op.get("created_at")
            time_text = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            status = op.get("status") or "success"
            status_label = _STATUS_LABELS.get(status, status)
            object_text = "/".join(
                part for part in (op.get("object_type") or "", op.get("object_id") or "") if part
            )
            values = [
                time_text,
                project_names.get(project_id, project_id),
                op.get("event") or "",
                op.get("detail") or "",
                status_label,
                object_text,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, op)
                self.operation_table.setItem(row, col, item)

        self.info_label.setText(
            f"操作审计日志：匹配 {len(operations)} 条（最多 {_LATEST_OPERATIONS_LIMIT} 条）"
        )

    def _populate_projects(self, db):
        """刷新项目筛选项，同时保留用户当前选择。"""
        selected = self.project_filter.currentData()
        if self.project_filter.count() > 1:
            return
        for project in db.get_projects():
            self.project_filter.addItem(project.name, project.project_id)
        index = self.project_filter.findData(selected)
        if index >= 0:
            self.project_filter.setCurrentIndex(index)

    @staticmethod
    def _parse_date(value: str, end: bool) -> datetime | None:
        value = value.strip()
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if len(value) == 10 and end:
                return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            return parsed
        except ValueError:
            return None

    def _selected_operation(self):
        row = self.operation_table.currentRow()
        if row < 0:
            return None
        item = self.operation_table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _request_selected_object(self, *_args):
        operation = self._selected_operation()
        if not operation or not operation.get("object_id"):
            self.info_label.setText("此记录没有关联对象。")
            return
        object_type, object_id = operation.get("object_type", ""), operation["object_id"]
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(object_id)
        self.object_requested.emit(object_type, object_id)
        self.info_label.setText(f"关联 {object_type or '对象'} ID 已复制：{object_id}")

    def _export_operations(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出操作审计日志", "operation_audit.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["时间", "项目", "事件", "详情", "状态", "对象类型", "对象 ID", "恢复 ID"])
                for row in range(self.operation_table.rowCount()):
                    op = self.operation_table.item(row, 0).data(Qt.UserRole)
                    writer.writerow([
                        self.operation_table.item(row, 0).text(),
                        self.operation_table.item(row, 1).text(), op.get("event", ""),
                        op.get("detail", ""), op.get("status", ""),
                        op.get("object_type", ""), op.get("object_id", ""), op.get("recovery_id", ""),
                    ])
            self.info_label.setText(f"已导出 {self.operation_table.rowCount()} 条操作审计日志：{path}")
        except OSError as exc:
            self.info_label.setText(f"导出操作审计日志失败：{exc}")
