"""媒体导入页面"""
import hashlib
from pathlib import Path
from typing import Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QGroupBox,
    QTextEdit, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QComboBox, QDateTimeEdit, QMenu, QApplication, QFileDialog
)
from PySide6.QtCore import Qt, Slot, QDateTime, Signal
from PySide6.QtGui import QPixmap

from DITWorkstation.App.navigation import get_nav_index
from DITWorkstation.App.feature_flags import is_enabled
from DITWorkstation.App.session_context import get_data_bus
from DITWorkstation.Models import Project
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Services.thumbnail_service import ThumbnailService, SIZE_LARGE
from DITWorkstation.Utils import (
    format_size, WorkerThread, get_db_service, pick_directory,
    find_overwrite_conflicts, open_in_file_manager, is_writable_directory,
    logger,
)
from DITWorkstation.Views.Widgets import WorkspaceProjectSelector, RefreshOnShowView
from DITWorkstation.Views.Widgets.empty_state import attach_empty_state, sync_empty_state
from DITWorkstation.Views.Widgets.error_dialog import show_error
from DITWorkstation.Views.Widgets.status_panel import StatusPanel
from DITWorkstation.Views.Widgets.table_factory import make_table
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS, TITLE_QSS, SUBTITLE_QSS, MONO_FONT_QSS


class MediaImportView(RefreshOnShowView):
    """媒体导入视图"""

    # 缩略图预览完成信号（由 ThumbnailService.thumbnail_ready 桥接）
    _preview_ready = Signal(str, QPixmap)

    def __init__(self):
        super().__init__()
        # 注入共享 db_service 单例，避免与其它视图各自新建 DatabaseService
        self.db_service = get_db_service()
        self.import_service = MediaImportService(db_service=self.db_service)
        self.current_project: Optional[Project] = None
        self.worker = None
        self._cancel_requested = False
        # 扫描得到的全部文件（含 stat 信息），时间筛选时在此列表上过滤，
        # 避免每次调整时间范围都重新扫描存储卡。
        self._all_scanned_files: list[Path] = []
        self.thumbnail_service = ThumbnailService()
        self._preview_key = ""  # 当前预览的缓存键
        self._setup_ui()
        self._preview_ready.connect(self._on_preview_ready)
        self.thumbnail_service.thumbnail_ready.connect(self._preview_ready)
        # 监听选择控件的 项目切换 信号，做本视图业务联动
        # （工作区/项目下拉与全局信号同步已由 WorkspaceProjectSelector 内部处理）
        self.selector.project_changed.connect(self._on_project_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("媒体导入")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel("将图片、视频、RAW文件导入项目，原文件位置保持不动")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # 左侧：工作区/项目选择（固定宽度，不参与拖动）
        left_panel = QWidget()
        left_panel.setFixedWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # 工作区 + 项目两级选择控件（封装下拉/列表/新建对话框/全局信号同步）
        self.selector = WorkspaceProjectSelector(
            project_widget="list",
            show_edit_workspace=False,
            show_new_project=True,
            db_service=self.db_service,
        )
        left_layout.addWidget(self.selector)
        left_layout.addStretch()

        # 右侧内容：各区块垂直堆叠、按内容自适应高度，
        # 空间不足时由整个视图的外层滚动条滚动
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        source_group = QGroupBox("导入源")
        source_layout = QVBoxLayout(source_group)

        source_row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择要导入的文件夹...")
        self.source_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._select_folder)
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(browse_btn)
        source_layout.addLayout(source_row)

        scan_row = QHBoxLayout()
        self.recursive_check = QCheckBox("递归扫描子文件夹")
        self.recursive_check.setChecked(True)
        self.recursive_check.setToolTip("勾选后会扫描子文件夹中的所有媒体文件。存储卡通常不需要递归。")
        scan_row.addWidget(self.recursive_check)

        self.include_images = QCheckBox("图片")
        self.include_images.setChecked(True)
        scan_row.addWidget(self.include_images)

        self.include_videos = QCheckBox("视频")
        self.include_videos.setChecked(True)
        scan_row.addWidget(self.include_videos)

        self.include_raw = QCheckBox("RAW")
        self.include_raw.setChecked(True)
        scan_row.addWidget(self.include_raw)

        scan_row.addStretch()
        scan_btn = QPushButton("🔍 扫描")
        scan_btn.setToolTip("扫描源目录中的媒体文件")
        scan_btn.clicked.connect(self._scan_folder)
        scan_row.addWidget(scan_btn)
        source_layout.addLayout(scan_row)

        # 按时间筛选：影视拍摄常跨多天，用户可能只想导入某天的素材。
        # 用文件修改时间筛选（存储卡上修改时间通常等于拍摄时间，无需读 EXIF，速度快）。
        time_row = QHBoxLayout()
        self.time_filter_check = QCheckBox("按时间筛选")
        self.time_filter_check.setToolTip(
            "勾选后只导入指定时间范围内的文件。\n"
            "时间依据为文件修改时间（存储卡上通常等于拍摄时间）。"
        )
        self.time_filter_check.toggled.connect(self._on_time_filter_toggled)
        time_row.addWidget(self.time_filter_check)

        # 默认时间范围：今天 00:00 ~ 23:59
        now = QDateTime.currentDateTime()
        self.start_time_edit = QDateTimeEdit(now.addDays(-1))
        self.start_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.start_time_edit.setCalendarPopup(True)
        self.start_time_edit.setEnabled(False)
        self.start_time_edit.dateTimeChanged.connect(self._apply_time_filter)
        time_row.addWidget(self.start_time_edit)

        time_row.addWidget(QLabel("至"))

        self.end_time_edit = QDateTimeEdit(now)
        self.end_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.end_time_edit.setCalendarPopup(True)
        self.end_time_edit.setEnabled(False)
        self.end_time_edit.dateTimeChanged.connect(self._apply_time_filter)
        time_row.addWidget(self.end_time_edit)

        # 快捷按钮：今天 / 最近7天 / 清除范围
        self.today_btn = QPushButton("今天")
        self.today_btn.setEnabled(False)
        self.today_btn.clicked.connect(self._set_time_range_today)
        time_row.addWidget(self.today_btn)

        self.week_btn = QPushButton("最近7天")
        self.week_btn.setEnabled(False)
        self.week_btn.clicked.connect(self._set_time_range_week)
        time_row.addWidget(self.week_btn)

        source_layout.addLayout(time_row)

        right_layout.addWidget(source_group)

        files_group = QGroupBox("待导入文件")
        files_group.setMinimumHeight(200)
        files_layout = QVBoxLayout(files_group)

        # 选择工具行：全选 / 全不选 / 反选 + 已选计数
        select_row = QHBoxLayout()
        select_row.setSpacing(8)
        self.select_all_btn = QPushButton("☑ 全选")
        self.select_all_btn.setToolTip("勾选全部文件")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_btn = QPushButton("☐ 全不选")
        self.select_none_btn.setToolTip("取消勾选全部文件")
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        self.select_invert_btn = QPushButton("⇄ 反选")
        self.select_invert_btn.setToolTip("反转勾选状态")
        self.select_invert_btn.clicked.connect(self._invert_selection)
        select_row.addWidget(self.select_all_btn)
        select_row.addWidget(self.select_none_btn)
        select_row.addWidget(self.select_invert_btn)
        select_row.addStretch()
        self.selected_label = QLabel("")
        self.selected_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        select_row.addWidget(self.selected_label)
        files_layout.addLayout(select_row)

        self.files_table = make_table(
            ["导入", "文件名", "类型", "大小", "修改时间", "路径"],
            sortable=True,
            resize_to_contents_cols=[0, 1, 2, 3],
        )
        # 双击打开所在目录
        self.files_table.doubleClicked.connect(self._on_file_double_clicked)
        # 行选中变化 → 刷新缩略图预览
        self.files_table.itemSelectionChanged.connect(self._on_selection_changed)
        # 勾选状态变化 → 更新已选计数
        self.files_table.itemChanged.connect(self._on_item_changed)
        # 右键菜单：打开目录 / 复制路径
        self.files_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files_table.customContextMenuRequested.connect(self._on_files_context_menu)
        attach_empty_state(self.files_table, "📁", "暂无待导入文件", "点击上方「浏览…」选择存储卡目录")

        # 缩略图预览面板：选中行时显示对应素材缩略图
        preview_panel = QWidget()
        preview_panel.setFixedWidth(260)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(8, 0, 0, 0)
        preview_layout.setSpacing(8)
        preview_title = QLabel("缩略图预览")
        preview_title.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        preview_layout.addWidget(preview_title)

        self.preview_label = QLabel("选中文件后在此预览")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(160)
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(
            f"background-color: {COLOR.BG_APP}; color: {COLOR.TEXT_SECONDARY}; "
            f"border: 1px dashed {COLOR.BORDER}; border-radius: {RADIUS.INPUT}px;"
        )
        preview_layout.addWidget(self.preview_label, 1)

        self.preview_info = QLabel("")
        self.preview_info.setWordWrap(True)
        self.preview_info.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        preview_layout.addWidget(self.preview_info)

        # 表格 + 缩略图预览 左右排列（预览面板固定宽度）
        files_row = QHBoxLayout()
        files_row.setSpacing(8)
        files_row.addWidget(self.files_table, 1)
        files_row.addWidget(preview_panel)
        files_layout.addLayout(files_row, 1)

        self.files_label = QLabel("")
        self.files_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        files_layout.addWidget(self.files_label)

        right_layout.addWidget(files_group, 1)

        # 下段容器：选项 + 按钮 + 执行状态
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        options_group = QGroupBox("导入选项")
        options_layout = QVBoxLayout(options_group)

        opt_row1 = QHBoxLayout()

        self.checksum_check = QCheckBox("计算校验和")
        self.checksum_check.setChecked(True)
        self.checksum_check.setToolTip("计算文件校验和（XXHash64），用于后续验证文件完整性和去重。建议勾选。")
        opt_row1.addWidget(self.checksum_check)

        self.copy_mode_check = QCheckBox("复制到工作区")
        self.copy_mode_check.setChecked(False)
        self.copy_mode_check.setToolTip("勾选后将文件复制到 当前工作区目录/项目名/ 下再导入，原文件保持不动")
        opt_row1.addWidget(self.copy_mode_check)

        # 复制目标路径提示（只读，自动基于当前工作区.path/<项目名> 生成）
        self.copy_dest_label = QLabel("")
        self.copy_dest_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        self.copy_mode_check.toggled.connect(self._on_copy_check_toggled)
        opt_row1.addWidget(self.copy_dest_label, 1)

        # 工作区未设置本地目录时出现的「选择目录…」按钮：
        # 个人模式常见（default 工作区初始 path 为空），此时不再禁用复选框，
        # 而是允许用户即时选择目标根目录（步骤2）。
        self.path_picker_btn = QPushButton("选择目录…")
        self.path_picker_btn.setToolTip("为当前工作区选择本地目录，作为「复制到工作区」的目标根")
        self.path_picker_btn.clicked.connect(self._on_pick_workspace_path)
        self.path_picker_btn.setVisible(False)
        opt_row1.addWidget(self.path_picker_btn)

        options_layout.addLayout(opt_row1)

        opt_row2 = QHBoxLayout()
        self.log_label = QLabel("关联拍摄日志:")
        opt_row2.addWidget(self.log_label)
        self.log_combo = QComboBox()
        self.log_combo.addItem("不关联", None)
        self.log_combo.setEnabled(False)
        self.log_combo.setMinimumWidth(200)
        opt_row2.addWidget(self.log_combo)
        opt_row2.addStretch()
        options_layout.addLayout(opt_row2)

        # 个人模式：隐藏「关联拍摄日志」选项（对象仍创建，
        # _start_import 中日志关联值固定为空，不依赖控件状态）
        if not is_enabled("shooting_log"):
            self.log_label.setVisible(False)
            self.log_combo.setVisible(False)

        bottom_layout.addWidget(options_group)

        action_row = QHBoxLayout()
        self.import_btn = QPushButton("📥 开始导入")
        self.import_btn.setToolTip("开始导入选中的文件到当前项目")
        self.import_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR.PRIMARY};
                color: white;
                padding: 10px 32px;
                border-radius: {RADIUS.BUTTON}px;
                font-size: {FONT_SIZE.MD}px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {COLOR.PRIMARY_HOVER}; }}
            QPushButton:disabled {{ background-color: {COLOR.DISABLED}; }}
        """)
        self.import_btn.clicked.connect(self._start_import)
        self.import_btn.setEnabled(False)
        action_row.addWidget(self.import_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setToolTip("取消正在进行的导入任务")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_import)
        action_row.addWidget(self.cancel_btn)

        action_row.addStretch()
        bottom_layout.addLayout(action_row)

        # 执行状态（合并进度条 + 状态标签 + 日志输出）
        status_group = QGroupBox("执行状态")
        status_layout = QVBoxLayout(status_group)
        self.status_panel = StatusPanel(log_min_height=40)
        status_layout.addWidget(self.status_panel)
        # 保留别名以减少方法体内 self.progress_bar / status_label / log_text 的改动
        self.progress_bar = self.status_panel.progress_bar
        self.status_label = self.status_panel.status_label
        self.log_text = self.status_panel.log_text
        bottom_layout.addWidget(status_group)

        right_layout.addWidget(bottom_widget)

        # 顶部水平布局：左选择器 + 右内容
        content_row = QHBoxLayout()
        content_row.setSpacing(16)
        content_row.addWidget(left_panel)
        content_row.addWidget(right_panel, 1)
        layout.addLayout(content_row, 1)

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self.selector.refresh()
        self._sync_copy_check_state()

    @Slot(object)
    def _on_project_changed(self, project_id):
        """选择控件的项目切换 → 加载日志下拉、更新导入按钮、更新复制目标提示

        全局项目广播已由 WorkspaceProjectSelector 内部处理，本方法只做本视图业务联动。
        """
        if project_id is None:
            self.current_project = None
            self.log_combo.clear()
            self.log_combo.addItem("不关联", None)
            self.log_combo.setEnabled(False)
            self.import_btn.setEnabled(False)
            self._update_copy_dest_label()
            return
        self.current_project = self.db_service.get_project(project_id)
        if self.current_project:
            self._log(f"已选择项目: {self.current_project.name}")
            self._load_logs(project_id)
            self._update_selected_label()
            self._update_copy_dest_label()

    def _get_current_workspace(self):
        """返回当前项目所属工作区，个人模式下不依赖隐藏的工作区下拉框。"""
        if not is_enabled("workspace_selector"):
            if self.current_project and self.current_project.workspace_id:
                workspace = self.db_service.get_workspace(
                    self.current_project.workspace_id
                )
                if workspace is not None:
                    return workspace
            return self.db_service.get_or_create_default_workspace()
        return self.selector.get_current_workspace()

    def _sync_copy_check_state(self):
        """根据当前工作区是否有 path，调整「复制到工作区」复选框与目录选择入口。

        - 未选具体工作区：禁用（无目标目录）
        - 工作区 path 为空：不再禁用复选框，而是允许用户勾选后即时选择目录
          （个人模式 default 工作区初始 path 为空，需提供设置入口而非卡死）
        - 工作区 path 非空：启用并提示目标路径
        """
        if not hasattr(self, 'copy_mode_check'):
            return
        ws = self._get_current_workspace()
        if ws is None:
            self.copy_mode_check.setEnabled(False)
            self.copy_mode_check.setChecked(False)
            self.copy_mode_check.setToolTip("请先选择具体项目/工作区")
            self._show_path_picker_button(False)
        elif not ws.path:
            # 个人模式常见情形：工作区尚未绑定本地目录。
            # 不再直接禁用复选框，而是允许勾选并即时选择目录，避免导入界面卡死。
            self.copy_mode_check.setEnabled(True)
            self.copy_mode_check.setToolTip(
                "当前工作区未设置目录，勾选后点击「选择目录…」指定复制目标根"
            )
            self._show_path_picker_button(True)
        else:
            self.copy_mode_check.setEnabled(True)
            self.copy_mode_check.setToolTip(
                f"勾选后将素材复制到：{ws.path}/<项目名>/ 下"
            )
            self._show_path_picker_button(False)
        # 同步复制目标提示
        self._update_copy_dest_label()

    def _show_path_picker_button(self, visible: bool):
        """显示/隐藏「选择目录…」按钮（仅当工作区无本地目录时需要）"""
        if hasattr(self, 'path_picker_btn'):
            self.path_picker_btn.setVisible(visible)

    def _choose_workspace_path(self, ws, title: str) -> bool:
        """选择、校验并持久化工作区目录；成功后更新传入的工作区对象。"""
        path = QFileDialog.getExistingDirectory(
            self, title, ws.path or str(Path.home())
        )
        if not path:
            return False
        if not is_writable_directory(path, create=True):
            QMessageBox.warning(self, "无法写入", f"所选目录不可写：\n{path}")
            return False
        if not self.db_service.update_workspace(ws.workspace_id, path=path):
            QMessageBox.warning(self, "保存失败", "工作区目录未能保存，请重试。")
            return False
        ws.path = path
        self._log(f"已设置工作区目录: {path}")
        self._sync_copy_check_state()
        return True

    def _on_pick_workspace_path(self):
        """为当前工作区选择一个本地目录，作为「复制到工作区」的目标根。

        选择后写回工作区并持久化，后续导入即可复制到 <目录>/<项目名>/ 下。
        选择前校验目录可写性，失败时给出明确提示而非静默失败（注意事项要求）。
        """
        ws = self._get_current_workspace()
        if ws is None:
            QMessageBox.warning(self, "提示", "请先选择项目/工作区")
            return
        self._choose_workspace_path(ws, "选择工作区目录")

    def _load_logs(self, project_id: str):
        self.log_combo.clear()
        self.log_combo.addItem("不关联", None)
        logs = self.db_service.get_shooting_logs(project_id)
        if logs:
            for log in logs:
                label = f"{log.scene} / {log.shot} / {log.take}"
                if log.description:
                    label += f" - {log.description}"
                self.log_combo.addItem(label, log.log_id)
            self.log_combo.setEnabled(True)
        else:
            self.log_combo.setEnabled(False)

    def _select_folder(self):
        path = pick_directory(self, "选择要导入的文件夹", category="import_source")
        if path:
            self.source_edit.setText(path)

    def set_source_folder(self, path: str, auto_scan: bool = True):
        """由外部（如存储卡自动识别）设置导入源目录。

        Args:
            path: 源目录路径
            auto_scan: 设置后是否立即触发扫描
        """
        if not path:
            return
        self.source_edit.setText(path)
        if auto_scan:
            self._scan_folder()

    def _on_copy_check_toggled(self, checked: bool):
        """复制到工作区复选框切换时，更新目标路径提示"""
        self._update_copy_dest_label()

    def _update_copy_dest_label(self):
        """根据当前工作区与项目，更新复制目标路径提示标签"""
        if not hasattr(self, 'copy_dest_label'):
            return
        if not self.copy_mode_check.isChecked():
            self.copy_dest_label.setText("")
            return
        ws = self._get_current_workspace()
        if ws is None or not ws.path:
            self.copy_dest_label.setText("（请点击「选择目录…」指定复制目标根）")
            self.copy_dest_label.setStyleSheet(f"color: {COLOR.DANGER}; font-size: {FONT_SIZE.SM}px;")
            return
        if self.current_project:
            dest = str(Path(ws.path) / self.current_project.name)
        else:
            dest = str(Path(ws.path) / "<项目名>")
        self.copy_dest_label.setText(f"→ {dest}")
        self.copy_dest_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")

    def _scan_folder(self):
        folder = self.source_edit.text()
        if not folder:
            QMessageBox.warning(self, "提示", "请先选择要导入的文件夹")
            return

        try:
            files = self.import_service.scan_media_folder(
                folder,
                recursive=self.recursive_check.isChecked(),
                include_images=self.include_images.isChecked(),
                include_videos=self.include_videos.isChecked(),
                include_raw=self.include_raw.isChecked()
            )
            # 保存全量列表，时间筛选时在此列表上过滤，无需重新扫描
            self._all_scanned_files = files
            self._apply_time_filter()
            total_size = sum(f.stat().st_size for f in files if f.exists())
            self._log(f"扫描完成: 发现 {len(files)} 个媒体文件, 总大小 {format_size(total_size)}")
        except Exception as e:
            import traceback
            show_error(
                title="扫描错误",
                description=str(e),
                details=traceback.format_exc(),
                parent=self,
            )
            self._log(f"扫描失败: {e}")

    def _on_time_filter_toggled(self, checked: bool):
        """启用/禁用时间筛选控件，并立即应用筛选"""
        self.start_time_edit.setEnabled(checked)
        self.end_time_edit.setEnabled(checked)
        self.today_btn.setEnabled(checked)
        self.week_btn.setEnabled(checked)
        self._apply_time_filter()

    def _set_time_range_today(self):
        """快捷设置时间范围为今天 00:00 ~ 23:59"""
        from PySide6.QtCore import QDate, QTime
        d = QDateTime.currentDateTime().date()
        self.start_time_edit.setDateTime(QDateTime(d, QTime(0, 0)))
        self.end_time_edit.setDateTime(QDateTime(d, QTime(23, 59)))

    def _set_time_range_week(self):
        """快捷设置时间范围为最近7天"""
        now = QDateTime.currentDateTime()
        self.start_time_edit.setDateTime(now.addDays(-6))
        self.end_time_edit.setDateTime(now)

    def _apply_time_filter(self):
        """根据时间筛选条件，从全量列表中过滤文件并刷新表格。

        若未启用时间筛选，显示全部文件；启用时按文件修改时间筛选。
        """
        if not self._all_scanned_files:
            return

        if self.time_filter_check.isChecked():
            start = self.start_time_edit.dateTime().toPython()
            end = self.end_time_edit.dateTime().toPython()
            filtered = []
            for f in self._all_scanned_files:
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if start <= mtime <= end:
                        filtered.append(f)
                except Exception:
                    # stat 失败的文件保留，避免遗漏
                    filtered.append(f)
            self._display_files(filtered)
            self.files_label.setText(
                f"共 {len(filtered)} 个文件"
                f"（已从 {len(self._all_scanned_files)} 个中筛选）"
            )
        else:
            self._display_files(self._all_scanned_files)
            self.files_label.setText(f"共 {len(self._all_scanned_files)} 个文件")

        self._update_selected_label()

    def _display_files(self, files):
        # 排序开启时先禁用，填充完再恢复，避免 setItem 过程中表格自动重排
        self.files_table.setSortingEnabled(False)
        self.files_table.blockSignals(True)
        try:
            self.files_table.setRowCount(len(files))
            sync_empty_state(self.files_table)
            for i, f in enumerate(files):
                stat = f.stat()
                # 勾选列：默认全部勾选；UserRole 存完整路径，排序后仍可定位
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                check_item.setCheckState(Qt.Checked)
                check_item.setData(Qt.UserRole, str(f))
                self.files_table.setItem(i, 0, check_item)
                self.files_table.setItem(i, 1, QTableWidgetItem(f.name))
                asset_type = self.import_service.classify_media_type(str(f))
                type_map = {
                    "image": "🖼️ 图片",
                    "video": "🎬 视频",
                    "raw": "📷 RAW",
                    "audio": "🎵 音频",
                    "other": "📄 其他"
                }
                self.files_table.setItem(i, 2, QTableWidgetItem(type_map.get(asset_type.value, asset_type.value)))
                self.files_table.setItem(i, 3, QTableWidgetItem(format_size(stat.st_size)))
                # 修改时间列（存储 timestamp 供排序，显示友好格式）
                mtime = datetime.fromtimestamp(stat.st_mtime)
                mtime_item = QTableWidgetItem(mtime.strftime("%Y-%m-%d %H:%M"))
                mtime_item.setData(Qt.UserRole, stat.st_mtime)
                self.files_table.setItem(i, 4, mtime_item)
                self.files_table.setItem(i, 5, QTableWidgetItem(str(f.parent)))
        finally:
            self.files_table.blockSignals(False)
        self.files_table.setSortingEnabled(True)
        self._update_selected_label()

    def _selected_files(self) -> list:
        """返回勾选的文件路径列表（按当前表格顺序）。"""
        paths = []
        for row in range(self.files_table.rowCount()):
            item = self.files_table.item(row, 0)
            if item is None:
                continue
            if item.checkState() == Qt.Checked:
                path = item.data(Qt.UserRole)
                if path:
                    paths.append(path)
        return paths

    def _set_all_checked(self, checked: bool):
        """全选 / 全不选"""
        self.files_table.blockSignals(True)
        try:
            for row in range(self.files_table.rowCount()):
                item = self.files_table.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self.files_table.blockSignals(False)
        self._update_selected_label()

    def _invert_selection(self):
        """反选：勾选/取消勾选互换"""
        self.files_table.blockSignals(True)
        try:
            for row in range(self.files_table.rowCount()):
                item = self.files_table.item(row, 0)
                if item is None:
                    continue
                new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
                item.setCheckState(new_state)
        finally:
            self.files_table.blockSignals(False)
        self._update_selected_label()

    def _update_selected_label(self):
        """更新已选计数与导入按钮可用状态。"""
        total = self.files_table.rowCount()
        selected = len(self._selected_files())
        self.selected_label.setText(f"已选 {selected} / {total}")
        self.import_btn.setEnabled(
            selected > 0 and self.current_project is not None
        )

    def _on_item_changed(self, item):
        """勾选状态变化时刷新计数（避免排序时误触发）。"""
        if item is not None and item.column() == 0:
            self._update_selected_label()

    def _on_selection_changed(self):
        """行选中变化 → 请求缩略图预览。"""
        index = self.files_table.currentIndex()
        if not index.isValid():
            return
        check_item = self.files_table.item(index.row(), 0)
        name_item = self.files_table.item(index.row(), 1)
        if check_item is None or name_item is None:
            return
        file_path = check_item.data(Qt.UserRole) or ""
        if not file_path:
            return
        self._preview_key = hashlib.md5(file_path.encode("utf-8")).hexdigest()
        self.preview_label.setText("生成缩略图中…")
        self.preview_info.setText(name_item.text())
        asset_type = self.import_service.classify_media_type(file_path).value
        pix = self.thumbnail_service.get_thumbnail(
            self._preview_key, file_path, asset_type, SIZE_LARGE
        )
        if pix is not None:
            self._apply_preview(self._preview_key, pix)

    @Slot(str, QPixmap)
    def _on_preview_ready(self, cache_key: str, pixmap: QPixmap):
        """异步缩略图生成完成：仅当仍是当前选中文件时回填。"""
        if cache_key == self._preview_key:
            self._apply_preview(cache_key, pixmap)

    def _apply_preview(self, cache_key: str, pixmap: QPixmap):
        self.preview_label.setText("")
        self.preview_label.setPixmap(pixmap.scaled(
            240, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def _on_file_double_clicked(self, index):
        """双击待导入文件 → 打开所在目录（通过表格单元格文本定位，避免排序后行号错位）"""
        if not index.isValid():
            return
        row = index.row()
        check_item = self.files_table.item(row, 0)
        if check_item is None:
            return
        path = check_item.data(Qt.UserRole) or ""
        if not path:
            return
        open_in_file_manager(path)

    def _on_files_context_menu(self, pos):
        """右键菜单：打开所在目录 / 复制路径"""
        index = self.files_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        check_item = self.files_table.item(row, 0)
        if check_item is None:
            return
        path = check_item.data(Qt.UserRole) or ""
        if not path:
            return
        menu = QMenu(self)
        action_open = menu.addAction("打开所在目录")
        action_copy = menu.addAction("复制文件路径")
        chosen = menu.exec(self.files_table.viewport().mapToGlobal(pos))
        if chosen is action_open:
            open_in_file_manager(path)
        elif chosen is action_copy:
            QApplication.clipboard().setText(path)

    def _start_import(self):
        if not self.current_project:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return

        selected_files = self._selected_files()
        if not selected_files:
            QMessageBox.warning(self, "提示", "请先勾选要导入的文件（☑ 列）")
            return

        copy_to_workspace = self.copy_mode_check.isChecked()
        workspace_dir = None
        if copy_to_workspace:
            # 复制目录自动基于当前工作区.path / 项目名
            ws = self._get_current_workspace()
            if ws is None:
                QMessageBox.warning(self, "提示", "请先选择项目/工作区")
                return
            # 工作区未设置本地目录：在导入时直接弹目录选择（一次性补齐），
            # 选择成功后继续；用户取消则放弃本次导入，不影响其他操作。
            if not ws.path or not is_writable_directory(ws.path):
                if not self._choose_workspace_path(ws, "选择工作区目录（复制到此处）"):
                    return
            workspace_dir = str(Path(ws.path) / self.current_project.name)

            # 覆盖确认：检查工作区目标目录中是否已存在同名文件
            try:
                source_names = [Path(p).name for p in selected_files]
                conflicts = find_overwrite_conflicts(source_names, [workspace_dir])
                if conflicts:
                    names = next(iter(conflicts.values()))
                    preview = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
                    reply = QMessageBox.question(
                        self, "存在同名文件",
                        f"工作区目标目录中已存在 {len(names)} 个同名文件，继续复制将覆盖：\n\n  · {preview}\n\n是否继续？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        self._log("用户取消导入：工作区目录存在同名文件")
                        return
            except Exception as e:
                self._log(f"（警告）覆盖冲突检测失败: {e}")

        # 个人模式固定不关联拍摄日志（不依赖已隐藏控件的取值）
        log_id = self.log_combo.currentData() if is_enabled("shooting_log") else None
        scene = ""
        shot = ""
        if log_id:
            log = self.db_service.get_shooting_log(log_id)
            if log:
                scene = log.scene
                shot = log.shot

        self.import_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self._cancel_requested = False

        self.worker = WorkerThread(
            self.import_service.import_assets,
            self.current_project.project_id,
            selected_files,
            compute_checksum=self.checksum_check.isChecked(),
            copy_to_workspace=copy_to_workspace,
            workspace_dir=workspace_dir,
            log_id=log_id,
            scene=scene,
            shot=shot,
            cancel_check=lambda: self._cancel_requested,
            inject_progress=True,
        )
        self.worker.finished.connect(self._on_import_finished)
        self.worker.error.connect(self._on_import_error)
        self.worker.progress.connect(self._on_progress)
        # 线程结束后自动释放，避免 QThread 对象泄漏
        self.worker.thread_finished.connect(self.worker.deleteLater)
        self.worker.start()

        self._log("开始导入...")
        self.status_label.setText("正在导入...")

    def _cancel_import(self):
        if self.worker and self.worker.isRunning():
            self._cancel_requested = True
            # service 通过 cancel_check 回调判断取消，无需 Qt 中断标志
            self._log("取消导入...")
            self.status_label.setText("正在取消...")

    @Slot(str, float, str)
    def _on_progress(self, target: str, progress: float, message: str):
        self.progress_bar.setValue(int(progress * 100))
        self.status_label.setText(f"{message} ({int(progress * 100)}%)")

    @Slot(object)
    def _on_import_finished(self, result):
        self.import_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        # worker 已连 deleteLater，这里清空引用避免悬挂
        self.worker = None

        # 防御 result 为 None 或非 dict
        if not isinstance(result, dict):
            self.status_label.setText("❌ 导入返回异常结果")
            self._log(f"导入返回异常: {result!r}")
            QMessageBox.warning(self, "导入异常", "导入任务返回了异常结果，请重试。")
            return

        imported = result.get('imported', 0)
        skipped = result.get('skipped', 0)
        failed = result.get('failed', 0)
        cancelled = result.get('cancelled', False)

        if cancelled:
            self.status_label.setText(
                f"⚠️ 已取消: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个"
            )
            self._log(f"导入已取消: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个")
        else:
            self.status_label.setText(
                f"✅ 导入完成: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个"
            )
            self._log(f"导入完成: 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个")

        if failed > 0:
            fail_details = [d for d in result.get('details', []) if d.get('status') == 'failed']
            for d in fail_details[:5]:
                self._log(f"  失败: {d['path']} - {d.get('error', '未知错误')}")

        # 广播数据变更，通知日志/检索/素材信息视图刷新
        if imported > 0:
            get_data_bus().emit_data_changed("assets_changed")

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.setWindowTitle("导入完成")
        msg_box.setText(f"导入完成！\n\n成功: {imported} 个\n跳过: {skipped} 个\n失败: {failed} 个")
        goto_backup_btn = msg_box.addButton("去数据备份", QMessageBox.AcceptRole)
        ok_btn = msg_box.addButton("确定", QMessageBox.RejectRole)
        msg_box.setDefaultButton(ok_btn)
        msg_box.exec()
        if msg_box.clickedButton() is goto_backup_btn:
            try:
                main_window = self.window()
                backup_idx = get_nav_index("backup")
                # 目标页未激活时 get_nav_index 返回 None，禁止传给 setCurrentRow
                if hasattr(main_window, 'nav_list') and backup_idx is not None:
                    main_window.nav_list.setCurrentRow(backup_idx)
            except Exception as e:
                logger.warning(f"跳转备份视图失败: {e}")

    @Slot(str)
    def _on_import_error(self, error: str):
        self.import_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(f"❌ 错误: {error}")
        self._log(f"导入错误: {error}")
        self.worker = None
        show_error(
            title="导入错误",
            description=error,
            details=error,
            parent=self,
        )

    def _log(self, message: str):
        self.status_panel.log(message)
