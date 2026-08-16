"""表格工厂：统一 QTableWidget 配置，消除多个视图的装配重复。

各视图原重复 6-8 行 QTableWidget 配置（列数、表头、Stretch、SelectRows、
AlternatingRowColors、setDefaultSectionSize(32) 等）。本工厂将其集中为
一个 make_table(...) 调用，仅改创建配置，不影响 setItem / 信号连接 / 右键菜单等业务逻辑。
"""
from typing import List, Optional

from PySide6.QtWidgets import QTableWidget, QTableView, QHeaderView, QAbstractItemView

DEFAULT_ROW_HEIGHT = 32


def make_table(
    headers: List[str],
    *,
    sortable: bool = False,
    selection_mode=QAbstractItemView.SingleSelection,
    selection_behavior=QAbstractItemView.SelectRows,
    row_height: int = DEFAULT_ROW_HEIGHT,
    alternating: bool = True,
    stretch_columns: bool = True,
    resize_to_contents_cols: Optional[List[int]] = None,
) -> QTableWidget:
    """创建标准配置的 QTableWidget。

    Args:
        headers: 列标题列表
        sortable: 是否启用点击列头排序
        selection_mode / selection_behavior: 选择模式
        row_height: 行高（默认 32）
        alternating: 交替行颜色
        stretch_columns: 列宽策略，True=先全部设为 Stretch
        resize_to_contents_cols: 需在 Stretch 基础上改为 ResizeToContents 的列索引列表
            （设置顺序：先全部 Stretch，再逐列覆盖为 ResizeToContents，与原各视图一致）

    Returns:
        配置好的 QTableWidget（未填充数据，未连接业务信号）
    """
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    if stretch_columns:
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    if resize_to_contents_cols:
        for col in resize_to_contents_cols:
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
    table.setSelectionBehavior(selection_behavior)
    table.setSelectionMode(selection_mode)
    table.setAlternatingRowColors(alternating)
    table.verticalHeader().setDefaultSectionSize(row_height)
    if sortable:
        table.setSortingEnabled(True)
    return table


def make_table_view(
    *,
    sortable: bool = False,
    selection_mode=QAbstractItemView.SingleSelection,
    selection_behavior=QAbstractItemView.SelectRows,
    row_height: int = DEFAULT_ROW_HEIGHT,
    alternating: bool = True,
    resize_to_contents_cols: Optional[List[int]] = None,
) -> QTableView:
    """创建标准配置的 ``QTableView``，由调用方设置数据模型。"""
    table = QTableView()
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Stretch)
    if resize_to_contents_cols:
        for col in resize_to_contents_cols:
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
    table.setSelectionBehavior(selection_behavior)
    table.setSelectionMode(selection_mode)
    table.setAlternatingRowColors(alternating)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setSortingEnabled(sortable)
    return table
