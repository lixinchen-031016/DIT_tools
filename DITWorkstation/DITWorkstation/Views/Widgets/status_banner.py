"""StatusBanner 状态横幅组件（《UI 现代化优化建议》§8.2 / §7.2）。

用途：在页面内以非模态状态条展示「信息 / 成功 / 警告 / 错误」四类状态，
替代零散的 QLabel + 手写配色。每种状态使用调色板对应的浅色背景 +
语义色文字 + 1px 语义色边框（BANNER_* 令牌，含 §8.2 扩展的
success / error 变体，深色调色板亦提供对应值）。

状态图标使用 Qt 内置标准图标（QStyle.StandardPixmap），不使用 emoji，
保证跨平台渲染一致（§5.4 / §9）。

无状态时默认隐藏、不占布局空间；set_status 后自动可见，clear() 隐藏。

使用示例：
    banner = StatusBanner()
    layout.addWidget(banner)
    banner.set_status("warning", "2 个文件冲突，已跳过")
    banner.clear()
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStyle, QWidget

from DITWorkstation.Views.Styles.theme import COLOR, CONTROL, RADIUS, SPACING

# 状态种类 -> (Qt 标准图标, 调色板背景/前景/边框令牌名)
_KIND_TOKENS: dict[str, tuple[QStyle.StandardPixmap, str, str, str]] = {
    "info": (
        QStyle.StandardPixmap.SP_MessageBoxInformation,
        "BANNER_INFO_BG",
        "BANNER_INFO_FG",
        "BANNER_INFO_BORDER",
    ),
    "success": (
        QStyle.StandardPixmap.SP_DialogApplyButton,
        "BANNER_SUCCESS_BG",
        "BANNER_SUCCESS_FG",
        "BANNER_SUCCESS_BORDER",
    ),
    "warning": (
        QStyle.StandardPixmap.SP_MessageBoxWarning,
        "BANNER_WARNING_BG",
        "BANNER_WARNING_FG",
        "BANNER_WARNING_BORDER",
    ),
    "error": (
        QStyle.StandardPixmap.SP_MessageBoxCritical,
        "BANNER_ERROR_BG",
        "BANNER_ERROR_FG",
        "BANNER_ERROR_BORDER",
    ),
}


class StatusBanner(QWidget):
    """页面内状态横幅：标准图标 + 状态文字，四类语义状态配色。"""

    def __init__(self, parent: QWidget | None = None):
        """初始化状态横幅；默认隐藏，等待首次 set_status。"""
        super().__init__(parent)
        self.setVisible(False)  # 无状态时不占位（§8.2）

        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(CONTROL.ICON_MD, CONTROL.ICON_MD)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._text_label = QLabel(self)
        self._text_label.setWordWrap(True)  # 中文长文本换行（§5.2）

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            SPACING.MD, SPACING.SM, SPACING.MD, SPACING.SM
        )
        layout.setSpacing(SPACING.SM)
        layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._text_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self._kind = ""

    # ---- 公开接口 ----

    def set_status(self, kind: str, text: str) -> None:
        """设置并显示一条状态。

        Args:
            kind: 状态种类，取值 "info" / "success" / "warning" / "error"；
                  未知取值兜底为 "info"，保证界面不中断。
            text: 状态说明文字。
        """
        if kind not in _KIND_TOKENS:
            kind = "info"
        self._kind = kind

        icon_enum, bg_token, fg_token, border_token = _KIND_TOKENS[kind]

        # 图标：Qt 内置标准图标（不使用 emoji，§5.4）
        icon: QIcon = self.style().standardIcon(icon_enum)
        self._icon_label.setPixmap(icon.pixmap(CONTROL.ICON_MD, CONTROL.ICON_MD))

        # 文字与配色：全部来自当前激活调色板令牌
        self._text_label.setText(text)
        self.setStyleSheet(
            f"StatusBanner {{"
            f" background-color: {getattr(COLOR, bg_token)};"
            f" border: 1px solid {getattr(COLOR, border_token)};"
            f" border-radius: {RADIUS.ROW}px;"
            f" }}"
            f"StatusBanner QLabel {{"
            f" color: {getattr(COLOR, fg_token)};"
            f" background: transparent;"
            f" font-size: 13px;"
            f" }}"
        )
        self.setVisible(True)

    def clear(self) -> None:
        """清除状态并隐藏横幅（不再占位）。"""
        self._kind = ""
        self._icon_label.clear()
        self._text_label.clear()
        self.setVisible(False)

    # ---- 只读属性 ----

    @property
    def kind(self) -> str:
        """当前状态种类；无状态时为空字符串。"""
        return self._kind
