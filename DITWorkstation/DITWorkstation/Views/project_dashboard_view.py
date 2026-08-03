"""项目概览看板视图 - 聚合展示当前项目进度，提供 SOP 下一步引导"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGridLayout, QFrame, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Slot
from PySide6.QtGui import QColor

from DITWorkstation.Utils import get_db_service, format_size, logger
from DITWorkstation.Views.Widgets import WorkspaceProjectSelector, RefreshOnShowView
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS, TITLE_QSS, SUBTITLE_QSS


def _nav_index(key: str) -> int:
    """从 navigation 单一事实源查询导航索引"""
    from DITWorkstation.App.navigation import get_nav_index
    return get_nav_index(key)


class _StatCard(QFrame):
    """单个统计卡片 — 带柔和阴影的卡片式展示"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setStyleSheet(f"""
            #statCard {{
                background-color: {COLOR.BG_CARD};
                border-radius: {RADIUS.CARD}px;
                border: 1px solid {COLOR.BORDER_LIGHT};
            }}
        """)
        # 柔和投影：让卡片有悬浮感，但不过于夸张
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 25))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px; font-weight: 500;"
        )
        layout.addWidget(self.title_label)

        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(
            f"color: {COLOR.TEXT_PRIMARY}; font-size: {FONT_SIZE.XXL}px; font-weight: 700;"
        )
        layout.addWidget(self.value_label)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.XS}px;"
        )
        layout.addWidget(self.hint_label)

    def set_value(self, value: str, hint: str = "", hint_color: str = COLOR.TEXT_SECONDARY):
        self.value_label.setText(value)
        self.hint_label.setText(hint)
        self.hint_label.setStyleSheet(f"color: {hint_color}; font-size: {FONT_SIZE.XS}px;")


class ProjectDashboardView(RefreshOnShowView):
    """项目概览看板视图

    展示当前项目的导入/备份/日志/报告进度，提供 SOP「下一步」按钮引导用户。
    监听全局 current_project_id，自动切换展示。
    """

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self._setup_ui()
        # 监听选择控件的项目切换，刷新统计卡片
        self.selector.project_changed.connect(self._on_project_changed_from_selector)
        # 监听数据变更广播（素材/日志/备份变更后刷新卡片）
        try:
            from DITWorkstation.App.session_context import get_data_bus
            get_data_bus().data_changed.connect(self._on_data_changed)
        except Exception as e:
            logger.warning(f"项目概览连接 data_bus 失败: {e}")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题 + 工作区/项目选择
        header = QHBoxLayout()
        title = QLabel("项目概览")
        title.setStyleSheet(TITLE_QSS)
        header.addWidget(title)

        header.addStretch()

        # 工作区 + 项目两级选择控件（combo 模式 + 编辑按钮）
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_edit_workspace=True,
            show_new_project=True,
            db_service=self.db_service,
        )
        header.addWidget(self.selector)

        layout.addLayout(header)

        subtitle = QLabel("聚合展示当前项目的导入/备份/日志/报告进度，快速跳转到下一步操作")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # 统计卡片网格（2x2）
        cards_layout = QGridLayout()
        cards_layout.setSpacing(12)

        self.card_assets = _StatCard("已导入素材")
        self.card_backups = _StatCard("已备份素材")
        self.card_logs = _StatCard("拍摄日志数")
        self.card_size = _StatCard("素材总大小")

        cards_layout.addWidget(self.card_assets, 0, 0)
        cards_layout.addWidget(self.card_backups, 0, 1)
        cards_layout.addWidget(self.card_logs, 1, 0)
        cards_layout.addWidget(self.card_size, 1, 1)
        layout.addLayout(cards_layout)

        # SOP 下一步引导
        guide_group_label = QLabel("SOP 操作链 — 下一步")
        guide_group_label.setStyleSheet(
            f"font-size: {FONT_SIZE.LG}px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; margin-top: 8px;"
        )
        layout.addWidget(guide_group_label)

        guide_layout = QHBoxLayout()
        guide_layout.setSpacing(8)

        # 4 个快捷跳转按钮（索引通过 _nav_index 从 main_window 单一事实源查询）
        self.btn_import = QPushButton("① 媒体导入")
        self.btn_import.setToolTip("去导入素材到当前项目")
        self.btn_import.clicked.connect(lambda: self._jump_to(_nav_index("import")))

        self.btn_backup = QPushButton("② 数据备份")
        self.btn_backup.setToolTip("去把存储卡安全备份")
        self.btn_backup.clicked.connect(lambda: self._jump_to(_nav_index("backup")))

        self.btn_log = QPushButton("③ 拍摄日志")
        self.btn_log.setToolTip("去补录场景/镜头/镜次并关联素材")
        self.btn_log.clicked.connect(lambda: self._jump_to(_nav_index("log")))

        self.btn_report = QPushButton("④ 生成报告")
        self.btn_report.setToolTip("去生成项目数据管理与 QC 报告")
        self.btn_report.clicked.connect(lambda: self._jump_to(_nav_index("report")))

        for btn in (self.btn_import, self.btn_backup, self.btn_log, self.btn_report):
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR.PRIMARY};
                    color: white;
                    padding: 10px 16px;
                    border-radius: {RADIUS.BUTTON}px;
                    font-size: {FONT_SIZE.BASE}px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: {COLOR.PRIMARY_HOVER}; }}
                QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}
            """)
            guide_layout.addWidget(btn)
        guide_layout.addStretch()
        layout.addLayout(guide_layout)

        # SOP 提示
        self.sop_hint = QLabel("")
        self.sop_hint.setStyleSheet(
            f"background-color: {COLOR.BANNER_INFO_BG}; color: {COLOR.BANNER_INFO_FG}; "
            f"padding: 12px 16px; border-radius: {RADIUS.BUTTON}px; font-size: {FONT_SIZE.BASE}px;"
        )
        self.sop_hint.setWordWrap(True)
        layout.addWidget(self.sop_hint)

        layout.addStretch()

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self.selector.refresh()
        self._refresh()

    @Slot(object)
    def _on_project_changed_from_selector(self, _project_id):
        """选择控件的项目切换 → 刷新统计卡片（全局广播由 selector 内部处理）"""
        self._refresh()

    @Slot(str)
    def _on_data_changed(self, event: str):
        """收到数据变更广播时刷新卡片

        工作区/项目列表变更由 selector 自行同步；本视图只需刷新统计卡片。
        """
        if self.isVisible():
            if event in ("all", "workspaces_changed", "projects_changed"):
                # 工作区/项目可能新增，让 selector 重新加载下拉
                self.selector.refresh()
            self._refresh()

    def _refresh(self):
        """根据当前项目刷新统计卡片与 SOP 提示"""
        project_id = self.selector.get_current_project_id()
        if not project_id:
            self.card_assets.set_value("—", "未选择项目")
            self.card_backups.set_value("—", "")
            self.card_logs.set_value("—", "")
            self.card_size.set_value("—", "")
            self.sop_hint.setText(
                "💡 请先选择或创建项目。可在「媒体导入」或「拍摄日志」视图新建项目，"
                "也可在此下拉中选择已有项目。"
            )
            self._set_guide_enabled(False)
            return

        self._set_guide_enabled(True)

        try:
            stats = self.db_service.get_project_stats(project_id)
        except Exception as e:
            self.sop_hint.setText(f"❌ 读取项目统计失败: {e}")
            return

        asset_count = stats.get("asset_count", 0)
        backed_up = stats.get("backed_up_count", 0)
        log_count = stats.get("log_count", 0)
        total_size = stats.get("total_size", 0)
        backup_job_count = stats.get("backup_job_count", 0)

        # 卡片
        if asset_count == 0:
            self.card_assets.set_value("0", "尚未导入", COLOR.WARNING)
        else:
            unlinked = asset_count - self._count_assets_with_log(project_id)
            hint = f"其中 {unlinked} 个未关联日志" if unlinked > 0 else "全部已关联日志"
            color = COLOR.DANGER if unlinked > 0 else COLOR.SUCCESS
            self.card_assets.set_value(str(asset_count), hint, color)

        if asset_count == 0:
            self.card_backups.set_value("—", "无素材", COLOR.TEXT_SECONDARY)
        elif backed_up == 0:
            self.card_backups.set_value("0", "尚未备份", COLOR.DANGER)
        elif backed_up < asset_count:
            self.card_backups.set_value(
                f"{backed_up}/{asset_count}", "部分已备份", COLOR.WARNING
            )
        else:
            self.card_backups.set_value(
                f"{backed_up}/{asset_count}", "全部已备份", COLOR.SUCCESS
            )

        self.card_logs.set_value(
            str(log_count),
            f"共 {backup_job_count} 次备份作业" if backup_job_count else "无备份记录",
            COLOR.TEXT_SECONDARY
        )
        self.card_size.set_value(format_size(total_size), "全部素材累计大小")

        # SOP 提示
        self.sop_hint.setText(self._build_sop_hint(asset_count, backed_up, log_count, backup_job_count))

    def _count_assets_with_log(self, project_id: str) -> int:
        """统计已关联 log_id 的 asset 数（用于卡片提示）"""
        try:
            assets = self.db_service.get_media_assets(project_id)
            return sum(1 for a in assets if a.log_id)
        except Exception as e:
            logger.warning(f"统计已关联日志素材失败 project_id={project_id}: {e}")
            return 0

    def _build_sop_hint(self, asset_count: int, backed_up: int,
                        log_count: int, backup_job_count: int) -> str:
        """根据当前进度生成 SOP 下一步建议"""
        if asset_count == 0:
            return ("① 下一步：去「媒体导入」扫描存储卡并导入素材到当前项目。\n"
                    "  提示：导入前可先去「数据备份」做存储卡的多目标安全备份。")
        if backed_up == 0 and backup_job_count == 0:
            return ("② 下一步：去「数据备份」做存储卡的安全备份（会自动回写素材的备份位置）。\n"
                    "  提示：备份时记得在「关联项目」下拉选中当前项目，否则无法回写 backup_locations。")
        if backed_up < asset_count:
            return (f"⚠ 部分素材（{asset_count - backed_up} 个）尚未备份。\n"
                    "  下一步：去「数据备份」继续备份未覆盖的文件。")
        if log_count == 0:
            return ("③ 下一步：去「拍摄日志」补录场景/镜头/镜次，并关联已导入的素材。\n"
                    "  提示：可在日志视图用「从代表素材填充 EXIF」自动带出相机/镜头/ISO。")
        unlinked = asset_count - self._count_assets_with_log(self.selector.get_current_project_id())
        if unlinked > 0:
            return (f"⚠ 还有 {unlinked} 个素材未关联到任何拍摄日志。\n"
                    "  下一步：去「拍摄日志」选中日志后点「关联素材」补全关联。")
        return ("④ 下一步：去「报告生成」生成项目数据管理与 QC 报告。\n"
                "  完成后可去「素材检索」复核全部数据。")

    def _set_guide_enabled(self, enabled: bool):
        for btn in (self.btn_import, self.btn_backup, self.btn_log, self.btn_report):
            btn.setEnabled(enabled)

    def _jump_to(self, view_index: int):
        """跳转到指定导航视图（通过 MainWindow 的 nav_list 切换）"""
        try:
            main_window = self.window()
            if hasattr(main_window, "nav_list"):
                main_window.nav_list.setCurrentRow(view_index)
            else:
                from DITWorkstation.Utils import logger
                logger.warning(f"_jump_to 找不到 nav_list，view_index={view_index}")
        except Exception as e:
            from DITWorkstation.Utils import logger
            logger.error(f"_jump_to 跳转失败 view_index={view_index}: {e}", exc_info=True)
