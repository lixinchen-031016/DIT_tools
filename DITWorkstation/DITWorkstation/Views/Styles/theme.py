"""DITWorkstation 全局主题：颜色 / 字号 / 间距 / 圆角常量 与 GLOBAL_QSS。

设计原则：
1. 单一主色调 #0a84ff；状态色（绿/橙/红）仅作图标/文本指示，不作按钮底色
2. 单一字体回退链：等宽日志字体在 macOS/Windows 均能找到合适字形
3. 统一间距标尺 4 / 8 / 12 / 16 / 24
4. 统一圆角层级：卡片 12 / GroupBox 10 / 按钮 8 / 输入框 6
5. 表格统一样式：交替行色 + 表头浅灰 + 选中主蓝

使用方式：
    from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, GLOBAL_QSS
    btn.setStyleSheet(COLOR.PRIMARY_BUTTON_QSS)
    app.setStyleSheet(GLOBAL_QSS)
"""
from __future__ import annotations

import sys


# ============ 颜色 ============
class COLOR:
    """颜色常量。所有视图的 setStyleSheet 应引用这些常量，禁止内联硬编码。"""

    # 主色
    PRIMARY = "#0a84ff"            # 主操作蓝
    PRIMARY_HOVER = "#0070e0"      # 主操作 hover
    PRIMARY_PRESSED = "#0058b0"    # 主操作按下

    # 状态色（仅作图标/文本/状态指示，不作按钮底色）
    SUCCESS = "#34c759"
    WARNING = "#ff9500"
    DANGER = "#ff3b30"
    DANGER_HOVER = "#ff453a"
    INFO = "#5856d6"

    # 文本
    TEXT_PRIMARY = "#1d1d1f"
    TEXT_SECONDARY = "#86868b"
    TEXT_PLACEHOLDER = "#c7c7cc"

    # 背景
    BG_APP = "#f5f5f7"            # 应用底色
    BG_CARD = "#ffffff"           # 卡片/容器
    BG_GROUP = "#fafafa"          # GroupBox 内部
    BG_HEADER = "#f5f5f7"         # 表头
    BG_ALT_ROW = "#fafafa"        # 表格交替行

    # 边框
    BORDER = "#e5e5ea"
    BORDER_LIGHT = "#f0f0f0"

    # 侧栏
    SIDEBAR_BG = "#2c2c2e"
    SIDEBAR_TEXT = "#ffffff"
    SIDEBAR_HOVER = "#3a3a3c"

    # 横幅
    BANNER_INFO_BG = "#e8f4ff"
    BANNER_INFO_FG = "#004080"
    BANNER_WARNING_BG = "#fff3cd"
    BANNER_WARNING_FG = "#856404"
    BANNER_WARNING_BORDER = "#ffe08a"

    # 禁用
    DISABLED = "#c7c7cc"


# ============ 字号 ============
class FONT_SIZE:
    """字号标尺（px）。全应用只用这 5 档。"""
    XS = 11        # 次要提示 / 状态
    SM = 12        # 表头 / 副要文本
    BASE = 13      # 正文 / 副标题
    MD = 14        # 按钮 / 强调
    LG = 15        # 分组标题
    XL = 22        # 视图标题
    XXL = 28       # 看板大数字


# ============ 间距 ============
class SPACING:
    """间距标尺（px）。"""
    XS = 4
    SM = 8
    MD = 12
    BASE = 16
    LG = 24
    XL = 32

    # 视图统一外边距
    VIEW_MARGIN = (24, 24, 24, 24)


# ============ 圆角 ============
class RADIUS:
    CARD = 12
    GROUP = 10
    BUTTON = 8
    INPUT = 6
    ROW = 4


# ============ 按钮样式 ============
PRIMARY_BUTTON_QSS = f"""
QPushButton {{
    background-color: {COLOR.PRIMARY};
    color: white;
    padding: 10px 24px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {FONT_SIZE.MD}px;
    font-weight: bold;
    border: none;
}}
QPushButton:hover {{ background-color: {COLOR.PRIMARY_HOVER}; }}
QPushButton:pressed {{ background-color: {COLOR.PRIMARY_PRESSED}; }}
QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}
"""

SECONDARY_BUTTON_QSS = f"""
QPushButton {{
    background-color: {COLOR.BG_CARD};
    color: {COLOR.TEXT_PRIMARY};
    padding: 10px 24px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {FONT_SIZE.MD}px;
    border: 1px solid {COLOR.BORDER};
}}
QPushButton:hover {{ background-color: {COLOR.BG_APP}; }}
QPushButton:pressed {{ background-color: {COLOR.BG_HEADER}; }}
QPushButton:disabled {{ color: {COLOR.DISABLED}; border-color: {COLOR.DISABLED}; }}
"""

DANGER_BUTTON_QSS = f"""
QPushButton {{
    background-color: {COLOR.DANGER};
    color: white;
    padding: 8px 16px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {FONT_SIZE.SM}px;
    border: none;
}}
QPushButton:hover {{ background-color: {COLOR.DANGER_HOVER}; }}
QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}
"""


# ============ 视图标题与副标题 ============
TITLE_QSS = f"font-size: {FONT_SIZE.XL}px; font-weight: bold; color: {COLOR.TEXT_PRIMARY};"
SUBTITLE_QSS = f"font-size: {FONT_SIZE.BASE}px; color: {COLOR.TEXT_SECONDARY};"


# ============ 等宽字体（日志/代码区）============
# 按平台选择首字体，并追加 emoji 字体到回退链：
# 自定义 font-family 的控件不继承 app 级 emoji 回退（main.py 的 setFamilies），
# 故需在此显式声明 emoji 字体，避免 Windows 上日志区 emoji 显示为缺字方块。
if sys.platform == "darwin":
    _MONO_PRIMARY = "'Menlo', 'Monaco'"
    _MONO_EMOJI = "'Apple Color Emoji'"
elif sys.platform == "win32":
    _MONO_PRIMARY = "'Cascadia Mono', 'Consolas'"
    _MONO_EMOJI = "'Segoe UI Emoji', 'Segoe UI Symbol'"
else:
    _MONO_PRIMARY = "'DejaVu Sans Mono', 'Liberation Mono'"
    _MONO_EMOJI = "'Noto Color Emoji', 'Noto Emoji'"

MONO_FONT_QSS = (
    f"font-family: {_MONO_PRIMARY}, 'Courier New', {_MONO_EMOJI}, monospace; "
    f"font-size: {FONT_SIZE.XS}px;"
)


# ============ 全局 QSS ============
GLOBAL_QSS = f"""
QWidget {{
    color: {COLOR.TEXT_PRIMARY};
    font-size: {FONT_SIZE.BASE}px;
}}

QGroupBox {{
    background-color: {COLOR.BG_GROUP};
    border: 1px solid {COLOR.BORDER};
    border-radius: {RADIUS.GROUP}px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    font-size: {FONT_SIZE.LG}px;
    font-weight: 600;
    color: {COLOR.TEXT_PRIMARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background-color: {COLOR.BG_GROUP};
}}

QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background-color: {COLOR.BG_CARD};
    border: 1px solid {COLOR.BORDER};
    border-radius: {RADIUS.INPUT}px;
    padding: 6px 8px;
    color: {COLOR.TEXT_PRIMARY};
    selection-background-color: {COLOR.PRIMARY};
    selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {COLOR.PRIMARY};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background-color: {COLOR.BG_APP};
    color: {COLOR.DISABLED};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLOR.BG_CARD};
    border: 1px solid {COLOR.BORDER};
    border-radius: {RADIUS.INPUT}px;
    selection-background-color: {COLOR.PRIMARY};
    selection-color: white;
    outline: none;
}}

/* ============ 表格 ============ */
QTableWidget {{
    background-color: {COLOR.BG_CARD};
    alternate-background-color: {COLOR.BG_ALT_ROW};
    border: 1px solid {COLOR.BORDER};
    border-radius: {RADIUS.BUTTON}px;
    gridline-color: {COLOR.BORDER_LIGHT};
    selection-background-color: {COLOR.PRIMARY};
    selection-color: white;
    outline: none;
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {COLOR.PRIMARY};
    color: white;
}}
QHeaderView::section {{
    background-color: {COLOR.BG_HEADER};
    color: {COLOR.TEXT_PRIMARY};
    padding: 8px 8px;
    border: none;
    border-right: 1px solid {COLOR.BORDER_LIGHT};
    border-bottom: 1px solid {COLOR.BORDER};
    font-weight: 600;
}}
QTableCornerButton::section {{
    background-color: {COLOR.BG_HEADER};
    border: none;
    border-bottom: 1px solid {COLOR.BORDER};
}}

/* ============ 进度条 ============ */
QProgressBar {{
    background-color: {COLOR.BG_APP};
    border: 1px solid {COLOR.BORDER};
    border-radius: {RADIUS.INPUT}px;
    text-align: center;
    color: {COLOR.TEXT_PRIMARY};
    font-size: {FONT_SIZE.SM}px;
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: {COLOR.PRIMARY};
    border-radius: {RADIUS.INPUT}px;
}}

/* ============ 滚动条 ============ */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {COLOR.BORDER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLOR.TEXT_SECONDARY};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {COLOR.BORDER};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {COLOR.TEXT_SECONDARY};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ============ 复选框 ============ */
QCheckBox {{
    spacing: 6px;
    color: {COLOR.TEXT_PRIMARY};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {COLOR.BORDER};
    border-radius: 3px;
    background-color: {COLOR.BG_CARD};
}}
QCheckBox::indicator:hover {{
    border-color: {COLOR.PRIMARY};
}}
QCheckBox::indicator:checked {{
    background-color: {COLOR.PRIMARY};
    border-color: {COLOR.PRIMARY};
    /* 内嵌 SVG 对勾：白色描边，跨平台一致，无需外部资源 */
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><path d='M3 8 L6.5 11.5 L13 4.5' stroke='white' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>");
}}
QCheckBox::indicator:disabled {{
    background-color: {COLOR.BG_APP};
    border-color: {COLOR.DISABLED};
}}

/* ============ 工具提示 ============ */
QToolTip {{
    background-color: #1d1d1f;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 8px;
    font-size: {FONT_SIZE.SM}px;
}}
"""


def apply_global_style(app):
    """在 QApplication 上应用全局 QSS。"""
    app.setStyleSheet(GLOBAL_QSS)
