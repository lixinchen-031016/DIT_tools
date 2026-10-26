"""应用内用户手册查看器（帮助菜单 → 用户手册…）。

渲染 ``docs/用户手册.md``（QTextBrowser + Markdown），手册文件随文档源
同步更新——程序内帮助不再是孤立的对话框文案，而与仓库文档保持一致。

文件查找顺序：
1. 打包态：``sys._MEIPASS``（PyInstaller onefile / onedir 的解包目录，
   macOS onedir 为 Contents/Frameworks；PyInstaller 6 Windows onedir
   数据可能位于 ``_internal/docs``）
2. 开发态：包目录上三级的 ``docs/``（仓库 ``DITWorkstation/docs/``）
3. 兜底：仓库根下 ``DITWorkstation/docs/``

均未命中时显示路径说明而非静默空白，便于定位分发问题。
"""

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextBrowser,
    QVBoxLayout,
)

from DITWorkstation.Utils import logger
from DITWorkstation.Views.Styles.theme import FONT_SIZE

_MANUAL_FILENAME = "用户手册.md"


def _manual_candidates() -> list[Path]:
    """按优先级返回手册文件的可能位置。"""
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        base = Path(bundle_root)
        candidates += [
            base / "docs" / _MANUAL_FILENAME,
            base / "_internal" / "docs" / _MANUAL_FILENAME,
        ]
    here = Path(__file__).resolve()
    # here = .../DITWorkstation/DITWorkstation/Views/Widgets/help_viewer_dialog.py
    # parents[3] = 仓库的 DITWorkstation/（docs/ 在其下）
    candidates.append(here.parents[3] / "docs" / _MANUAL_FILENAME)
    # 兜底：仓库根 / DITWorkstation / docs
    candidates.append(here.parents[4] / "DITWorkstation" / "docs" / _MANUAL_FILENAME)
    return candidates


def locate_manual() -> Path | None:
    """定位用户手册文件；找不到返回 None。"""
    for candidate in _manual_candidates():
        if candidate.is_file():
            return candidate
    return None


class HelpViewerDialog(QDialog):
    """用户手册查看器：Markdown 渲染 + 关键字查找。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DIT 工作站 — 用户手册")
        self.setMinimumSize(860, 640)
        self.resize(920, 720)

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("查找:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入关键字，回车查找下一个")
        self.search_edit.returnPressed.connect(self._find_next)
        search_row.addWidget(self.search_edit, 1)
        layout.addLayout(search_row)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        layout.addWidget(self.browser, 1)

        self._manual_path: Path | None = locate_manual()
        if self._manual_path is None:
            searched = "\n".join(f"  • {p}" for p in _manual_candidates())
            self.browser.setHtml(
                "<h3>未找到用户手册文件</h3>"
                "<p>已按以下顺序查找 <code>用户手册.md</code>：</p>"
                f"<pre>{searched}</pre>"
                "<p>打包产物缺少该文件时，请确认构建脚本的 datas 配置包含 docs 目录。</p>"
            )
            logger.warning("用户手册文件未找到，查看器显示路径说明")
        else:
            try:
                markdown = self._manual_path.read_text(encoding="utf-8")
            except OSError as e:
                logger.warning(f"读取用户手册失败 {self._manual_path}: {e}")
                self.browser.setPlainText(f"读取用户手册失败：{e}")
            else:
                self.browser.document().setMarkdown(markdown)
                self.browser.setStyleSheet(
                    f"QTextBrowser {{ font-size: {FONT_SIZE.BASE + 1}px; }}"
                )

    def _find_next(self):
        """回车查找下一个匹配；到文件末尾后自动回到顶部再找。"""
        text = self.search_edit.text().strip()
        if not text:
            return
        if not self.browser.find(text):
            cursor = self.browser.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.browser.setTextCursor(cursor)
            self.browser.find(text)  # 仍无匹配则保持原位
