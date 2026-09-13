"""PathField 路径字段组件（《UI 现代化优化建议》§8.2 / §6.2 / §5.3）。

用途：统一「路径展示 + 浏览 / 打开目录 / 清除」三动作的路径输入区，
替代各视图手写的 QLineEdit + 按钮组合。对应报告 §6.2 媒体导入页
「来源目录采用大号路径输入区，提供浏览、打开、清除三个明确动作」。

组成：
- 内部 QLineEdit：默认只读展示（editable=False），可切换为可编辑；
- 「浏览」按钮：QStyle.SP_DirOpenIcon，弹出目录选择对话框；
- 「打开目录」按钮：QDesktopServices.openUrl 打开当前路径；
  路径为空或目录不存在时自动禁用（§8.2 异常兜底）；
- 「清除」按钮：QStyle.SP_TrashIcon，清空路径并发射 path_changed("")。

信号：path_changed(str)——用户浏览选择 / 清除 / 可编辑状态下手动修改时发射；
程序化 set_path 不发射（避免回环）。

按钮为 QToolButton + AutoRaise + tooltip，最小点击区域 28x28（§5.3
图标按钮最小点击区域要求），样式使用 theme.TOOL_BUTTON_QSS 工具按钮令牌。

使用示例：
    field = PathField("选择备份目标目录")
    field.path_changed.connect(self._on_target_changed)
    layout.addWidget(field)
"""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QStyle,
    QToolButton,
    QWidget,
)

from DITWorkstation.Views.Styles.theme import CONTROL, TOOL_BUTTON_QSS


class PathField(QWidget):
    """路径字段：只读/可编辑路径框 + 浏览 / 打开目录 / 清除三个工具按钮。"""

    # 路径变化信号：浏览选择、清除、可编辑状态下用户修改时发射
    path_changed = Signal(str)

    def __init__(self, placeholder: str = "", parent: QWidget | None = None):
        """初始化路径字段。

        Args:
            placeholder: 路径为空时的占位提示文字。
            parent: 父控件。
        """
        super().__init__(parent)

        # ---- 内部路径输入框：默认只读展示 ----
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setReadOnly(True)  # editable 默认 False
        self._edit.setMinimumHeight(CONTROL.HEIGHT)
        # 用户在可编辑状态下手动修改时发射信号（textEdited 仅响应用户输入）
        self._edit.textEdited.connect(self._on_user_edited)

        # ---- 三个工具按钮（图标 + tooltip，§5.4；最小 28x28 点击区，§5.3）----
        style = self.style()

        self._browse_btn = QToolButton(self)
        self._browse_btn.setAutoRaise(True)
        self._browse_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self._browse_btn.setToolTip("浏览…（选择目录）")
        self._browse_btn.setMinimumSize(28, 28)
        self._browse_btn.setStyleSheet(TOOL_BUTTON_QSS)
        self._browse_btn.clicked.connect(self._on_browse)

        self._open_btn = QToolButton(self)
        self._open_btn.setAutoRaise(True)
        self._open_btn.setIcon(
            style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        )
        self._open_btn.setToolTip("打开目录")
        self._open_btn.setMinimumSize(28, 28)
        self._open_btn.setStyleSheet(TOOL_BUTTON_QSS)
        self._open_btn.clicked.connect(self._on_open_directory)

        self._clear_btn = QToolButton(self)
        self._clear_btn.setAutoRaise(True)
        self._clear_btn.setIcon(
            style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self._clear_btn.setToolTip("清除路径")
        self._clear_btn.setMinimumSize(28, 28)
        self._clear_btn.setStyleSheet(TOOL_BUTTON_QSS)
        self._clear_btn.clicked.connect(self._on_clear)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)  # SPACING.XS：图标按钮与输入框紧邻（§5.3）
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._browse_btn)
        layout.addWidget(self._open_btn)
        layout.addWidget(self._clear_btn)

        # 初始同步按钮可用状态（路径为空：打开/清除禁用）
        self._sync_button_states()

    # ---- 公开接口 ----

    def set_path(self, path: str) -> None:
        """程序化设置路径（不发射 path_changed，避免回环）。"""
        self._edit.setText(path or "")
        self._sync_button_states()

    def path(self) -> str:
        """返回当前路径文本。"""
        return self._edit.text()

    def set_editable(self, editable: bool) -> None:
        """切换路径框为可编辑 / 只读。"""
        self._edit.setReadOnly(not editable)

    def is_editable(self) -> bool:
        """路径框当前是否可编辑。"""
        return not self._edit.isReadOnly()

    # ---- 内部实现 ----

    def _on_browse(self) -> None:
        """弹出目录选择对话框，选中后写回路径并发射信号。"""
        try:
            current = self._edit.text()
            start_dir = current if current and os.path.isdir(current) else ""
            selected = QFileDialog.getExistingDirectory(self, "选择目录", start_dir)
        except Exception:
            return
        if selected:
            self._edit.setText(selected)
            self._sync_button_states()
            self.path_changed.emit(selected)

    def _on_open_directory(self) -> None:
        """用系统文件管理器打开当前目录；目录不存在时禁用兜底。"""
        path = self._edit.text()
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_clear(self) -> None:
        """清空路径并发射 path_changed("")。"""
        self._edit.clear()
        self._sync_button_states()
        self.path_changed.emit("")

    def _on_user_edited(self, text: str) -> None:
        """可编辑状态下用户手动修改路径时同步状态并发射信号。"""
        self._sync_button_states()
        self.path_changed.emit(text)

    def _sync_button_states(self) -> None:
        """根据当前路径同步按钮可用状态：目录存在才可打开，非空才可清除。"""
        path = self._edit.text()
        self._open_btn.setEnabled(bool(path) and os.path.isdir(path))
        self._clear_btn.setEnabled(bool(path))
