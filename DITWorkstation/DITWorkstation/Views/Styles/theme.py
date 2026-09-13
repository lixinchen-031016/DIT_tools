"""DITWorkstation 全局主题：颜色 / 字号 / 间距 / 圆角常量 与 GLOBAL_QSS。

适配浅色/深色双主题模式。主题切换写入配置，重启后生效（与使用场景模式一致）。

设计原则（对应《UI 现代化优化建议》§5.1/§5.2/§5.3/§8.1/§8.3）：
1. 单一主色调 #0a84ff；状态色（绿/橙/红）仅作图标/文本指示，不作按钮底色
2. 单一字体回退链：等宽日志字体在 macOS/Windows 均能找到合适字形
3. 统一间距标尺 4 / 8 / 12 / 16 / 24 / 32
4. 统一圆角层级：卡片 10 / GroupBox 10 / 按钮 8 / 输入框 6（§5.3 卡片 6-10px）
5. 表格统一样式：交替行色 + 表头浅灰 + 选中主蓝
6. 调色板显式参数化：QSS 构建函数接收 ThemePalette，深色主题为独立调色板
   而非浅色简单反转（§5.1/§8.3）

使用方式：
    from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, GLOBAL_QSS
    btn.setStyleSheet(COLOR.PRIMARY_BUTTON_QSS)
    app.setStyleSheet(GLOBAL_QSS)

主题切换（未来接入设置页时）：
    from DITWorkstation.Views.Styles.theme import (
        DARK_PALETTE, set_active_palette, apply_global_style,
    )
    set_active_palette(DARK_PALETTE)   # 更新 COLOR 并重建全部 QSS 常量
    apply_global_style(app)            # 重新应用到 QApplication
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
from dataclasses import dataclass

# ============ 主题调色板（§5.1：浅色为基准，深色为独立调色板）============


@dataclass
class ThemePalette:
    """单个主题的调色板。所有 QSS 均从调色板字段构建，不硬编码颜色。"""

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

    # 背景（应用背景 / 表面 / 分组表面 / 表头 / 交替行）
    BG_APP: str = "#f5f5f7"
    BG_CARD: str = "#ffffff"
    BG_GROUP: str = "#fafafa"
    BG_HEADER: str = "#f5f5f7"
    BG_ALT_ROW: str = "#fafafa"

    # 边框（低对比度，优先用间距建立分区）
    BORDER: str = "#e5e5ea"
    BORDER_LIGHT: str = "#f0f0f0"

    # 侧栏
    SIDEBAR_BG: str = "#2c2c2e"
    SIDEBAR_TEXT: str = "#ffffff"
    SIDEBAR_HOVER: str = "#3a3a3c"

    # 横幅（信息 / 成功 / 警告 / 错误：浅色背景 + 语义色文字 + 1px 语义色边框，§8.2）
    BANNER_INFO_BG: str = "#e8f4ff"
    BANNER_INFO_FG: str = "#004080"
    BANNER_INFO_BORDER: str = "#b8d9ff"
    BANNER_SUCCESS_BG: str = "#e3f7e9"
    BANNER_SUCCESS_FG: str = "#1e6b34"
    BANNER_SUCCESS_BORDER: str = "#a8dfba"
    BANNER_WARNING_BG: str = "#fff3cd"
    BANNER_WARNING_FG: str = "#856404"
    BANNER_WARNING_BORDER: str = "#ffe08a"
    BANNER_ERROR_BG: str = "#fdecea"
    BANNER_ERROR_FG: str = "#a8201a"
    BANNER_ERROR_BORDER: str = "#f5b5b0"

    # 禁用
    DISABLED: str = "#c7c7cc"


LIGHT_PALETTE = ThemePalette()


DARK_PALETTE = ThemePalette(
    # 主色：深色下略微提亮 hover / 压暗 pressed 以保持层级
    PRIMARY="#0a84ff",
    PRIMARY_HOVER="#3395ff",
    PRIMARY_PRESSED="#0060c0",
    # 状态色：适配深色背景的高亮度变体（Apple 深色系统色）
    SUCCESS="#30d158",
    WARNING="#ff9f0a",
    DANGER="#ff453a",
    DANGER_HOVER="#ff6961",
    INFO="#7d7aff",
    # 文本：浅色主文本 / 次文本（不简单反转浅色主题）
    TEXT_PRIMARY="#f5f5f7",
    TEXT_SECONDARY="#98989d",
    TEXT_PLACEHOLDER="#636366",
    # 背景：应用背景 / 表面 / 分组表面 / 表头 / 交替行（深灰层级）
    BG_APP="#1e1e20",
    BG_CARD="#2a2a2c",
    BG_GROUP="#232325",
    BG_HEADER="#2a2a2c",
    BG_ALT_ROW="#2e2e30",
    # 边框：低对比度深灰
    BORDER="#3a3a3c",
    BORDER_LIGHT="#323234",
    # 侧栏：随主题微调，保持与内容区可区分
    SIDEBAR_BG="#252527",
    SIDEBAR_TEXT="#f5f5f7",
    SIDEBAR_HOVER="#3a3a3c",
    # 横幅：深色底 + 高亮度语义色文字
    BANNER_INFO_BG="#1c2a3a",
    BANNER_INFO_FG="#7cc4ff",
    BANNER_INFO_BORDER="#2c4a6e",
    BANNER_SUCCESS_BG="#1c2f22",
    BANNER_SUCCESS_FG="#4cd97b",
    BANNER_SUCCESS_BORDER="#2c5c3a",
    BANNER_WARNING_BG="#33270d",
    BANNER_WARNING_FG="#ffb84d",
    BANNER_WARNING_BORDER="#6e5218",
    BANNER_ERROR_BG="#331a18",
    BANNER_ERROR_FG="#ff7a72",
    BANNER_ERROR_BORDER="#6e2f2a",
    # 禁用
    DISABLED="#636366",
)


# ============ 当前激活调色板（§8.3）============
# COLOR 是独立于 LIGHT_PALETTE 的实例：视图代码 `from theme import COLOR` 拿到的是
# 同一个对象的引用，set_active_palette 通过原地更新字段（__dict__.update）保证
# 所有已导入 COLOR 的模块无需重新 import 即可读到新配色。

COLOR = ThemePalette()  # 默认与浅色调色板一致


# ============ 字号（§5.2：页面标题 22 / 区块标题 15 / 正文 13 / 辅助 12）============
class FONT_SIZE:
    XS = 11
    SM = 12
    BASE = 13
    MD = 14
    LG = 15
    XL = 22
    XXL = 28


# ============ 语义字号（§5.2 五级文本层级的令牌化表达）============
class TYPO:
    """语义字号令牌：页面标题 / 区块标题 / 正文 / 辅助说明。"""

    PAGE_TITLE = 22  # 页面标题（与 FONT_SIZE.XL 一致）
    SECTION_TITLE = 15  # 区块标题（与 FONT_SIZE.LG 一致）
    BODY = 13  # 正文（与 FONT_SIZE.BASE 一致）
    CAPTION = 12  # 辅助说明 / 图注（不低于 12px）
    # 等宽字体 QSS：路径、日志、校验和等技术信息（§5.2），复用 MONO_FONT_QSS


# ============ 控件尺寸（§5.3：按钮/输入框 32-36px、图标 16/20/24、行高 36-40px）============
class CONTROL:
    """控件尺寸令牌：统一高度、图标档位与表格行高。"""

    HEIGHT = 34  # 普通按钮 / 输入框统一高度（32-36px 区间中值）
    HEIGHT_LG = 38  # 高频主操作高度（36-40px 区间）
    ICON_SM = 16  # 小图标（行内状态、按钮内图标）
    ICON_MD = 20  # 中图标（工具按钮默认）
    ICON_LG = 24  # 大图标（空状态、导航）
    TABLE_ROW = 38  # 表格行高建议值（36-40px 区间）


# ============ 间距（§5.3：4 的倍数标尺）============
class SPACING:
    XS = 4  # 图标与文字、紧邻状态元素
    SM = 8  # 控件内间距、同一行控件之间
    MD = 12  # 表单字段、表格单元、列表项
    BASE = 16  # 区块内边距、页面常规间距
    LG = 24  # 页面左右留白、主要区块间距
    XL = 32  # 顶部区域、不同任务区域之间
    VIEW_MARGIN = (24, 24, 24, 24)


# ============ 圆角（§5.3：卡片 6-10px，减少不必要的阴影和大圆角）============
class RADIUS:
    CARD = 10  # 卡片（报告建议 6-10px，原 12 收敛为 10）
    GROUP = 10  # GroupBox
    BUTTON = 8  # 按钮
    INPUT = 6  # 输入框 / 工具按钮
    ROW = 4  # 表格行 / 横幅等行级元素


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

# 等宽字体语义令牌（§5.2 typography.mono）：直接复用 MONO_FONT_QSS
TYPO.MONO = MONO_FONT_QSS


# ============ 构建 QSS（§8.3：调色板显式参数化，不再固定引用浅色）============


def _build_qss(palette: ThemePalette | None = None) -> str:
    """从给定调色板构建全局 QSS 字符串。

    Args:
        palette: 目标调色板；为 None 时使用当前激活调色板 COLOR。
    """
    C = palette if palette is not None else COLOR
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


def build_qss(palette: ThemePalette | None = None) -> str:
    """公开接口：从指定调色板构建全局 QSS（§8.3）。"""
    return _build_qss(palette)


def get_global_qss(palette: ThemePalette | None = None) -> str:
    """获取全局 QSS：palette 为 None 时基于当前激活调色板。"""
    return _build_qss(palette)


# ============ 按钮四级变体（§7.1：主 / 次 / 工具 / 危险）============
# padding 8px 20px + font-size 13px 使普通按钮高度约等于 CONTROL.HEIGHT (34px)。


def _primary_button_qss(palette: ThemePalette) -> str:
    """主按钮：页面唯一实心主操作。"""
    return f"""
QPushButton {{
    background-color: {palette.PRIMARY};
    color: white;
    padding: 8px 20px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {TYPO.BODY}px;
    font-weight: bold;
    border: none;
}}
QPushButton:hover {{ background-color: {palette.PRIMARY_HOVER}; }}
QPushButton:pressed {{ background-color: {palette.PRIMARY_PRESSED}; }}
QPushButton:disabled {{ background-color: {palette.DISABLED}; }}
"""


def _secondary_button_qss(palette: ThemePalette) -> str:
    """次按钮：浏览 / 预览 / 保存模板等辅助动作。"""
    return f"""
QPushButton {{
    background-color: {palette.BG_CARD};
    color: {palette.TEXT_PRIMARY};
    padding: 8px 20px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {TYPO.BODY}px;
    border: 1px solid {palette.BORDER};
}}
QPushButton:hover {{ background-color: {palette.BG_APP}; }}
QPushButton:pressed {{ background-color: {palette.BG_HEADER}; }}
QPushButton:disabled {{ color: {palette.DISABLED}; border-color: {palette.DISABLED}; }}
"""


def _danger_button_qss(palette: ThemePalette) -> str:
    """危险按钮：删除 / 清空 / 覆盖等不可逆操作，置于次要位置。"""
    return f"""
QPushButton {{
    background-color: {palette.DANGER};
    color: white;
    padding: 8px 20px;
    border-radius: {RADIUS.BUTTON}px;
    font-size: {TYPO.BODY}px;
    border: none;
}}
QPushButton:hover {{ background-color: {palette.DANGER_HOVER}; }}
QPushButton:pressed {{ background-color: {palette.DANGER}; }}
QPushButton:disabled {{ background-color: {palette.DISABLED}; }}
"""


def _tool_button_qss(palette: ThemePalette) -> str:
    """工具按钮（§7.1）：无边框扁平，用于刷新 / 打开目录 / 复制路径等低干扰动作。

    供 QToolButton 使用：透明背景，hover 浅色底，颜色为次要文本，
    hover 时提升为主文本以提示可交互。
    """
    return f"""
QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS.INPUT}px;
    padding: 6px;
    color: {palette.TEXT_SECONDARY};
    font-size: {TYPO.BODY}px;
}}
QToolButton:hover {{
    background-color: {palette.BORDER_LIGHT};
    color: {palette.TEXT_PRIMARY};
}}
QToolButton:pressed {{ background-color: {palette.BORDER}; }}
QToolButton:disabled {{ color: {palette.DISABLED}; }}
"""


def _title_qss(palette: ThemePalette) -> str:
    """页面标题样式（§5.2 PAGE_TITLE 22px 粗体）。"""
    return f"font-size: {TYPO.PAGE_TITLE}px; font-weight: bold; color: {palette.TEXT_PRIMARY};"


def _subtitle_qss(palette: ThemePalette) -> str:
    """页面副标题样式（§5.2 BODY 13px 次要色）。"""
    return f"font-size: {TYPO.BODY}px; color: {palette.TEXT_SECONDARY};"


# ============ 模块级 QSS 常量（基于当前激活调色板构建，向后兼容）============

GLOBAL_QSS = _build_qss(LIGHT_PALETTE)

PRIMARY_BUTTON_QSS = _primary_button_qss(COLOR)
SECONDARY_BUTTON_QSS = _secondary_button_qss(COLOR)
DANGER_BUTTON_QSS = _danger_button_qss(COLOR)
TOOL_BUTTON_QSS = _tool_button_qss(COLOR)

TITLE_QSS = _title_qss(COLOR)
SUBTITLE_QSS = _subtitle_qss(COLOR)

def set_active_palette(palette: ThemePalette) -> None:
    """切换当前激活调色板并重建所有 QSS 常量（§8.3，本次仅建立机制）。

    COLOR 保持同一对象引用并原地更新字段，因此视图代码中
    `from theme import COLOR` 拿到的引用无需重新 import 即可读到新配色。
    切换后需调用 apply_global_style(app) 将新 QSS 应用到 QApplication。

    Args:
        palette: 目标调色板（LIGHT_PALETTE 或 DARK_PALETTE）。
    """
    # 原地更新：保证 COLOR 引用语义稳定（from-import 拿到的是同一对象）
    COLOR.__dict__.update(dataclasses.asdict(palette))
    # 重建全部模块级 QSS 常量
    rebuilt = {
        "GLOBAL_QSS": _build_qss(COLOR),
        "PRIMARY_BUTTON_QSS": _primary_button_qss(COLOR),
        "SECONDARY_BUTTON_QSS": _secondary_button_qss(COLOR),
        "DANGER_BUTTON_QSS": _danger_button_qss(COLOR),
        "TOOL_BUTTON_QSS": _tool_button_qss(COLOR),
        "TITLE_QSS": _title_qss(COLOR),
        "SUBTITLE_QSS": _subtitle_qss(COLOR),
    }
    globals().update(rebuilt)


# ============ 对勾 SVG ============


def _ensure_checkmark_svg() -> str:
    """生成复选框对勾 SVG 临时文件并返回其路径。"""
    tmp_dir = tempfile.gettempdir()
    svg_path = os.path.join(tmp_dir, "dit_checkmark.svg")
    tmp_path = ""
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


def apply_global_style(app, palette: ThemePalette | None = None) -> None:
    """在 QApplication 上应用全局 QSS。

    Args:
        app: QApplication 实例。
        palette: 可选调色板；None 时使用当前激活调色板（保持原有行为）。
    """
    checkmark_path = _ensure_checkmark_svg()
    qss = _build_qss(palette)
    if checkmark_path:
        qss = qss.replace("__CHECKMARK_SVG_PATH__", checkmark_path)
    app.setStyleSheet(qss)
