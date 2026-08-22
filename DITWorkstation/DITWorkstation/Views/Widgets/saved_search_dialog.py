"""保存搜索管理对话框：列出/重命名/删除已保存的搜索条件。

从素材检索视图的「保存搜索」按钮打开。
"""
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

from DITWorkstation.Utils import get_db_service
from DITWorkstation.Views.Styles.theme import TITLE_QSS


class SavedSearchDialog(QDialog):
    """保存搜索管理对话框。"""

    def __init__(self, parent=None, db_service=None, on_apply=None):
        super().__init__(parent)
        self.setWindowTitle("保存的搜索")
        self.resize(500, 350)
        self.db_service = db_service or get_db_service()
        self._on_apply = on_apply
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(8)

        title = QLabel("保存的搜索 / 智能集合")
        title.setStyleSheet(TITLE_QSS)
        main.addWidget(title)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["名称", "类型", "筛选条件"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self._apply)
        main.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self._apply_selected)
        btn_row.addWidget(self.apply_btn)

        self.rename_btn = QPushButton("重命名")
        self.rename_btn.clicked.connect(self._rename_selected)
        btn_row.addWidget(self.rename_btn)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.delete_btn)

        btn_row.addStretch()
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)
        main.addLayout(btn_row)

    def _refresh(self):
        self.table.setRowCount(0)
        self._items = list(self.db_service.get_saved_searches())
        for item in self._items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(item["name"]))
            stype = "智能集合" if item["is_smart"] else "保存搜索"
            self.table.setItem(row, 1, QTableWidgetItem(stype))
            filters = item.get("filters", {})
            summary = ", ".join(f"{k}={v}" for k, v in filters.items() if v)
            self.table.setItem(row, 2, QTableWidgetItem(summary))
            self.table.item(row, 0).setData(Qt.UserRole, item["search_id"])

    def _apply_selected(self):
        self._apply(None)

    def _apply(self, _=None):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._items):
            return
        item = self._items[row]
        if self._on_apply and callable(self._on_apply):
            self._on_apply(item["filters"])
        self.accept()

    def _rename_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._items):
            return
        item = self._items[row]
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=item["name"])
        if ok and name:
            self.db_service.update_saved_search(item["search_id"], name=name)
            self._refresh()

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._items):
            return
        item = self._items[row]
        ret = QMessageBox.question(self, "确认删除", f"删除「{item['name']}」？")
        if ret == QMessageBox.Yes:
            self.db_service.delete_saved_search(item["search_id"])
            self._refresh()
