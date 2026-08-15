"""回收站对话框：查看并恢复软删除的项目和素材记录。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from DITWorkstation.App.session_context import get_data_bus
from DITWorkstation.Utils import get_db_service, logger


_ENTITY_LABELS = {"asset": "素材", "project": "项目"}


class RecycleBinDialog(QDialog):
    """提供回收站记录的查看、刷新和安全恢复入口。"""

    def __init__(self, parent=None, db_service=None):
        super().__init__(parent)
        self.db_service = db_service or get_db_service()
        self.setWindowTitle("回收站")
        self.resize(900, 500)
        self._setup_ui()
        self._refresh_items()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        note = QLabel("已删除的数据库记录会在保留期内显示于此。恢复不会覆盖现有同 ID 记录。")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["类型", "对象 ID", "项目 ID", "删除时间", "到期时间"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.count_label = QLabel("")
        buttons.addWidget(self.count_label)
        buttons.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_items)
        buttons.addWidget(refresh_btn)
        self.restore_btn = QPushButton("恢复选中项")
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self._restore_selected)
        buttons.addWidget(self.restore_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self.table.itemSelectionChanged.connect(
            lambda: self.restore_btn.setEnabled(bool(self._selected_recycle_id()))
        )

    def _refresh_items(self):
        self.db_service.cleanup_expired_recycle_bin()
        items = self.db_service.get_recycle_bin_items()
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            type_item = QTableWidgetItem(_ENTITY_LABELS.get(item["entity_type"], item["entity_type"]))
            type_item.setData(Qt.UserRole, item["recycle_id"])
            self.table.setItem(row, 0, type_item)
            self.table.setItem(row, 1, QTableWidgetItem(item["entity_id"]))
            self.table.setItem(row, 2, QTableWidgetItem(item.get("project_id") or ""))
            self.table.setItem(row, 3, QTableWidgetItem(str(item["deleted_at"])))
            self.table.setItem(row, 4, QTableWidgetItem(str(item["expires_at"])))
        self.count_label.setText(f"共 {len(items)} 项")
        self.restore_btn.setEnabled(False)

    def _selected_recycle_id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else None

    def _restore_selected(self):
        recycle_id = self._selected_recycle_id()
        if not recycle_id:
            return
        result = self.db_service.restore_recycle_item(recycle_id)
        if not result:
            QMessageBox.warning(self, "恢复失败", result.message or "无法恢复该记录")
            return
        logger.info(f"已从回收站恢复记录: {recycle_id}")
        get_data_bus().emit_data_changed("recycle_restored")
        QMessageBox.information(self, "恢复完成", "记录已恢复。")
        self._refresh_items()
