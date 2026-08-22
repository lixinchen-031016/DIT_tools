"""可复用的错误反馈对话框组件。

替代 QMessageBox.critical，提供：
- 友好的错误标题和描述
- 复制错误详情按钮（含完整 traceback）
- 可选的重试按钮
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS


class ErrorDialog(QDialog):
    """错误反馈对话框。

    Features:
    - 友好的错误标题（红色图标 + 标题）
    - 错误描述（用户可读的文案）
    - 折叠的错误详情（技术信息，默认折叠）
    - 复制错误详情按钮
    - 可选重试按钮
    """

    def __init__(
        self,
        title: str,
        description: str,
        details: str = "",
        retry_callback: Callable | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("错误")
        self.setMinimumWidth(480)
        self._retry_callback = retry_callback
        self._details = details

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 16)

        # 错误标题行（图标 + 标题）
        header_row = QHBoxLayout()
        icon_label = QLabel("❌")
        icon_label.setStyleSheet(f"font-size: {FONT_SIZE.XXL}px;")
        header_row.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: {FONT_SIZE.LG}px; font-weight: 600; color: {COLOR.DANGER};"
        )
        title_label.setWordWrap(True)
        header_row.addWidget(title_label, 1)
        layout.addLayout(header_row)

        # 错误描述
        desc_label = QLabel(description)
        desc_label.setStyleSheet(
            f"font-size: {FONT_SIZE.BASE}px; color: {COLOR.TEXT_PRIMARY};"
        )
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # 错误详情（可折叠）
        if details:
            details_btn = QPushButton("▶ 查看技术详情")
            details_btn.setStyleSheet("text-align: left; border: none; padding: 4px 0;")
            details_btn.setCursor(Qt.PointingHandCursor)
            layout.addWidget(details_btn)

            self.details_text = QTextEdit()
            self.details_text.setPlainText(details)
            self.details_text.setReadOnly(True)
            self.details_text.setMaximumHeight(150)
            self.details_text.setStyleSheet(
                f"background-color: {COLOR.BG_GROUP}; "
                f"border: 1px solid {COLOR.BORDER}; "
                f"border-radius: {RADIUS.INPUT}px; "
                f"font-family: 'Menlo', 'Consolas', 'Courier New', monospace; "
                f"font-size: {FONT_SIZE.XS}px;"
            )
            self.details_text.setVisible(False)
            layout.addWidget(self.details_text)

            self._details_visible = False

            def _toggle_details():
                self._details_visible = not self._details_visible
                self.details_text.setVisible(self._details_visible)
                details_btn.setText(
                    "▼ 隐藏技术详情" if self._details_visible else "▶ 查看技术详情"
                )

            details_btn.clicked.connect(_toggle_details)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {COLOR.BORDER_LIGHT};")
        layout.addWidget(line)

        # 按钮区
        btn_row = QHBoxLayout()

        copy_btn = QPushButton("📋 复制错误详情")
        copy_btn.setToolTip("将完整错误信息复制到剪贴板，便于反馈问题")
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.BG_CARD};
                color: {COLOR.TEXT_PRIMARY};
                padding: 8px 16px;
                border: 1px solid {COLOR.BORDER};
                border-radius: {RADIUS.BUTTON}px;
                font-size: {FONT_SIZE.SM}px;
            }}
            QPushButton:hover {{ background-color: {COLOR.BG_APP}; }}
        """)
        copy_btn.clicked.connect(self._copy_details)
        btn_row.addWidget(copy_btn)

        btn_row.addStretch()

        if retry_callback is not None:
            retry_btn = QPushButton("🔄 重试")
            retry_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR.PRIMARY};
                    color: white;
                    padding: 8px 20px;
                    border-radius: {RADIUS.BUTTON}px;
                    font-size: {FONT_SIZE.SM}px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: {COLOR.PRIMARY_HOVER}; }}
            """)
            retry_btn.clicked.connect(self._on_retry)
            btn_row.addWidget(retry_btn)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 20px;
                border-radius: {RADIUS.BUTTON}px;
                font-size: {FONT_SIZE.SM}px;
            }}
        """)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _copy_details(self):
        """复制错误详情到剪贴板"""
        text = self._details if self._details else ""
        if not text:
            return
        cb = QGuiApplication.clipboard()
        cb.setText(text)
        # 简短反馈
        sender = self.sender()
        if sender:
            sender.setText("✓ 已复制")
            from PySide6.QtCore import QTimer

            QTimer.singleShot(2000, lambda: sender.setText("📋 复制错误详情"))

    def _on_retry(self):
        """重试回调"""
        if self._retry_callback:
            self.accept()
            self._retry_callback()


def show_error(
    title: str,
    description: str,
    details: str = "",
    retry_callback: Callable | None = None,
    parent=None,
):
    """便捷函数：弹出错误对话框。

    Args:
        title: 错误标题（简短）
        description: 用户可读的错误描述
        details: 技术详情（如 traceback 字符串），可选
        retry_callback: 重试回调，可选
        parent: 父窗口
    """
    dialog = ErrorDialog(title, description, details, retry_callback, parent)
    dialog.exec()
