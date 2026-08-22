"""Capture-date timeline for search results."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from DITWorkstation.Utils import format_size
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS


class CaptureTimelineWidget(QWidget):
    """Horizontal date cards that emit a date range when selected."""

    period_selected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
        )
        layout.addWidget(self.summary_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(155)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.cards_layout = QHBoxLayout(self.content)
        self.cards_layout.setContentsMargins(8, 8, 8, 8)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

    def set_entries(self, entries: list[dict]):
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = list(entries or [])
        self.summary_label.setText(
            f"按拍摄日期聚合：{len(entries)} 天"
            if entries
            else "没有包含拍摄日期的素材"
        )
        for entry in entries:
            period = str(entry.get("period") or "未知日期")
            card = QPushButton()
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setToolTip("点击查看该日期拍摄的素材")
            card.setStyleSheet(
                f"QPushButton {{ text-align: left; background: {COLOR.BG_CARD}; "
                f"border: 1px solid {COLOR.BORDER}; border-radius: {RADIUS.CARD}px; "
                f"padding: 12px; min-width: 150px; }} "
                f"QPushButton:hover {{ border-color: {COLOR.PRIMARY}; background: {COLOR.BANNER_INFO_BG}; }}"
            )
            card.setText(
                f"{period}\n"
                f"{int(entry.get('asset_count') or 0)} 个素材\n"
                f"{format_size(int(entry.get('total_size') or 0))}"
            )
            start = (
                period
                if len(period) == 10
                else str(entry.get("first_taken") or period)[:10]
            )
            end = (
                period
                if len(period) == 10
                else str(entry.get("last_taken") or period)[:10]
            )
            card.clicked.connect(
                lambda _checked=False, start=start, end=end: self.period_selected.emit(
                    start, end
                )
            )
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
