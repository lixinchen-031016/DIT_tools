"""项目概览看板视图 - 聚合展示当前项目进度，提供 SOP 下一步引导"""

import zipfile
from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from DITWorkstation.App.feature_flags import is_enabled
from DITWorkstation.App.navigation import get_nav_index
from DITWorkstation.App.session_context import (
    get_current_workspace_id,
    get_data_bus,
    set_current_project,
)
from DITWorkstation.Services.archive_service import ArchiveService
from DITWorkstation.Services.project_health_service import ProjectHealthService
from DITWorkstation.Utils import (
    format_size,
    get_db_service,
    logger,
    now_local,
    pick_directory,
    pick_open_file,
    pick_save_file,
    safe_slot,
)
from DITWorkstation.ViewModels import TaskViewModel
from DITWorkstation.Views.Styles.theme import (
    COLOR,
    FONT_SIZE,
    RADIUS,
    SUBTITLE_QSS,
    TITLE_QSS,
)
from DITWorkstation.Views.Widgets import (
    CapacityTrendWidget,
    RefreshOnShowView,
    WorkspaceProjectSelector,
)
from DITWorkstation.Views.Widgets.project_template_dialog import (
    create_project_from_template,
    save_project_as_template,
)


def _nav_index(key: str) -> int | None:
    """从 navigation 单一事实源查询导航索引"""
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

    def set_value(
        self, value: str, hint: str = "", hint_color: str = COLOR.TEXT_SECONDARY
    ):
        self.value_label.setText(value)
        self.hint_label.setText(hint)
        self.hint_label.setStyleSheet(
            f"color: {hint_color}; font-size: {FONT_SIZE.XS}px;"
        )


class ProjectDashboardView(RefreshOnShowView):
    """项目概览看板视图

    展示当前项目的导入/备份/日志/报告进度，提供 SOP「下一步」按钮引导用户。
    监听全局 current_project_id，自动切换展示。
    """

    # 归档/恢复进度信号：(current, total, message)
    _archive_progress = Signal(int, int, str)

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self.archive_service = ArchiveService(db_service=self.db_service)
        self.health_service = ProjectHealthService(self.db_service)
        self.task_vm = TaskViewModel(self, task_store=self.db_service)
        self.task_vm.error.connect(self._on_task_error)
        self.task_vm.finished.connect(self._on_task_finished)
        self._dashboard_task_kind = None
        self._setup_ui()
        self._archive_progress.connect(self._on_task_progress)
        # 监听选择控件的项目切换，刷新统计卡片
        self.selector.project_changed.connect(self._on_project_changed_from_selector)
        # 监听数据变更广播（素材/日志/备份变更后刷新卡片）
        try:
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

        subtitle = QLabel(
            "聚合展示当前项目的导入/备份/日志/报告进度，快速跳转到下一步操作"
        )
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

        # 项目管理（归档 / 恢复）
        self.manage_label = QLabel("🗜 项目管理")
        self.manage_label.setStyleSheet(
            f"font-size: {FONT_SIZE.LG}px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; margin-top: 8px;"
        )
        layout.addWidget(self.manage_label)

        manage_layout = QHBoxLayout()
        manage_layout.setSpacing(8)
        self.btn_archive = QPushButton("🗜 归档当前项目…")
        self.btn_archive.setToolTip(
            "把当前项目（信息 + 日志 + 素材元数据，可选素材文件）打包为 zip"
        )
        self.btn_archive.clicked.connect(self._archive_project)

        self.btn_restore = QPushButton("↩ 恢复项目…")
        self.btn_restore.setToolTip(
            "从归档 zip 恢复项目到当前工作区（可选还原素材文件）"
        )
        self.btn_restore.clicked.connect(self._restore_project)

        self.btn_template = QPushButton("🧩 从模板新建项目…")
        self.btn_template.setToolTip(
            "基于项目模板快速创建新项目（名称/描述/工作目录自动预填）"
        )
        self.btn_template.clicked.connect(self._create_from_template)

        self.btn_save_template = QPushButton("💾 保存当前项目为模板…")
        self.btn_save_template.setToolTip(
            "把当前项目的名称/描述/工作目录保存为模板，供后续复用"
        )
        self.btn_save_template.clicked.connect(self._save_as_template)

        for btn in (
            self.btn_archive,
            self.btn_restore,
            self.btn_template,
            self.btn_save_template,
        ):
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR.BG_CARD};
                    color: {COLOR.TEXT_PRIMARY};
                    border: 1px solid {COLOR.BORDER};
                    padding: 10px 16px;
                    border-radius: {RADIUS.BUTTON}px;
                    font-size: {FONT_SIZE.BASE}px;
                }}
                QPushButton:hover {{ background-color: {COLOR.BORDER_LIGHT}; }}
                QPushButton:disabled {{ color: {COLOR.DISABLED}; }}
            """)
        manage_layout.addWidget(btn)
        manage_layout.addStretch()
        layout.addLayout(manage_layout)

        # 任务进度（归档/恢复时显示）
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 1000)
        self.task_progress.setVisible(False)
        layout.addWidget(self.task_progress)
        self.cancel_task_btn = QPushButton("取消当前任务")
        self.cancel_task_btn.clicked.connect(self._cancel_current_task)
        self.cancel_task_btn.setVisible(False)
        layout.addWidget(self.cancel_task_btn)

        # 最近操作（审计日志摘要）
        self.recent_label = QLabel("🕘 最近操作")
        self.recent_label.setStyleSheet(
            f"font-size: {FONT_SIZE.LG}px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; margin-top: 8px;"
        )
        layout.addWidget(self.recent_label)
        self.recent_ops_label = QLabel("暂无操作记录")
        self.recent_ops_label.setWordWrap(True)
        self.recent_ops_label.setStyleSheet(
            f"background-color: {COLOR.BG_CARD}; color: {COLOR.TEXT_PRIMARY}; "
            f"border: 1px solid {COLOR.BORDER}; border-radius: {RADIUS.BUTTON}px; "
            f"padding: 12px 16px; font-size: {FONT_SIZE.SM}px;"
        )
        layout.addWidget(self.recent_ops_label)

        self.health_label = QLabel("项目健康")
        self.health_label.setStyleSheet(
            f"font-size: {FONT_SIZE.LG}px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; margin-top: 8px;"
        )
        layout.addWidget(self.health_label)
        health_row = QHBoxLayout()
        self.health_summary_label = QLabel("请选择项目以查看健康状态")
        self.health_summary_label.setWordWrap(True)
        self.health_summary_label.setStyleSheet(
            f"background-color: {COLOR.BG_CARD}; color: {COLOR.TEXT_PRIMARY}; "
            f"border: 1px solid {COLOR.BORDER}; border-radius: {RADIUS.BUTTON}px; "
            f"padding: 12px 16px; font-size: {FONT_SIZE.SM}px;"
        )
        health_row.addWidget(self.health_summary_label, 1)
        self.refresh_health_btn = QPushButton("刷新健康状态")
        self.refresh_health_btn.setToolTip(
            "检查失联素材、失败任务、待重试文件和备份盘剩余容量"
        )
        self.refresh_health_btn.clicked.connect(self._refresh_health)
        health_row.addWidget(self.refresh_health_btn)
        layout.addLayout(health_row)

        self.capacity_label = QLabel("备份盘容量趋势")
        self.capacity_label.setStyleSheet(
            f"font-size: {FONT_SIZE.LG}px; font-weight: 600; color: {COLOR.TEXT_PRIMARY}; margin-top: 8px;"
        )
        layout.addWidget(self.capacity_label)
        self.capacity_trend = CapacityTrendWidget()
        layout.addWidget(self.capacity_trend)

        layout.addStretch()

        self._apply_usage_mode_visibility()

    def _apply_usage_mode_visibility(self):
        """按功能模式隐藏团队向组件。

        控件对象始终创建（保证 _refresh 等逻辑引用安全），仅设置不可见；
        数据表与服务层不受影响，切回团队模式后功能完整恢复。
        """
        if not is_enabled("shooting_log"):
            # 拍摄日志统计卡与日志快捷按钮
            self.card_logs.setVisible(False)
            self.btn_log.setVisible(False)
        if not is_enabled("report"):
            # 报告快捷按钮
            self.btn_report.setVisible(False)
        if not is_enabled("archive_restore"):
            self.manage_label.setVisible(False)
            self.btn_archive.setVisible(False)
            self.btn_restore.setVisible(False)
        if not is_enabled("project_templates"):
            self.btn_template.setVisible(False)
            self.btn_save_template.setVisible(False)
        if not is_enabled("audit_panel"):
            # 最近操作审计面板
            self.recent_label.setVisible(False)
            self.recent_ops_label.setVisible(False)
        if not is_enabled("audit_panel"):
            self.health_label.setVisible(False)
            self.health_summary_label.setVisible(False)
            self.refresh_health_btn.setVisible(False)
            self.capacity_label.setVisible(False)
            self.capacity_trend.setVisible(False)

    @safe_slot("从模板新建项目失败")
    def _create_from_template(self):
        """从模板新建项目：创建后广播并让选择控件选中新项目。"""
        project = create_project_from_template(parent=self, db_service=self.db_service)
        if project:
            self.selector.refresh()
            self._refresh()

    @safe_slot("保存项目模板失败")
    def _save_as_template(self):
        """把当前项目保存为项目模板。"""
        current = self.selector.get_current_project()
        if current is None:
            QMessageBox.information(
                self, "提示", "请先在右上角选择一个项目，再保存为模板。"
            )
            return
        template = save_project_as_template(
            parent=self, db_service=self.db_service, project=current
        )
        if template:
            QMessageBox.information(
                self,
                "保存成功",
                f"项目模板「{template.name}」已保存，可在「从模板新建项目…」中使用。",
            )

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
        if is_enabled("audit_panel"):
            self._refresh_recent_operations()
        project_id = self.selector.get_current_project_id()
        if not project_id:
            self.card_assets.set_value("—", "未选择项目")
            self.card_backups.set_value("—", "")
            self.card_logs.set_value("—", "")
            self.card_size.set_value("—", "")
            self.health_summary_label.setText("请选择项目以查看健康状态")
            self.capacity_trend.set_snapshots([])
            self.refresh_health_btn.setEnabled(False)
            if is_enabled("shooting_log"):
                self.sop_hint.setText(
                    "💡 请先选择或创建项目。可在「媒体导入」或「拍摄日志」视图新建项目，"
                    "也可在此下拉中选择已有项目。"
                )
            else:
                self.sop_hint.setText(
                    "💡 请先选择或创建项目。可在上方「+ 新建项目」创建，"
                    "或在此下拉中选择已有项目。"
                )
            self._set_guide_enabled(False)
            return

        self._set_guide_enabled(True)
        self.refresh_health_btn.setEnabled(True)

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
            unlinked = asset_count - stats.get("linked_asset_count", 0)
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
            COLOR.TEXT_SECONDARY,
        )
        self.card_size.set_value(format_size(total_size), "全部素材累计大小")

        # SOP 提示
        self.sop_hint.setText(
            self._build_sop_hint(asset_count, backed_up, log_count, backup_job_count)
        )
        self._refresh_health(capture_capacity=False)

    @safe_slot("刷新项目健康状态失败")
    def _refresh_health(self, _checked=False, capture_capacity=True):
        project_id = self.selector.get_current_project_id()
        if not project_id:
            return
        report = self.health_service.get_health_report(
            project_id, capture_capacity=capture_capacity
        )
        issues = report["issues"]
        check = report["last_integrity_check"]
        check_text = (
            "从未校验"
            if not check
            else (
                check["created_at"].strftime("%Y-%m-%d %H:%M")
                if check.get("created_at")
                else "时间未知"
            )
        )
        capacity_lines = []
        for item in report["capacities"]:
            if item["available"]:
                capacity_lines.append(
                    f"{Path(item['path']).name or item['path']}：剩余 {format_size(item['free_bytes'])}"
                )
            else:
                capacity_lines.append(
                    f"{Path(item['path']).name or item['path']}：不可达"
                )
        forecast = report.get("capacity_forecast", {})
        if forecast.get("warning"):
            days = forecast.get("days_remaining")
            warning_text = "容量不足"
            if days is not None:
                warning_text += f"，预计 {days} 天内耗尽"
            capacity_lines.append(f"⚠ {warning_text}")
        state = "正常" if report["severity"] == "healthy" else "需要关注"
        self.health_summary_label.setText(
            f"状态：{state}  |  未备份 {issues['unbacked_assets']}  |  失联路径 {issues['missing_assets']}  | "
            f"失败任务 {issues['failed_tasks']}  |  待重试 {issues['retry_files']}\n"
            f"最后校验：{check_text}"
            + ("  |  " + "；".join(capacity_lines) if capacity_lines else "")
        )
        self.capacity_trend.set_snapshots(report.get("capacity_history", []))

    def _refresh_recent_operations(self):
        """刷新「最近操作」审计日志摘要（最近 5 条）。"""
        try:
            ops = self.db_service.get_recent_operations(limit=5)
        except Exception as e:
            logger.warning(f"读取最近操作失败: {e}")
            ops = []
        if not ops:
            self.recent_ops_label.setText("暂无操作记录")
            return
        lines = []
        for op in ops:
            ts = op["created_at"].strftime("%m-%d %H:%M") if op["created_at"] else "--"
            line = f"{ts}  {op['event']}"
            if op["detail"]:
                line += f"：{op['detail']}"
            lines.append(line)
        self.recent_ops_label.setText("\n".join(lines))

    def _count_assets_with_log(self, project_id: str) -> int:
        """统计已关联 log_id 的 asset 数（用于卡片提示）"""
        try:
            return self.db_service.get_project_stats(project_id).get(
                "linked_asset_count", 0
            )
        except Exception as e:
            logger.warning(f"统计已关联日志素材失败 project_id={project_id}: {e}")
            return 0

    def _build_sop_hint(
        self, asset_count: int, backed_up: int, log_count: int, backup_job_count: int
    ) -> str:
        """根据当前进度生成 SOP 下一步建议"""
        if not is_enabled("sop_guide"):
            # 个人模式：简化引导，不出现日志/报告等团队流程
            return self._build_sop_hint_personal(
                asset_count, backed_up, backup_job_count
            )
        if asset_count == 0:
            return (
                "① 下一步：去「媒体导入」扫描存储卡并导入素材到当前项目。\n"
                "  提示：导入前可先去「数据备份」做存储卡的多目标安全备份。"
            )
        if backed_up == 0 and backup_job_count == 0:
            return (
                "② 下一步：去「数据备份」做存储卡的安全备份（会自动回写素材的备份位置）。\n"
                "  提示：备份时记得在「关联项目」下拉选中当前项目，否则无法回写 backup_locations。"
            )
        if backed_up < asset_count:
            return (
                f"⚠ 部分素材（{asset_count - backed_up} 个）尚未备份。\n"
                "  下一步：去「数据备份」继续备份未覆盖的文件。"
            )
        if log_count == 0:
            return (
                "③ 下一步：去「拍摄日志」补录场景/镜头/镜次，并关联已导入的素材。\n"
                "  提示：可在日志视图用「从代表素材填充 EXIF」自动带出相机/镜头/ISO。"
            )
        current_project_id = self.selector.get_current_project_id()
        if current_project_id is None:
            return ""
        unlinked = asset_count - self._count_assets_with_log(current_project_id)
        if unlinked > 0:
            return (
                f"⚠ 还有 {unlinked} 个素材未关联到任何拍摄日志。\n"
                "  下一步：去「拍摄日志」选中日志后点「关联素材」补全关联。"
            )
        return (
            "④ 下一步：去「报告生成」生成项目数据管理与 QC 报告。\n"
            "  完成后可去「素材检索」复核全部数据。"
        )

    def _build_sop_hint_personal(
        self, asset_count: int, backed_up: int, backup_job_count: int
    ) -> str:
        """个人模式的简化下一步建议（仅导入 → 备份 → 检索主流程）"""
        if asset_count == 0:
            return (
                "① 下一步：去「媒体导入」扫描存储卡并导入素材到当前项目。\n"
                "  提示：导入前可先去「数据备份」为存储卡做一次安全备份。"
            )
        if backed_up == 0 and backup_job_count == 0:
            return (
                "② 下一步：去「数据备份」为素材做安全备份（会自动回写备份位置）。\n"
                "  提示：备份时记得在「关联项目」下拉选中当前项目。"
            )
        if backed_up < asset_count:
            return (
                f"⚠ 部分素材（{asset_count - backed_up} 个）尚未备份。\n"
                "  下一步：去「数据备份」继续备份未覆盖的文件。"
            )
        return (
            "✅ 素材已全部完成备份。\n"
            "  可在「素材检索」定位素材，或用「RAW提取」「文件重命名」继续整理。"
        )

    def _set_guide_enabled(self, enabled: bool):
        for btn in (self.btn_import, self.btn_backup, self.btn_log, self.btn_report):
            btn.setEnabled(enabled)

    def _jump_to(self, view_index):
        """跳转到指定导航视图（通过 MainWindow 的 nav_list 切换）。

        view_index 为 None 表示目标页在当前模式下未激活（如个人模式的日志/报告），
        直接忽略，禁止把 None 传给 setCurrentRow()。
        """
        if view_index is None:
            return
        try:
            main_window = self.window()
            if hasattr(main_window, "nav_list"):
                main_window.nav_list.setCurrentRow(view_index)
            else:
                logger.warning(f"_jump_to 找不到 nav_list，view_index={view_index}")
        except Exception as e:
            logger.error(
                f"_jump_to 跳转失败 view_index={view_index}: {e}", exc_info=True
            )

    # ===== 项目管理：归档 / 恢复 =====

    @safe_slot("归档项目失败")
    def _archive_project(self):
        project_id = self.selector.get_current_project_id()
        if not project_id:
            QMessageBox.warning(self, "提示", "请先选择要归档的项目")
            return
        if self.task_vm.is_running():
            QMessageBox.warning(self, "提示", "已有任务正在运行，请稍候")
            return

        include_files = (
            QMessageBox.question(
                self,
                "归档项目",
                "是否把素材文件一并打包进归档？\n\n"
                "· 是：归档包自包含（体积大，适合长期保存/交接）\n"
                "· 否：仅归档项目信息与素材元数据（体积小）",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            == QMessageBox.Yes
        )

        default_name = f"项目归档_{now_local().strftime('%Y%m%d_%H%M%S')}.zip"
        path = pick_save_file(
            self, "保存项目归档", default_name, "ZIP 归档 (*.zip);;所有文件 (*)"
        )
        if not path:
            return

        self._set_task_running(
            True, f"归档项目（{'含' if include_files else '不含'}素材文件）…"
        )
        self._dashboard_task_kind = "archive"
        self.task_vm.start(
            self.archive_service.archive_project,
            project_id,
            path,
            include_files=include_files,
            progress_callback=lambda cur, tot, msg: self._archive_progress.emit(
                cur, tot, msg
            ),
            inject_cancel_check=True,
            task_name="归档项目",
            project_id=project_id,
            recovery_info={"archive_path": path, "include_files": include_files},
        )

    @Slot(object)
    def _on_archive_finished(self, result):
        self._set_task_running(False)
        QMessageBox.information(
            self,
            "归档完成",
            f"项目已归档到：\n{result}\n\n可随时通过「恢复项目」还原。",
        )

    @safe_slot("恢复项目失败")
    def _restore_project(self):
        if self.task_vm.is_running():
            QMessageBox.warning(self, "提示", "已有任务正在运行，请稍候")
            return

        path = pick_open_file(
            self, "选择项目归档包", "", "ZIP 归档 (*.zip);;所有文件 (*)"
        )
        if not path:
            return

        has_files = False
        try:
            with zipfile.ZipFile(path) as zf:
                has_files = any(
                    n.startswith("files/") and not n.endswith("/")
                    for n in zf.namelist()
                )
        except zipfile.BadZipFile:
            QMessageBox.warning(self, "提示", "所选文件不是有效的 zip 归档包")
            return

        workspace_id = get_current_workspace_id()
        restore_files = False
        files_dest = None
        if has_files:
            reply = QMessageBox.question(
                self,
                "还原素材文件",
                "归档包内包含素材文件，是否一并还原到项目工作目录？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                files_dest = pick_directory(
                    self, "选择素材文件还原目录", category="archive_restore"
                )
                if files_dest:
                    restore_files = True

        self._set_task_running(True, "恢复项目…")
        self._dashboard_task_kind = "restore"
        self.task_vm.start(
            self.archive_service.restore_project,
            path,
            workspace_id=workspace_id,
            restore_files=restore_files,
            files_dest=files_dest,
            verify=restore_files,
            progress_callback=lambda cur, tot, msg: self._archive_progress.emit(
                cur, tot, msg
            ),
            inject_cancel_check=True,
            task_name="恢复项目",
            recovery_info={"archive_path": path, "restore_files": restore_files},
        )

    @Slot(object)
    def _on_task_finished(self, result):
        kind, self._dashboard_task_kind = self._dashboard_task_kind, None
        if kind == "archive":
            self._on_archive_finished(result)
        elif kind == "restore":
            self._on_restore_finished(result)

    @Slot(object)
    def _on_restore_finished(self, result):
        self._set_task_running(False)
        project = result["project"]
        lines = [
            f"已恢复项目：{project.name}",
            f"素材记录：{result['restored_assets']} 条",
            f"拍摄日志：{result['restored_logs']} 条",
        ]
        if result["restored_files"]:
            lines.append(f"还原素材文件：{result['restored_files']} 个")
        if result["missing_files"]:
            lines.append(f"⚠ 归档中缺失文件：{result['missing_files']} 个")
        if result["mismatches"]:
            lines.append(f"❌ 校验和不一致：{len(result['mismatches'])} 个")
        QMessageBox.information(self, "恢复完成", "\n".join(lines))

        # 切换到恢复的项目并刷新
        set_current_project(project.project_id)
        self.selector.refresh()
        self._refresh()
        try:
            get_data_bus().emit_data_changed("projects_changed")
        except Exception as e:
            logger.warning(f"恢复后广播 projects_changed 失败: {e}")

    def _set_task_running(self, running: bool, message: str = ""):
        self.btn_archive.setEnabled(not running)
        self.btn_restore.setEnabled(not running)
        self.task_progress.setVisible(running)
        self.task_progress.setValue(0)
        self.cancel_task_btn.setVisible(running)
        self.cancel_task_btn.setEnabled(running)

    def _cancel_current_task(self):
        if self.task_vm.is_running():
            self.task_vm.cancel()
            self.cancel_task_btn.setEnabled(False)

    @Slot(int, int, str)
    def _on_task_progress(self, current: int, total: int, message: str):
        if total > 0:
            self.task_progress.setValue(int(current / total * 1000))
        self.sop_hint.setText(message)

    @Slot(str)
    def _on_task_error(self, error: str):
        self._dashboard_task_kind = None
        self._set_task_running(False)
        self.sop_hint.setText("❌ 任务失败")
        QMessageBox.critical(self, "任务失败", error)
