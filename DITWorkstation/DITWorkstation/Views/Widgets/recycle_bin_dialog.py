"""回收站对话框：查看并恢复软删除的项目和素材记录。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
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
        self.restore_all_btn = QPushButton("还原全部记录")
        self.restore_all_btn.setEnabled(False)
        self.restore_all_btn.clicked.connect(self._restore_all)
        buttons.addWidget(self.restore_all_btn)
        self.empty_btn = QPushButton("清空回收站")
        self.empty_btn.setEnabled(False)
        self.empty_btn.clicked.connect(self._empty_recycle_bin)
        buttons.addWidget(self.empty_btn)
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
        self.restore_all_btn.setEnabled(bool(items))
        self.empty_btn.setEnabled(bool(items))

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

    def _restore_all(self):
        count = self.table.rowCount()
        if not count:
            return
        reply = QMessageBox.question(
            self, "确认还原全部记录",
            f"确定还原回收站中的全部 {count} 项记录？\n\n"
            "恢复不会覆盖现有同 ID 记录；无法恢复的记录将保留在回收站中。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        result = self.db_service.restore_all_recycle_items()
        if not result:
            QMessageBox.warning(self, "恢复失败", result.message or "无法恢复回收站记录")
            return
        if result.affected_count:
            logger.info(f"已从回收站批量恢复记录: {result.affected_count}")
            get_data_bus().emit_data_changed("recycle_restored")
        message = f"已恢复 {result.affected_count} 项记录。"
        if result.message:
            message = f"{message}\n{result.message}，其余记录仍保留在回收站中。"
        QMessageBox.information(self, "恢复完成", message)
        self._refresh_items()

    def _empty_recycle_bin(self):
        count = self.table.rowCount()
        if not count:
            return
        reply = QMessageBox.question(
            self, "确认清空回收站",
            f"确定永久删除回收站中的全部 {count} 项记录？\n\n"
            "此操作无法撤销，且不会删除磁盘上的源媒体文件。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        result = self.db_service.empty_recycle_bin()
        if not result:
            QMessageBox.warning(self, "清空失败", result.message or "无法清空回收站")
            return
        logger.info(f"已清空回收站: {result.affected_count} 项")
        QMessageBox.information(self, "清空完成", f"已永久删除 {result.affected_count} 项回收站记录。")
        self._refresh_items()
