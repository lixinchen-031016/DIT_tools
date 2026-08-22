"""DITWorkstation 全局主题：颜色 / 字号 / 间距 / 圆角常量 与 GLOBAL_QSS。

适配浅色/深色双主题模式。主题切换写入配置，重启后生效（与使用场景模式一致）。

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

import os
import sys
import tempfile
from dataclasses import dataclass

# ============ 主题调色板 ============

@dataclass
class ThemePalette:
    """单个主题的调色板。"""
    # 主色
    PRIMARY: str = "#0a84ff"
    PRIMARY_HOVER: str = "#0070e0"
    PRIMARY_PRESSED: str = "#0058b0"

    # 状态色
    SUCCESS: str = "#34c759"
    WARNING: str = "#ff9500"
    DANGER: str = "#ff3b30"
    DANGER_HOVER: str = "#ff453a"
    INFO: str = "#5856d6"

    # 文本
    TEXT_PRIMARY: str = "#1d1d1f"
    TEXT_SECONDARY: str = "#86868b"
    TEXT_PLACEHOLDER: str = "#c7c7cc"

    # 背景
    BG_APP: str = "#f5f5f7"
    BG_CARD: str = "#ffffff"
    BG_GROUP: str = "#fafafa"
    BG_HEADER: str = "#f5f5f7"
    BG_ALT_ROW: str = "#fafafa"

    # 边框
    BORDER: str = "#e5e5ea"
    BORDER_LIGHT: str = "#f0f0f0"

    # 侧栏
    SIDEBAR_BG: str = "#2c2c2e"
    SIDEBAR_TEXT: str = "#ffffff"
    SIDEBAR_HOVER: str = "#3a3a3c"

    # 横幅
    BANNER_INFO_BG: str = "#e8f4ff"
    BANNER_INFO_FG: str = "#004080"
    BANNER_WARNING_BG: str = "#fff3cd"
    BANNER_WARNING_FG: str = "#856404"
    BANNER_WARNING_BORDER: str = "#ffe08a"

    # 禁用
    DISABLED: str = "#c7c7cc"


LIGHT_PALETTE = ThemePalette()

DARK_PALETTE = ThemePalette(
    PRIMARY="#0a84ff",
    PRIMARY_HOVER="#1f93ff",
    PRIMARY_PRESSED="#0066cc",
    SUCCESS="#30d158",
    WARNING="#ff9f0a",
    DANGER="#ff453a",
    DANGER_HOVER="#ff6961",
    INFO="#5e5ce6",
    TEXT_PRIMARY="#f2f2f7",
    TEXT_SECONDARY="#98989d",
    TEXT_PLACEHOLDER="#6e6e73",
    BG_APP="#121214",
    BG_CARD="#1c1c1e",
    BG_GROUP="#232326",
    BG_HEADER="#2c2c2e",
    BG_ALT_ROW="#1f1f21",
    BORDER="#3a3a3c",
    BORDER_LIGHT="#2c2c2e",
    SIDEBAR_BG="#1a1a1b",
    SIDEBAR_TEXT="#ffffff",
    SIDEBAR_HOVER="#2a2a2b",
    BANNER_INFO_BG="#0d2137",
    BANNER_INFO_FG="#64b5f6",
    BANNER_WARNING_BG="#2b2000",
    BANNER_WARNING_FG="#ffd54f",
    BANNER_WARNING_BORDER="#6b5400",
    DISABLED="#6e6e73",
)


# ============ 当前激活调色板（模块级引用，视图在调用 setStyleSheet 时读取）============

_ACTIVE_PALETTE: ThemePalette = LIGHT_PALETTE


class COLOR_PROXY:
    """颜色代理。访问 COLOR.X 时从当前激活调色板读取。"""

    def __getattr__(self, name):
        return getattr(_ACTIVE_PALETTE, name)


COLOR = COLOR_PROXY()


# ============ 字号 ============
class FONT_SIZE:
    XS = 11
    SM = 12
    BASE = 13
    MD = 14
    LG = 15
    XL = 22
    XXL = 28


# ============ 间距 ============
class SPACING:
    XS = 4
    SM = 8
    MD = 12
    BASE = 16
    LG = 24
    XL = 32
    VIEW_MARGIN = (24, 24, 24, 24)


# ============ 圆角 ============
class RADIUS:
    CARD = 12
    GROUP = 10
    BUTTON = 8
    INPUT = 6
    ROW = 4


# ============ 等宽字体 ============
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


# ============ 构建 QSS ============

def _build_qss() -> str:
    """从当前激活调色板构建全局 QSS 字符串。"""
    C = _ACTIVE_PALETTE
    FS = FONT_SIZE
    R = RADIUS
    return f"""
QWidget {{
    color: {C.TEXT_PRIMARY};
    font-size: {FS.BASE}px;
}}

QGroupBox {{
    background-color: {C.BG_GROUP};
    border: 1px solid {C.BORDER};
    border-radius: {R.GROUP}px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    font-size: {FS.LG}px;
    font-weight: 600;
    color: {C.TEXT_PRIMARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background-color: {C.BG_GROUP};
}}

QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background-color: {C.BG_CARD};
    border: 1px solid {C.BORDER};
    border-radius: {R.INPUT}px;
    padding: 6px 8px;
    color: {C.TEXT_PRIMARY};
    selection-background-color: {C.PRIMARY};
    selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {C.PRIMARY};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background-color: {C.BG_APP};
    color: {C.DISABLED};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {C.BG_CARD};
    border: 1px solid {C.BORDER};
    border-radius: {R.INPUT}px;
    selection-background-color: {C.PRIMARY};
    selection-color: white;
    outline: none;
}}

QTableWidget {{
    background-color: {C.BG_CARD};
    alternate-background-color: {C.BG_ALT_ROW};
    border: 1px solid {C.BORDER};
    border-radius: {R.BUTTON}px;
    gridline-color: {C.BORDER_LIGHT};
    selection-background-color: {C.PRIMARY};
    selection-color: white;
    outline: none;
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {C.PRIMARY};
    color: white;
}}
QHeaderView::section {{
    background-color: {C.BG_HEADER};
    color: {C.TEXT_PRIMARY};
    padding: 8px 8px;
    border: none;
    border-right: 1px solid {C.BORDER_LIGHT};
    border-bottom: 1px solid {C.BORDER};
    font-weight: 600;
}}
QTableCornerButton::section {{
    background-color: {C.BG_HEADER};
    border: none;
    border-bottom: 1px solid {C.BORDER};
}}

QProgressBar {{
    background-color: {C.BG_APP};
    border: 1px solid {C.BORDER};
    border-radius: {R.INPUT}px;
    text-align: center;
    color: {C.TEXT_PRIMARY};
    font-size: {FS.SM}px;
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: {C.PRIMARY};
    border-radius: {R.INPUT}px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C.BORDER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C.TEXT_SECONDARY};
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
    background: {C.BORDER};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C.TEXT_SECONDARY};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QCheckBox {{
    spacing: 6px;
    color: {C.TEXT_PRIMARY};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {C.BORDER};
    border-radius: 3px;
    background-color: {C.BG_CARD};
}}
QCheckBox::indicator:hover {{
    border-color: {C.PRIMARY};
}}
QCheckBox::indicator:checked {{
    background-color: {C.PRIMARY};
    border-color: {C.PRIMARY};
    image: url("__CHECKMARK_SVG_PATH__");
}}
QCheckBox::indicator:disabled {{
    background-color: {C.BG_APP};
    border-color: {C.DISABLED};
}}

QToolTip {{
    background-color: #1d1d1f;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 8px;
    font-size: {FS.SM}px;
}}
"""


# ============ 模块级 QSS 常量（由当前激活调色板构建）============

GLOBAL_QSS = _build_qss()

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

TITLE_QSS = f"font-size: {FONT_SIZE.XL}px; font-weight: bold; color: {COLOR.TEXT_PRIMARY};"
SUBTITLE_QSS = f"font-size: {FONT_SIZE.BASE}px; color: {COLOR.TEXT_SECONDARY};"


# ============ 主题切换 API ============

def set_theme_mode(mode: str) -> None:
    """设置当前主题并重建模块级 QSS 常量。

    调用方（main.py）在启动时读取 config.theme_mode 后调用此函数，
    再创建 MainWindow，确保视图构造时 COLOR 已指向正确调色板。

    Args:
        mode: "light" 或 "dark"
    """
    global _ACTIVE_PALETTE, GLOBAL_QSS, PRIMARY_BUTTON_QSS
    global SECONDARY_BUTTON_QSS, DANGER_BUTTON_QSS, TITLE_QSS, SUBTITLE_QSS
    if mode == "dark":
        _ACTIVE_PALETTE = DARK_PALETTE
    else:
        _ACTIVE_PALETTE = LIGHT_PALETTE
    # 重建所有模块级 QSS 常量
    GLOBAL_QSS = _build_qss()
    PRIMARY_BUTTON_QSS = f"""QPushButton {{
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
QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}"""
    SECONDARY_BUTTON_QSS = f"""QPushButton {{
    background-color: {COLOR.BG_CARD};
    color: {COLOR.TEXT_PRIMARY};
    padding: 10px 24px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {FONT_SIZE.MD}px;
    border: 1px solid {COLOR.BORDER};
}}
QPushButton:hover {{ background-color: {COLOR.BG_APP}; }}
QPushButton:pressed {{ background-color: {COLOR.BG_HEADER}; }}
QPushButton:disabled {{ color: {COLOR.DISABLED}; border-color: {COLOR.DISABLED}; }}"""
    DANGER_BUTTON_QSS = f"""QPushButton {{
    background-color: {COLOR.DANGER};
    color: white;
    padding: 8px 16px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {FONT_SIZE.SM}px;
    border: none;
}}
QPushButton:hover {{ background-color: {COLOR.DANGER_HOVER}; }}
QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}"""
    TITLE_QSS = f"font-size: {FONT_SIZE.XL}px; font-weight: bold; color: {COLOR.TEXT_PRIMARY};"
    SUBTITLE_QSS = f"font-size: {FONT_SIZE.BASE}px; color: {COLOR.TEXT_SECONDARY};"


def get_theme_mode() -> str:
    """返回当前主题模式名称。"""
    if _ACTIVE_PALETTE is DARK_PALETTE:
        return "dark"
    return "light"


# ============ 对勾 SVG ============

def _ensure_checkmark_svg() -> str:
    """生成复选框对勾 SVG 临时文件并返回其路径。"""
    tmp_dir = tempfile.gettempdir()
    svg_path = os.path.join(tmp_dir, "dit_checkmark.svg")
    if not os.path.exists(svg_path):
        svg_content = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' "
            "viewBox='0 0 16 16'>"
            "<path d='M3 8 L6.5 11.5 L13 4.5' stroke='white' stroke-width='2.5' "
            "stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>"
        )
        try:
            tmp_path = svg_path + ".tmp." + str(os.getpid())
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(svg_content)
            os.replace(tmp_path, svg_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except (OSError, NameError):
                pass
            return ""
    return svg_path.replace("\\", "/")


def apply_global_style(app):
    """在 QApplication 上应用全局 QSS。"""
    checkmark_path = _ensure_checkmark_svg()
    qss = GLOBAL_QSS
    if checkmark_path:
        qss = qss.replace("__CHECKMARK_SVG_PATH__", checkmark_path)
    app.setStyleSheet(qss)
