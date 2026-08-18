"""按需展示素材分页数据的 Qt 表格模型。"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

CellFactory = Callable[[object, int], tuple[str, QColor | None]]


class AssetTableModel(QAbstractTableModel):
    """只持有当前页 ``MediaAsset`` 的轻量表格模型。

    ``QTableWidget`` 会为每个单元格构造一个 Qt 对象；大项目翻页时这会带来
    不必要的分配。此模型只保存当前页的领域对象，显示文本在请求时生成。
    """

    def __init__(self, headers: list[str], cell_factory: CellFactory, parent=None):
        super().__init__(parent)
        self._headers = headers
        self._cell_factory = cell_factory
        self._assets: list[object] = []
        self._status: dict[str, tuple[str, QColor | None]] = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._assets)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and 0 <= section < len(self._headers):
            return self._headers[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._assets):
            return None
        asset = self._assets[index.row()]
        if role == Qt.UserRole:
            return getattr(asset, "asset_id", None)
        if role not in (Qt.DisplayRole, Qt.ForegroundRole):
            return None
        text, color = self._cell_factory(asset, index.column())
        return text if role == Qt.DisplayRole else color

    def set_assets(self, assets) -> None:
        self.beginResetModel()
        self._assets = list(assets)
        self.endResetModel()

    def clear(self) -> None:
        self.set_assets([])

    def asset_at(self, row: int):
        return self._assets[row] if 0 <= row < len(self._assets) else None

    def set_status(self, asset_id: str, text: str, color: QColor | None = None) -> None:
        self._status[asset_id] = (text, color)
        for row, asset in enumerate(self._assets):
            if getattr(asset, "asset_id", None) == asset_id:
                index = self.index(row, self.columnCount() - 1)
                self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.ForegroundRole])
                return

    def status_for(self, asset_id: str, default: tuple[str, QColor | None]):
        return self._status.get(asset_id, default)

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        """保持当前页可排序；分页顺序仍以数据库的 keyset 游标为准。"""
        reverse = order == Qt.DescendingOrder
        self.layoutAboutToBeChanged.emit()
        self._assets.sort(
            key=lambda asset: (self._cell_factory(asset, column)[0], getattr(asset, "asset_id", "")),
            reverse=reverse,
        )
        self.layoutChanged.emit()
