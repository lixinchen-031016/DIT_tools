"""项目概览看板视图 - 聚合展示当前项目进度，提供 SOP 下一步引导"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QGridLayout, QFrame
)
from PySide6.QtCore import Slot

from DITWorkstation.Utils import get_db_service, format_size


# 主窗口导航栏索引（与 main_window.py 的 nav_items 顺序保持一致）
# 修改导航栏顺序时只需同步此处常量，避免按钮跳转到错误视图
_NAV_INDEX = {
    "dashboard": 0,   # 🏠 项目概览
    "import": 1,      # 📁 媒体导入
    "backup": 2,      # 📦 数据备份
    "raw": 3,         # 🎞 RAW提取
    "rename": 4,      # ✏️ 文件重命名
    "log": 5,         # 📋 拍摄日志
    "search": 6,      # 🔍 素材检索
    "asset_info": 7,  # ℹ️ 素材信息
    "report": 8,      # 📊 报告生成
}


class _StatCard(QFrame):
    """单个统计卡片"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setStyleSheet("""
            #statCard {
                background-color: #f5f5f7;
                border-radius: 12px;
                border: 1px solid #e5e5e7;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #86868b; font-size: 12px; font-weight: 500;")
        layout.addWidget(self.title_label)

        self.value_label = QLabel("—")
        self.value_label.setStyleSheet("color: #1d1d1f; font-size: 28px; font-weight: 700;")
        layout.addWidget(self.value_label)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: #86868b; font-size: 11px;")
        layout.addWidget(self.hint_label)

    def set_value(self, value: str, hint: str = "", hint_color: str = "#86868b"):
        self.value_label.setText(value)
        self.hint_label.setText(hint)
        self.hint_label.setStyleSheet(f"color: {hint_color}; font-size: 11px;")


class ProjectDashboardView(QWidget):
    """项目概览看板视图

    展示当前项目的导入/备份/日志/报告进度，提供 SOP「下一步」按钮引导用户。
    监听全局 current_project_id，自动切换展示。
    """

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self._setup_ui()
        # 监听全局工作区与项目切换
        try:
            from DITWorkstation.Views.main_window import get_data_bus
            get_data_bus().workspace_focus_changed.connect(self._on_global_workspace_changed)
            get_data_bus().project_focus_changed.connect(self._on_global_project_changed)
            get_data_bus().data_changed.connect(self._on_data_changed)
        except Exception:
            pass

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题 + 工作区/项目选择
        header = QHBoxLayout()
        title = QLabel("项目概览")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        header.addWidget(title)

        header.addStretch()

        header.addWidget(QLabel("工作区:"))
        self.workspace_combo = QComboBox()
        self.workspace_combo.setMinimumWidth(180)
        self.workspace_combo.addItem("（全部工作区）", None)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        header.addWidget(self.workspace_combo)

        header.addWidget(QLabel("项目:"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(260)
        self.project_combo.addItem("（未选择项目）", None)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        header.addWidget(self.project_combo)

        layout.addLayout(header)

        subtitle = QLabel("聚合展示当前项目的导入/备份/日志/报告进度，快速跳转到下一步操作")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
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
            "font-size: 15px; font-weight: 600; color: #1d1d1f; margin-top: 8px;"
        )
        layout.addWidget(guide_group_label)

        guide_layout = QHBoxLayout()
        guide_layout.setSpacing(8)

        # 4 个快捷跳转按钮（索引来自 _NAV_INDEX，与 main_window 导航栏顺序对齐）
        self.btn_import = QPushButton("① 媒体导入")
        self.btn_import.setToolTip("去导入素材到当前项目")
        self.btn_import.clicked.connect(lambda: self._jump_to(_NAV_INDEX["import"]))

        self.btn_backup = QPushButton("② 数据备份")
        self.btn_backup.setToolTip("去把存储卡安全备份")
        self.btn_backup.clicked.connect(lambda: self._jump_to(_NAV_INDEX["backup"]))

        self.btn_log = QPushButton("③ 拍摄日志")
        self.btn_log.setToolTip("去补录场景/镜头/镜次并关联素材")
        self.btn_log.clicked.connect(lambda: self._jump_to(_NAV_INDEX["log"]))

        self.btn_report = QPushButton("④ 生成报告")
        self.btn_report.setToolTip("去生成项目数据管理与 QC 报告")
        self.btn_report.clicked.connect(lambda: self._jump_to(_NAV_INDEX["report"]))

        for btn in (self.btn_import, self.btn_backup, self.btn_log, self.btn_report):
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0a84ff;
                    color: white;
                    padding: 10px 16px;
                    border-radius: 8px;
                    font-size: 13px;
                    font-weight: 600;
                }
                QPushButton:hover { background-color: #0070e0; }
                QPushButton:disabled { background-color: #cccccc; }
            """)
            guide_layout.addWidget(btn)
        guide_layout.addStretch()
        layout.addLayout(guide_layout)

        # SOP 提示
        self.sop_hint = QLabel("")
        self.sop_hint.setStyleSheet(
            "background-color: #e8f4ff; color: #004080; padding: 12px 16px; "
            "border-radius: 8px; font-size: 13px;"
        )
        self.sop_hint.setWordWrap(True)
        layout.addWidget(self.sop_hint)

        layout.addStretch()

    def showEvent(self, event):
        super().showEvent(event)
        self._load_workspaces()
        self._load_projects()
        self._refresh()

    def _load_workspaces(self):
        """加载工作区下拉，优先选中全局 current_workspace_id"""
        from DITWorkstation.Views.main_window import get_current_workspace_id
        prev_id = get_current_workspace_id()
        self.workspace_combo.blockSignals(True)
        self.workspace_combo.clear()
        self.workspace_combo.addItem("（全部工作区）", None)
        try:
            workspaces = self.db_service.get_workspaces()
        except Exception:
            workspaces = []
        target_index = 0
        for i, ws in enumerate(workspaces, start=1):
            label = ws.name
            if ws.path:
                label += f"  [{ws.path}]"
            self.workspace_combo.addItem(label, ws.workspace_id)
            if prev_id and ws.workspace_id == prev_id:
                target_index = i
        self.workspace_combo.setCurrentIndex(target_index)
        self.workspace_combo.blockSignals(False)

    def _load_projects(self):
        """加载项目下拉，按当前选中工作区过滤；优先选中全局 current_project_id"""
        from DITWorkstation.Views.main_window import get_current_project_id
        prev_id = get_current_project_id()
        ws_id = self.workspace_combo.currentData()

        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("（未选择项目）", None)
        try:
            # 按工作区过滤；ws_id 为 None 时返回全部项目（与"全部工作区"语义一致）
            projects = self.db_service.get_projects(workspace_id=ws_id)
        except Exception:
            projects = []
        target_index = 0
        for i, p in enumerate(projects, start=1):
            self.project_combo.addItem(f"{p.name} ({p.project_id})", p.project_id)
            if prev_id and p.project_id == prev_id:
                target_index = i
        self.project_combo.setCurrentIndex(target_index)
        self.project_combo.blockSignals(False)
        # 手动触发刷新
        self._refresh()

    @Slot(int)
    def _on_workspace_changed(self, _index: int):
        """本视图工作区下拉切换 -> 广播到全局，并重载项目下拉"""
        from DITWorkstation.Views.main_window import set_current_workspace
        set_current_workspace(self.workspace_combo.currentData())
        # 工作区切换会触发 _on_global_workspace_changed -> _load_projects，避免重复加载
        # 但若全局未变化（同 ID），仍需手动重载
        self._load_projects()

    @Slot(int)
    def _on_project_changed(self, _index: int):
        """本视图项目下拉切换 -> 广播到全局"""
        from DITWorkstation.Views.main_window import set_current_project
        set_current_project(self.project_combo.currentData())
        self._refresh()

    @Slot(object)
    def _on_global_workspace_changed(self, workspace_id):
        """全局工作区切换 -> 同步本视图工作区下拉（项目下拉由 _load_projects 重新加载）"""
        self.workspace_combo.blockSignals(True)
        if workspace_id is None:
            self.workspace_combo.setCurrentIndex(0)
        else:
            for i in range(self.workspace_combo.count()):
                if self.workspace_combo.itemData(i) == workspace_id:
                    self.workspace_combo.setCurrentIndex(i)
                    break
        self.workspace_combo.blockSignals(False)
        self._load_projects()

    @Slot(object)
    def _on_global_project_changed(self, project_id):
        """全局项目切换 -> 同步本视图项目下拉"""
        self.project_combo.blockSignals(True)
        if project_id is None:
            self.project_combo.setCurrentIndex(0)
        else:
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == project_id:
                    self.project_combo.setCurrentIndex(i)
                    break
        self.project_combo.blockSignals(False)
        self._refresh()

    @Slot(str)
    def _on_data_changed(self, event: str):
        """收到数据变更广播时刷新卡片"""
        # 仅当前视图可见时刷新
        if self.isVisible():
            # 工作区/项目列表也可能变了（如新建项目后），重载下拉
            if event in ("all", "workspaces_changed", "projects_changed"):
                self._load_workspaces()
                self._load_projects()
            else:
                self._refresh()

    def _refresh(self):
        """根据当前项目刷新统计卡片与 SOP 提示"""
        project_id = self.project_combo.currentData()
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
            self.card_assets.set_value("0", "尚未导入", "#ff9500")
        else:
            unlinked = asset_count - self._count_assets_with_log(project_id)
            hint = f"其中 {unlinked} 个未关联日志" if unlinked > 0 else "全部已关联日志"
            color = "#ff3b30" if unlinked > 0 else "#34c759"
            self.card_assets.set_value(str(asset_count), hint, color)

        if asset_count == 0:
            self.card_backups.set_value("—", "无素材", "#86868b")
        elif backed_up == 0:
            self.card_backups.set_value("0", "尚未备份", "#ff3b30")
        elif backed_up < asset_count:
            self.card_backups.set_value(
                f"{backed_up}/{asset_count}", "部分已备份", "#ff9500"
            )
        else:
            self.card_backups.set_value(
                f"{backed_up}/{asset_count}", "全部已备份", "#34c759"
            )

        self.card_logs.set_value(
            str(log_count),
            f"共 {backup_job_count} 次备份作业" if backup_job_count else "无备份记录",
            "#86868b"
        )
        self.card_size.set_value(format_size(total_size), "全部素材累计大小")

        # SOP 提示
        self.sop_hint.setText(self._build_sop_hint(asset_count, backed_up, log_count, backup_job_count))

    def _count_assets_with_log(self, project_id: str) -> int:
        """统计已关联 log_id 的 asset 数（用于卡片提示）"""
        try:
            assets = self.db_service.get_media_assets(project_id)
            return sum(1 for a in assets if a.log_id)
        except Exception:
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
        unlinked = asset_count - self._count_assets_with_log(self.project_combo.currentData())
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
