"""视图缩放支持。

为所有业务视图提供统一的缩放能力（Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+滚轮）。
通过 ZoomableMixin 注入 RefreshOnShowView，一次性覆盖全部视图。

设计要点：
- 基准尺寸记录在 Qt dynamic property 上，避免反复缩放导致尺寸累积漂移
- 表格缩放：行高 + 表头字体 + 单元格字体
- 标签缩放：字号（px）
- 缩放后 QLabel/QGroupBox 的 sizeHint 自动增大，触发外层 QScrollArea 滚动条
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QTableWidget

# 缩放参数
ZOOM_DEFAULT = 1.0
ZOOM_MIN = 0.75
ZOOM_MAX = 2.5
ZOOM_STEP = 0.1

# 基准尺寸 dynamic property 名
_BASE_ROW_SIZE_ATTR = "_zoom_base_row_size"
_BASE_HEADER_FONT_ATTR = "_zoom_base_header_font"
_BASE_ITEM_FONT_ATTR = "_zoom_base_item_font"
_BASE_LABEL_FONT_PX_ATTR = "_zoom_base_font_px"


class ZoomableMixin:
    """为视图提供 Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+滚轮 缩放能力。

    子类可重写 `_apply_zoom(factor)` 自定义缩放目标控件；
    默认实现遍历子 QTableWidget 并调用 apply_table_zoom。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._zoom_factor = ZOOM_DEFAULT
        # 安装事件过滤器捕获 Ctrl+滚轮
        self.installEventFilter(_ZoomWheelFilter(self))

    def zoom_in(self):
        self._set_zoom(min(self._zoom_factor + ZOOM_STEP, ZOOM_MAX))

    def zoom_out(self):
        self._set_zoom(max(self._zoom_factor - ZOOM_STEP, ZOOM_MIN))

    def zoom_reset(self):
        self._set_zoom(ZOOM_DEFAULT)

    def _set_zoom(self, factor: float):
        if abs(factor - self._zoom_factor) < 1e-3:
            return
        self._zoom_factor = factor
        self._apply_zoom(factor)

    def _apply_zoom(self, factor: float):
        """默认缩放：对所有子 QTableWidget 应用表格缩放。"""
        for child in self.findChildren(QTableWidget):
            apply_table_zoom(child, factor)


class _ZoomWheelFilter(QObject):
    """Ctrl+滚轮 触发视图缩放。"""

    def __init__(self, target):
        super().__init__(target)
        self._target = target

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self._target.zoom_in()
            else:
                self._target.zoom_out()
            return True
        return super().eventFilter(obj, event)


def apply_table_zoom(table: QTableWidget, factor: float):
    """缩放表格：行高 + 表头字体 + 单元格字体。

    首次调用时记录基准尺寸到 dynamic property，后续按 基准 × factor 计算，
    避免反复缩放导致尺寸累积漂移。
    """
    vh = table.verticalHeader()

    # 行高基准
    base_row = table.property(_BASE_ROW_SIZE_ATTR)
    if base_row is None:
        base_row = vh.defaultSectionSize() or 32
        table.setProperty(_BASE_ROW_SIZE_ATTR, base_row)
    vh.setDefaultSectionSize(max(20, int(base_row * factor)))

    # 表头字体基准
    header = table.horizontalHeader()
    base_h_font = table.property(_BASE_HEADER_FONT_ATTR)
    if base_h_font is None:
        base_h_font = QFont(header.font())
        table.setProperty(_BASE_HEADER_FONT_ATTR, base_h_font)
    h_font = QFont(base_h_font)
    h_font.setPointSizeF(max(6.0, base_h_font.pointSizeF() * factor))
    header.setFont(h_font)

    # 单元格字体基准
    base_i_font = table.property(_BASE_ITEM_FONT_ATTR)
    if base_i_font is None:
        base_i_font = QFont(table.font())
        table.setProperty(_BASE_ITEM_FONT_ATTR, base_i_font)
    i_font = QFont(base_i_font)
    i_font.setPointSizeF(max(6.0, base_i_font.pointSizeF() * factor))
    table.setFont(i_font)


def apply_label_zoom(label: QLabel, factor: float, base_px: int):
    """缩放 QLabel 字体。base_px 为基准字号（px）。"""
    f = QFont(label.font())
    f.setPixelSize(max(8, int(base_px * factor)))
    label.setFont(f)
