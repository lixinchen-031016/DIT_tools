"""空状态提示组件 — 统一表格/列表无数据时的视觉反馈。

用法：
    from DITWorkstation.Views.Widgets.empty_state import attach_empty_state, sync_empty_state

    # 创建 QTableWidget 或 QTableView 时附加空状态
    self.result_table = QTableView()
    attach_empty_state(self.result_table, "🔍", "暂无搜索结果", "请输入搜索条件后点击「搜索」按钮")

    # 更新表格数据后同步显示/隐藏
    self.result_table.setRowCount(len(results))
    sync_empty_state(self.result_table)

设计要点：
- EmptyState 作为表格 viewport 的子控件，居中覆盖在表格上方
- 表格有数据时自动隐藏，无数据时自动显示
- 不改变原有表格结构和布局，零侵入接入
- 跨平台：使用标准 QLabel + QSS，无平台依赖
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTableView

from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE

_EMPTY_STATE_ATTR = "_empty_state_widget"


class EmptyState(QLabel):
    """空状态提示控件：居中显示图标、标题和提示文案。"""

    def __init__(self, icon: str, title: str, hint: str = "", parent=None):
        text = f"{icon}" if icon else ""
        if title:
            text += f"\n{title}" if text else title
        if hint:
            text += f"\n{hint}"

        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setStyleSheet(f"""
            QLabel {{
                color: {COLOR.TEXT_SECONDARY};
                font-size: {FONT_SIZE.BASE}px;
                background: transparent;
            }}
        """)
        # 让 hint 行更小更淡（通过富文本实现）
        if icon and title and hint:
            self.setTextFormat(Qt.RichText)
            self.setText(
                f"<div style='text-align: center;'>"
                f"<div style='font-size: {FONT_SIZE.XXL}px; margin-bottom: 8px;'>{icon}</div>"
                f"<div style='font-size: {FONT_SIZE.MD}px; font-weight: 600; color: {COLOR.TEXT_PRIMARY};'>{title}</div>"
                f"<div style='font-size: {FONT_SIZE.SM}px; color: {COLOR.TEXT_SECONDARY}; margin-top: 4px;'>{hint}</div>"
                f"</div>"
            )


def attach_empty_state(table: QTableView, icon: str, title: str, hint: str = ""):
    """为 QTableWidget 或 QTableView 附加空状态提示。

    在表格创建后调用一次即可。之后每次更新表格行数后调用 sync_empty_state(table)。

    Args:
        table: 目标表格
        icon: emoji 图标，如 "🔍"
        title: 标题文案，如 "暂无搜索结果"
        hint: 可选提示文案，如 "请输入条件后搜索"
    """
    empty = EmptyState(icon, title, hint, table.viewport())
    setattr(table, _EMPTY_STATE_ATTR, empty)
    empty.setGeometry(table.viewport().rect())
    empty.setVisible(_row_count(table) == 0)
    # 监听 viewport 的 resize 事件：表格缩放时保持空状态居中
    # 通过重写 viewport 的 resizeEvent 实现
    _install_resize_handler(table)


def sync_empty_state(table: QTableView):
    """根据表格行数同步显示/隐藏空状态提示。

    在每次 table.setRowCount(...) 之后调用。
    """
    empty = getattr(table, _EMPTY_STATE_ATTR, None)
    if empty is None:
        return
    empty.setVisible(_row_count(table) == 0)
    # 确保空状态控件覆盖整个 viewport
    empty.setGeometry(table.viewport().rect())
    empty.raise_()


def _install_resize_handler(table: QTableView):
    """安装 viewport resize 处理器，使空状态控件随表格缩放。"""
    viewport = table.viewport()
    original_resize = viewport.resizeEvent

    def _on_resize(event):
        original_resize(event)
        empty = getattr(table, _EMPTY_STATE_ATTR, None)
        if empty is not None and empty.isVisible():
            empty.setGeometry(viewport.rect())

    viewport.resizeEvent = _on_resize


def _row_count(table: QTableView) -> int:
    """兼容 QTableWidget 与由模型驱动的 QTableView。"""
    row_count = getattr(table, "rowCount", None)
    if callable(row_count):
        return row_count()
    model = table.model()
    return model.rowCount() if model is not None else 0
