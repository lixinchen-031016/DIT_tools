"""
DIT工作站 - 应用入口
专业摄影数据管理应用，支持安全备份、校验和验证、JPG筛选RAW提取等功能
"""
import sys
import os
import platform

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPainter, QPixmap, QColor

from DITWorkstation.App import config
from DITWorkstation.App.navigation import get_nav_index
from DITWorkstation.Utils import logger
from DITWorkstation.Views.main_window import (
    MainWindow, set_current_project, set_current_workspace
)
from DITWorkstation.Views.first_run_wizard import maybe_show_wizard
from DITWorkstation.Views.Styles.theme import apply_global_style


def _pick_default_font_family() -> str:
    """按平台选择默认字体族（保证中文与 emoji 都能正确渲染）。

    - macOS：PingFang SC 含中文 + 拉丁字形；emoji 由系统自动回退到 Apple Color Emoji
    - Windows：Microsoft YaHei UI 含中文；emoji 需 Segoe UI Emoji，圆圈数字需 Segoe UI Symbol
    - Linux：Noto Sans CJK SC + Noto Color Emoji
    """
    system = platform.system()
    if system == "Darwin":
        return "PingFang SC"
    elif system == "Windows":
        return "Microsoft YaHei UI"
    else:
        return "Noto Sans CJK SC"


def _pick_default_font_point_size() -> int:
    """按平台选择默认字号（pt）。

    macOS @2x Retina 视觉上 13pt 偏大但符合系统默认；Windows 96 DPI 下 10pt 与系统控件协调。
    """
    system = platform.system()
    if system == "Darwin":
        return 13
    elif system == "Windows":
        return 10
    else:
        return 10


def _ensure_emoji_font_fallback():
    """补充 emoji / 符号字体回退，避免 Windows 上导航 emoji 渲染为缺字方块。

    Qt 在 setFont 后会按 family 链回退，但默认回退表可能不包含 Segoe UI Emoji。
    这里显式将 Segoe UI Emoji / Segoe UI Symbol / Apple Color Emoji 加入回退链。
    仅在对应字体存在时生效。
    """
    system = platform.system()
    families = QFontDatabase.families()
    fallback: list[str] = []
    if system == "Windows":
        for name in ("Segoe UI Emoji", "Segoe UI Symbol"):
            if name in families:
                fallback.append(name)
    elif system == "Darwin":
        if "Apple Color Emoji" in families:
            fallback.append("Apple Color Emoji")
    else:
        for name in ("Noto Color Emoji", "Noto Emoji"):
            if name in families:
                fallback.append(name)

    if fallback:
        # 用 app 全局字体的 substitution 列表插入回退
        app = QApplication.instance()
        if app is not None:
            current = app.font()
            # QFont.setFamilies 在 PySide6 6.x 支持；若不可用则静默跳过
            set_families = getattr(current, "setFamilies", None)
            if callable(set_families):
                primary = current.family()
                set_families([primary] + fallback)


def _create_app_icon() -> QIcon:
    """用代码生成应用图标（主蓝色圆角方块 + 白色 D），避免依赖外部资源文件。

    跨平台一致：macOS Dock、Windows 任务栏/标题栏、打包后可执行文件图标统一。
    """
    size = 256
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # 主蓝色圆角方块背景
    painter.setBrush(QColor("#0a84ff"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    # 白色 "D" 字母
    font = QFont("Helvetica", int(size * 0.6), QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRect(0, -size * 0.03, size, size), Qt.AlignCenter, "D")
    painter.end()
    return QIcon(pixmap)


def main():
    """应用主入口"""
    # 确保必要目录存在
    config.ensure_dirs()

    # 高DPI支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("DIT工作站")
    app.setOrganizationName("DITWorkstation")
    app.setWindowIcon(_create_app_icon())

    # 设置默认字体（跨平台，含中文与 emoji 回退）
    font = QFont(_pick_default_font_family(), _pick_default_font_point_size())
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
    _ensure_emoji_font_fallback()

    # 应用全局 QSS（颜色 / 字号 / 表格 / 输入框 / 滚动条 等统一基础样式）
    apply_global_style(app)

    # 创建并显示主窗口
    window = MainWindow()
    window.show()

    # 首启向导：无任何项目时弹出，引导新用户完成「工作区→项目」初始化
    # 在主窗口 show() 之后调用，让用户先看到主界面再弹向导
    wizard = maybe_show_wizard(parent=window)
    if wizard is not None:
        # 向导创建了工作区与项目：依次设为全局当前工作区/项目，并跳转到「媒体导入」
        if wizard._created_workspace_id:
            set_current_workspace(wizard._created_workspace_id)
        if wizard._created_project_id:
            set_current_project(wizard._created_project_id)
        try:
            window.nav_list.setCurrentRow(get_nav_index("import"))
        except Exception as e:
            logger.debug(f"向导后跳转媒体导入失败: {e}")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
