"""状态面板复用控件：进度条 + 状态标签 + 日志文本框。

多个视图原各自重复装配「QProgressBar + QLabel("就绪") + QTextEdit(readonly)」
三件套并实现 `_log` 方法（部分视图还重新实现了时间戳格式）。本控件将其统一封装，
消除装配重复与日志格式分歧（统一走 Utils.generate_log_message）。

通过 show_progress / show_status / show_log 三个开关适配仅含部分控件的视图：
- raw_extraction / rename：仅进度条 + 状态标签（show_log=False）
- report：仅日志框（show_progress=False, show_status=False）
"""
from typing import Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar, QTextEdit

from DITWorkstation.Utils import generate_log_message
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, MONO_FONT_QSS


class StatusPanel(QWidget):
    """统一的执行状态面板：进度条 + 状态标签 + 日志文本框。"""

    def __init__(
        self,
        parent=None,
        *,
        show_progress: bool = True,
        show_status: bool = True,
        show_log: bool = True,
        progress_range=(0, 100),
        status_text: str = "就绪",
        log_min_height: Optional[int] = 40,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 三个控件均可能为 None（按需创建），调用方通过别名引用时需注意。
        self.progress_bar: Optional[QProgressBar] = None
        self.status_label: Optional[QLabel] = None
        self.log_text: Optional[QTextEdit] = None

        if show_progress:
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(*progress_range)
            self.progress_bar.setValue(0)
            layout.addWidget(self.progress_bar)

        if show_status:
            self.status_label = QLabel(status_text)
            self.status_label.setStyleSheet(
                f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
            )
            layout.addWidget(self.status_label)

        if show_log:
            self.log_text = QTextEdit()
            self.log_text.setReadOnly(True)
            if log_min_height is not None:
                self.log_text.setMinimumHeight(log_min_height)
            self.log_text.setStyleSheet(MONO_FONT_QSS)
            layout.addWidget(self.log_text)

    def log(self, message: str):
        """追加一行日志（带时间戳，格式由 Utils.generate_log_message 统一）。"""
        if self.log_text is not None:
            self.log_text.append(generate_log_message(message))

    def set_progress(self, value: int):
        if self.progress_bar is not None:
            self.progress_bar.setValue(value)

    def set_status(self, text: str):
        if self.status_label is not None:
            self.status_label.setText(text)

    def clear_log(self):
        if self.log_text is not None:
            self.log_text.clear()
