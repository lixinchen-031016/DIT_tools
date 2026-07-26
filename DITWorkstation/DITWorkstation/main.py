"""
DIT工作站 - 应用入口
专业摄影数据管理应用，支持安全备份、校验和验证、JPG筛选RAW提取等功能
"""
import sys
import os

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from DITWorkstation.App import config
from DITWorkstation.Views.main_window import MainWindow


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

    # 设置默认字体
    font = QFont("SF Pro Text", 13)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)

    # 创建并显示主窗口
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
