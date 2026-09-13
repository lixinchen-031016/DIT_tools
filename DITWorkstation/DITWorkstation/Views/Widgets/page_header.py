"""PageHeader 页面头组件（《UI 现代化优化建议》§8.2 / §5.2）。

用途：统一各页面的「标题 + 副标题 + 页面级操作」头部区域，替代各视图
手写的标题布局。左侧为页面标题（22px 粗体）与副标题（13px 次要色），
右侧为右对齐的操作按钮区（供调用方放置主操作 / 次操作按钮），
底部一条 1px 分隔线（BORDER 色）建立页面头部与内容的分区（§4 通用规范）。

颜色全部取自 theme 调色板令牌，不硬编码，保证未来主题切换可用。

使用示例：
    header = PageHeader("媒体导入", "从存储卡 / 目录导入素材")
    header.add_action(import_btn)   # 主按钮加入右侧操作区
    layout.addWidget(header)
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from DITWorkstation.Views.Styles.theme import COLOR, TYPO


class PageHeader(QWidget):
    """页面头：标题 / 副标题（左）+ 右对齐操作区（右）+ 底部分隔线。"""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        """初始化页面头。

        Args:
            title: 页面主标题。
            subtitle: 可选副标题，为空时不占位。
            parent: 父控件。
        """
        super().__init__(parent)

        # ---- 左侧：垂直的标题 + 副标题 ----
        text_container = QWidget(self)
        text_layout = QVBoxLayout(text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self._title_label = QLabel(title, text_container)
        self._title_label.setStyleSheet(
            f"font-size: {TYPO.PAGE_TITLE}px; font-weight: bold; "
            f"color: {COLOR.TEXT_PRIMARY};"
        )
        text_layout.addWidget(self._title_label)

        self._subtitle_label = QLabel(subtitle, text_container)
        self._subtitle_label.setStyleSheet(
            f"font-size: {TYPO.BODY}px; color: {COLOR.TEXT_SECONDARY};"
        )
        self._subtitle_label.setVisible(bool(subtitle))
        text_layout.addWidget(self._subtitle_label)

        # ---- 右侧：页面级操作按钮区（右对齐）----
        self.action_layout = QHBoxLayout()
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(8)
        self.action_layout.addStretch(1)  # 将按钮推向右侧

        # ---- 顶部横向布局（放在内容容器上，自身保留唯一的外层布局）----
        header_row = QWidget(self)
        root = QHBoxLayout(header_row)
        root.setContentsMargins(0, 0, 0, 12)
        root.setSpacing(16)
        root.addWidget(text_container, 1)
        root.addLayout(self.action_layout)

        # ---- 底部 1px 分隔线（BORDER 色）----
        self._separator = QWidget(self)
        self._separator.setFixedHeight(1)
        self._separator.setStyleSheet(
            f"background-color: {COLOR.BORDER}; border: none;"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header_row)
        outer.addWidget(self._separator)

    # ---- 标题 / 副标题 ----

    def set_title(self, title: str) -> None:
        """设置页面标题。"""
        self._title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        """设置副标题；传空字符串时隐藏副标题占位。"""
        self._subtitle_label.setText(subtitle)
        self._subtitle_label.setVisible(bool(subtitle))

    # ---- 右侧操作区 ----

    def add_action(self, btn: QWidget) -> None:
        """向右侧操作区追加一个控件（通常为按钮），追加在 stretch 之后右对齐。

        Args:
            btn: 要加入的控件（QPushButton / QToolButton 等）。
        """
        # 插到 stretch 之前，保证多按钮仍按添加顺序从左到右右对齐排列
        self.action_layout.insertWidget(self.action_layout.count() - 1, btn)
