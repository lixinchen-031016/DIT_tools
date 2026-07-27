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
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from DITWorkstation.App import config
from DITWorkstation.Views.main_window import (
    MainWindow, set_current_project, set_current_workspace
)
from DITWorkstation.Views.first_run_wizard import maybe_show_wizard


def _pick_default_font_family() -> str:
    """按平台选择默认字体族（保证中文显示）"""
    system = platform.system()
    if system == "Darwin":
        return "SF Pro Text"
    elif system == "Windows":
        return "Microsoft YaHei"
    else:
        return "Noto Sans CJK SC"


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

    # 设置默认字体（跨平台）
    font = QFont(_pick_default_font_family(), 13)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)

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
            window.nav_list.setCurrentRow(1)  # 1 = 媒体导入
        except Exception:
            pass

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
