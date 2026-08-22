"""Ctrl+K 全局命令面板：类似 Windows 运行对话框（Win+R）。

设计：
- 紧凑对话框：居中搜索框 + 下拉结果列表
- 输入时按 200ms 防抖搜索
- 结果按类别分组：工作区 / 项目 / 素材 / 操作 / 运行
- 上下键选择，Enter 运行/跳转；Esc 关闭
- 直接运行：输入文件/目录路径（文件管理器打开）或 URL（浏览器打开）
- 个人模式自动隐藏 log/report 导航项
- 不需要额外依赖，纯 Qt 实现
"""

from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from DITWorkstation.App.feature_flags import is_nav_enabled
from DITWorkstation.App.session_context import (
    set_current_project,
    set_current_workspace,
)
from DITWorkstation.Utils import get_db_service, logger, open_in_file_manager
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS


class CommandPalette(QDialog):
    """全局命令面板（Ctrl+K）。"""

    NAVIGATION_ACTIONS: ClassVar[list[tuple[str, str]]] = [
        ("🏠 项目概览", "dashboard"),
        ("📁 媒体导入", "import"),
        ("📦 数据备份", "backup"),
        ("🎞 RAW 提取", "raw"),
        ("✏️ 文件重命名", "rename"),
        ("📋 拍摄日志", "log"),
        ("🔍 素材检索", "search"),
        ("ℹ️ 素材信息", "asset_info"),
        ("📊 报告生成", "report"),
    ]

    # 执行命令：直接「运行」常用入口
    RUN_ACTIONS: ClassVar[list[tuple[str, str]]] = [
        ("🖥 打开文件管理器…", "open_file_manager"),
        ("📂 打开日志目录…", "open_log_dir"),
        ("⚙️ 打开设置…", "open_settings"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.Popup)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.db_service = get_db_service()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._do_search)
        self._all_items: list[dict] = []
        self._setup_ui()
        self._load_all()

        if parent:
            pw, ph = parent.width(), parent.height()
            self.resize(min(560, int(pw * 0.5)), min(360, int(ph * 0.35)))
            self.move(
                parent.x() + (pw - self.width()) // 2,
                parent.y() + int(ph * 0.12),
            )
        else:
            self.resize(480, 320)

    # ===== UI =====

    def _setup_ui(self):
        # 用布局 + 边距承载圆角卡片，避免手动 setGeometry 在 resize 后失效
        self.setContentsMargins(0, 0, 0, 0)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        main = QWidget()
        main.setObjectName("palette_main")
        main.setStyleSheet(f"""
            QWidget#palette_main {{
                background: {COLOR.BG_CARD};
                border: 1px solid {COLOR.BORDER};
                border-radius: {RADIUS.CARD}px;
            }}
        """)
        outer.addWidget(main)

        layout = QVBoxLayout(main)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        # 输入行：类似 Windows 运行对话框的「▶」提示 + 输入框
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        run_label = QLabel("▶")
        run_label.setStyleSheet(
            f"font-size: {FONT_SIZE.LG}px; color: {COLOR.TEXT_SECONDARY}; background: transparent;"
        )
        input_row.addWidget(run_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入路径、URL 或搜索关键字…")
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                font-size: {FONT_SIZE.LG}px;
                padding: 8px 12px;
                border: 2px solid {COLOR.PRIMARY};
                border-radius: {RADIUS.INPUT}px;
                background: {COLOR.BG_CARD};
                color: {COLOR.TEXT_PRIMARY};
                selection-background-color: {COLOR.PRIMARY};
                selection-color: white;
            }}
            QLineEdit::placeholder {{
                color: {COLOR.TEXT_PLACEHOLDER};
            }}
        """)
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.returnPressed.connect(self._on_return)
        input_row.addWidget(self.search_input, 1)
        layout.addLayout(input_row)

        self.hint_label = QLabel("↑↓ 选择   Enter 运行/跳转   Esc 关闭")
        self.hint_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.XS}px;"
            " background: transparent; padding: 0 4px;"
        )
        layout.addWidget(self.hint_label)

        self.result_list = QListWidget()
        self.result_list.setStyleSheet(f"""
            QListWidget {{
                border: none;
                background: {COLOR.BG_CARD};
                color: {COLOR.TEXT_PRIMARY};
                font-size: {FONT_SIZE.BASE}px;
                outline: none;
            }}
            QListWidget::item {{
                color: {COLOR.TEXT_PRIMARY};
                padding: 6px 10px;
                border-radius: {RADIUS.ROW}px;
            }}
            QListWidget::item:hover {{
                background: {COLOR.BG_APP};
            }}
            QListWidget::item:selected {{
                background: {COLOR.PRIMARY};
                color: white;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {COLOR.BORDER};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)
        self.result_list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.result_list, 1)

        self.search_input.setFocus()

    # ===== 键盘交互 =====

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_Down:
            n = self.result_list.count()
            if n > 0:
                idx = (self.result_list.currentRow() + 1) % n
                self.result_list.setCurrentRow(idx)
        elif event.key() == Qt.Key_Up:
            n = self.result_list.count()
            if n > 0:
                idx = (self.result_list.currentRow() - 1) % n
                self.result_list.setCurrentRow(idx)
        else:
            super().keyPressEvent(event)

    def _on_text_changed(self, _text):
        self._debounce.start(200)

    def _on_return(self):
        if self.result_list.count() == 0:
            self._run_direct(self.search_input.text().strip())
            return
        item = self.result_list.currentItem() or self.result_list.item(0)
        self._activate(item)

    def _on_item_clicked(self, item):
        self._activate(item)

    # ===== 搜索 =====

    def _do_search(self):
        keyword = self.search_input.text().strip()
        self.result_list.clear()
        if not keyword:
            self._load_all()
            return
        kw = keyword.lower()
        for item in self._all_items:
            if kw in item["label"].lower() or kw in item.get("meta", "").lower():
                self._add_item(item)

        # 输入为现有路径或 URL 时，附加「直接运行」提示行
        if not self.result_list.count():
            p = Path(keyword)
            if p.exists() or "://" in keyword or keyword.startswith("www."):
                w = QListWidgetItem(f"⚡ 直接运行: {keyword}")
                w.setData(Qt.UserRole, {"type": "direct", "text": keyword})
                self.result_list.addItem(w)

    def _load_all(self):
        self.result_list.clear()
        self._all_items = []
        for label, key in self.NAVIGATION_ACTIONS:
            # 个人模式隐藏 log/report 导航项
            if key in ("log", "report") and not is_nav_enabled(key):
                continue
            self._all_items.append({"type": "nav", "label": label, "key": key})
        for label, key in self.RUN_ACTIONS:
            self._all_items.append({"type": "run", "label": label, "key": key})
        try:
            for ws in self.db_service.get_workspaces():
                self._all_items.append(
                    {
                        "type": "workspace",
                        "label": f"📁 {ws.name}",
                        "meta": ws.path,
                        "id": ws.workspace_id,
                    }
                )
        except Exception as exc:
            logger.debug(f"命令面板加载工作区失败: {exc}")
        try:
            for proj in self.db_service.get_projects():
                self._all_items.append(
                    {
                        "type": "project",
                        "label": f"📂 {proj.name}",
                        "meta": proj.project_id,
                        "id": proj.project_id,
                    }
                )
        except Exception as exc:
            logger.debug(f"命令面板加载项目失败: {exc}")
        for item in self._all_items:
            self._add_item(item)

    def _add_item(self, item):
        w = QListWidgetItem(item["label"])
        w.setData(Qt.UserRole, item)
        self.result_list.addItem(w)

    # ===== 执行 =====

    def _run_direct(self, text: str) -> None:
        """直接运行输入的内容：路径在文件管理器打开，URL 在浏览器打开。"""
        if not text:
            return
        import webbrowser

        p = Path(text)
        if p.exists():
            self.close()
            open_in_file_manager(text)
            return
        if "://" in text or text.startswith("www."):
            url = text if "://" in text else "https://" + text
            self.close()
            webbrowser.open(url)
            return
        # 不匹配任何模式，留在面板中等待用户选择

    def _run_command(self, key: str) -> None:
        """执行快速命令（打开目录、设置等）。"""
        if key == "open_file_manager":
            open_in_file_manager(str(Path.home()))
        elif key == "open_log_dir":
            from DITWorkstation.App import config

            open_in_file_manager(str(config.log_dir))
        elif key == "open_settings":
            from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog

            parent = self.parent()
            SettingsDialog(parent).exec()
        self.close()

    def _activate(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        try:
            nav_key = None
            if data["type"] == "nav":
                nav_key = data["key"]
            elif data["type"] == "workspace":
                set_current_workspace(data["id"])
            elif data["type"] == "project":
                set_current_project(data["id"])
            elif data["type"] == "run":
                self._run_command(data["key"])
                return
            elif data["type"] == "direct":
                self.close()
                self._run_direct(data["text"])
                return
            self.close()
            if nav_key:
                from DITWorkstation.App.navigation import get_nav_index

                idx = get_nav_index(nav_key)
                if idx is not None:
                    parent = self.parent()
                    if parent and hasattr(parent, "nav_list"):
                        parent.nav_list.setCurrentRow(idx)
        except Exception as exc:
            logger.warning(f"命令面板执行失败: {exc}")
            self.close()
