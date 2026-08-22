"""Project storage capacity trend widget."""

from collections import defaultdict
from itertools import pairwise

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from DITWorkstation.Views.Styles.theme import COLOR


class CapacityTrendWidget(QWidget):
    """Draw recent free-capacity ratios for each backup target."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshots = []
        self._message = "暂无容量快照，刷新健康状态后开始记录。"
        self.setMinimumHeight(180)
        self.setMinimumWidth(320)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def sizeHint(self):
        return QSize(720, 210)

    def set_snapshots(self, snapshots):
        self._snapshots = list(snapshots or [])
        self.update()

    def set_message(self, message: str):
        self._message = message
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(12, 12, -12, -12)
        painter.setPen(QPen(QColor(COLOR.BORDER), 1))
        painter.setBrush(QColor(COLOR.BG_CARD))
        painter.drawRoundedRect(rect, 6, 6)

        grouped = defaultdict(list)
        for item in self._snapshots:
            path = str(item.get("target_path") or "")
            total = int(item.get("total_bytes") or 0)
            if path and total > 0:
                grouped[path].append(item)
        if not grouped:
            painter.setPen(QColor(COLOR.TEXT_SECONDARY))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._message)
            return

        left, top = rect.left() + 46, rect.top() + 30
        right, bottom = rect.right() - 18, rect.bottom() - 34
        chart_width = max(1, right - left)
        chart_height = max(1, bottom - top)

        painter.setFont(self.font())
        painter.setPen(QColor(COLOR.TEXT_SECONDARY))
        for ratio, label in ((1.0, "100%"), (0.5, "50%"), (0.0, "0%")):
            y = bottom - ratio * chart_height
            painter.setPen(QPen(QColor(COLOR.BORDER_LIGHT), 1))
            painter.drawLine(left, int(y), right, int(y))
            painter.setPen(QColor(COLOR.TEXT_SECONDARY))
            painter.drawText(4, int(y - 8), 38, 16, Qt.AlignmentFlag.AlignRight, label)

        palette = [COLOR.PRIMARY, COLOR.SUCCESS, COLOR.INFO, COLOR.WARNING]
        legend_x = left
        for index, (path, entries) in enumerate(sorted(grouped.items())):
            entries = sorted(entries, key=lambda item: item.get("captured_at") or "")[
                -60:
            ]
            color = QColor(palette[index % len(palette)])
            painter.setPen(QPen(color, 2))
            points = []
            for point_index, item in enumerate(entries):
                total = int(item.get("total_bytes") or 0)
                free = max(0, int(item.get("free_bytes") or 0))
                ratio = min(1.0, free / total) if total else 0.0
                x = left + (chart_width * point_index / max(1, len(entries) - 1))
                y = bottom - ratio * chart_height
                points.append(QPointF(x, y))
            if len(points) == 1:
                painter.drawEllipse(points[0], 3, 3)
            else:
                for first, second in pairwise(points):
                    painter.drawLine(first, second)
                painter.setBrush(color)
                for point in points[-1:]:
                    painter.drawEllipse(point, 3, 3)

            label = path.rsplit("/", 1)[-1] or path
            label = QFontMetrics(self.font()).elidedText(
                label, Qt.TextElideMode.ElideRight, 140
            )
            painter.drawLine(
                legend_x, rect.bottom() - 17, legend_x + 14, rect.bottom() - 17
            )
            painter.setPen(QColor(COLOR.TEXT_SECONDARY))
            painter.drawText(legend_x + 18, rect.bottom() - 10, label)
            legend_x += 170
