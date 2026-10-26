"""设置对话框：数据存储位置重设 / 临时文件清理 / 最近路径管理 / 运行参数"""

import platform
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from DITWorkstation.App import config
from DITWorkstation.App.feature_flags import (
    UsageMode,
    get_minimal_import_target_dir,
    get_usage_mode,
    is_enabled,
    is_minimal_mode,
    set_minimal_import_target_dir,
    set_usage_mode,
)
from DITWorkstation.Services.card_automation_service import SOP_STEPS
from DITWorkstation.Utils import (
    clear_recent_paths,
    count_recent_paths,
    delete_log_files,
    export_settings,
    format_size,
    get_db_service,
    get_thumbnail_service,
    import_settings,
    log_files_summary,
    logger,
    open_in_file_manager,
    pick_directory,
    pick_save_file,
    save_app_settings,
)
from DITWorkstation.Views.Styles.theme import (
    COLOR,
    FONT_SIZE,
    SECONDARY_BUTTON_QSS,
    SUBTITLE_QSS,
    TITLE_QSS,
)
from DITWorkstation.Views.Widgets.path_field import PathField

#: SOP 步骤名 → 展示名（模块化勾选用；步骤顺序以 SOP_STEPS 为准）
_SOP_STEP_LABELS = {
    "import": "导入素材",
    "backup": "备份",
    "raw_extract": "RAW 提取",
    "rename": "重命名",
    "report": "报告",
}

#: 自动重命名模板预设（与文件重命名视图的 4 种预设一致）
_RENAME_PATTERN_PRESETS = (
    "{scene}_{shot}_{take}_{number}",
    "{scene}_{shot}_{take}_{original}",
    "{prefix}_{scene}_{number}",
    "{date}_{scene}_{shot}_{number}",
)


def _post_ingest_hook_presets() -> list[tuple[str, str]]:
    """按平台返回常用 post-ingest 钩子预设（展示名, 命令）。

    命令均为可直接执行的 shell 片段，上下文经 DIT_* 环境变量注入。
    """
    system = platform.system()
    if system == "Darwin":
        return [
            (
                "macOS 桌面通知",
                (
                    'osascript -e \'display notification "相机卡处理完成" '
                    'with title "DIT 工作站"\''
                ),
            ),
            ("macOS 打开源卡目录", 'open "$DIT_SOURCE_PATH"'),
            (
                "追加日志到桌面文件",
                (
                    'echo "$(date "+%F %T") $DIT_SOURCE_PATH 完成" '
                    ">> ~/Desktop/dit_hooks.log"
                ),
            ),
            (
                "Webhook 通知（POST JSON）",
                (
                    'curl -s -X POST -H "Content-Type: application/json" '
                    '-d "{\\"event\\":\\"post_ingest\\",'
                    '\\"source\\":\\"$DIT_SOURCE_PATH\\"}" https://example.com/hook'
                ),
            ),
        ]
    if system == "Windows":
        return [
            (
                "Windows 弹窗通知",
                (
                    "powershell -NoProfile -Command "
                    '"Add-Type -AssemblyName PresentationFramework; '
                    "[System.Windows.MessageBox]::Show("
                    "$env:DIT_SOURCE_PATH, 'DIT 工作站 - 相机卡处理完成')\""
                ),
            ),
            ("Windows 打开源卡目录", "explorer %DIT_SOURCE_PATH%"),
            (
                "追加日志到临时文件",
                'echo %DIT_SOURCE_PATH% >> "%TEMP%\\dit_hooks.log"',
            ),
            (
                "Webhook 通知（POST JSON）",
                (
                    'curl -s -X POST -H "Content-Type: application/json" '
                    '-d "{\\"event\\":\\"post_ingest\\"}" https://example.com/hook'
                ),
            ),
        ]
    return [
        (
            "追加日志到桌面文件",
            'echo "$(date "+%F %T") $DIT_SOURCE_PATH 完成" >> ~/dit_hooks.log',
        ),
        ("打开源卡目录", 'xdg-open "$DIT_SOURCE_PATH"'),
        (
            "Webhook 通知（POST JSON）",
            (
                'curl -s -X POST -H "Content-Type: application/json" '
                '-d "{\\"event\\":\\"post_ingest\\",'
                '\\"source\\":\\"$DIT_SOURCE_PATH\\"}" https://example.com/hook'
            ),
        ),
    ]


#: 使用模式值 → 展示名（模式切换提示文案用）
_MODE_LABELS = {
    UsageMode.TEAM: "团队模式",
    UsageMode.PERSONAL: "个人模式",
    UsageMode.MINIMAL: "极简模式",
}


class SettingsDialog(QDialog):
    """应用设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(640, 640)
        self.thumbnail_service = get_thumbnail_service()
        self._setup_ui()
        self._refresh_states()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("设置")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel(
            "使用场景、外观主题、数据存储、完整性校验、自动更新、存储卡与运行参数"
        )
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # ===== 使用场景（功能模式开关）=====
        usage_group = QGroupBox("🧭 使用场景")
        usage_layout = QVBoxLayout(usage_group)
        usage_row = QHBoxLayout()
        usage_row.addWidget(QLabel("界面模式:"))
        self.usage_mode_combo = QComboBox()
        self.usage_mode_combo.addItem(
            "团队模式（完整 DIT 工作流）", UsageMode.TEAM.value
        )
        self.usage_mode_combo.addItem(
            "个人模式（独立创作者）", UsageMode.PERSONAL.value
        )
        self.usage_mode_combo.addItem("极简模式（仅媒体导入）", UsageMode.MINIMAL.value)
        self.usage_mode_combo.setToolTip(
            "个人模式隐藏拍摄日志、素材评级、报告、模板、归档等团队功能，\n"
            "仅保留项目管理、导入、单目标备份、RAW 提取、重命名与检索。\n"
            "极简模式仅保留「媒体导入」，其余模块全部隐藏，并可自定义\n"
            "导入素材复制到本机时的保存目录。\n"
            "切换后重启应用生效；数据库数据不受影响，可随时切回。"
        )
        self.usage_mode_combo.currentIndexChanged.connect(self._on_usage_mode_changed)
        usage_row.addWidget(self.usage_mode_combo)
        usage_row.addStretch()
        usage_layout.addLayout(usage_row)
        usage_hint = QLabel("模式切换在重启应用后生效")
        usage_hint.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
        )
        usage_layout.addWidget(usage_hint)

        # ---- 极简模式专属：媒体保存目录（自定义路径设置项）----
        # 仅当「界面模式」下拉选中极简模式时可见，便于切换后立即完成设置。
        self.minimal_dir_widget = QWidget()
        minimal_dir_box = QVBoxLayout(self.minimal_dir_widget)
        minimal_dir_box.setContentsMargins(0, 4, 0, 0)
        minimal_dir_box.setSpacing(6)

        minimal_dir_title = QLabel("媒体保存目录（导入的素材复制到 <目录>/<项目名>/）:")
        minimal_dir_title.setWordWrap(True)
        minimal_dir_title.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY};")
        minimal_dir_box.addWidget(minimal_dir_title)

        minimal_dir_row = QHBoxLayout()
        self.minimal_dir_label = QLabel("")
        self._style_path_label(self.minimal_dir_label)
        minimal_dir_row.addWidget(self.minimal_dir_label, 1)
        self.minimal_dir_change_btn = QPushButton("选择…")
        self.minimal_dir_change_btn.setToolTip("选择导入素材在本机的目标保存目录")
        self.minimal_dir_change_btn.clicked.connect(self._change_minimal_import_dir)
        minimal_dir_row.addWidget(self.minimal_dir_change_btn)
        self.minimal_dir_open_btn = QPushButton("打开")
        self.minimal_dir_open_btn.setToolTip("在文件管理器中打开保存目录")
        self.minimal_dir_open_btn.clicked.connect(self._open_minimal_import_dir)
        minimal_dir_row.addWidget(self.minimal_dir_open_btn)
        self.minimal_dir_clear_btn = QPushButton("清除")
        self.minimal_dir_clear_btn.setToolTip(
            "清除保存目录设置（导入时将重新提示选择）"
        )
        self.minimal_dir_clear_btn.clicked.connect(self._clear_minimal_import_dir)
        minimal_dir_row.addWidget(self.minimal_dir_clear_btn)
        minimal_dir_box.addLayout(minimal_dir_row)

        self.minimal_dir_widget.setVisible(is_minimal_mode())
        usage_layout.addWidget(self.minimal_dir_widget)
        layout.addWidget(usage_group)

        # ===== 数据存储位置 =====
        dir_group = QGroupBox("📂 数据存储位置")
        dir_form = QFormLayout(dir_group)
        dir_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.db_dir_label = QLabel("")
        self._style_path_label(self.db_dir_label)
        dir_form.addRow(
            "数据库目录:",
            self._make_dir_row(
                self.db_dir_label,
                self._change_db_dir,
                lambda: open_in_file_manager(str(config.effective_db_dir)),
            ),
        )

        self.report_dir_label = QLabel("")
        self._style_path_label(self.report_dir_label)
        dir_form.addRow(
            "报告输出:",
            self._make_dir_row(
                self.report_dir_label,
                self._change_report_dir,
                lambda: open_in_file_manager(str(config.report_dir)),
            ),
        )

        self.log_dir_label = QLabel("")
        self._style_path_label(self.log_dir_label)
        dir_form.addRow(
            "日志目录:",
            self._make_dir_row(
                self.log_dir_label,
                self._change_log_dir,
                lambda: open_in_file_manager(str(config.log_dir)),
            ),
        )

        self.thumb_dir_label = QLabel("")
        self._style_path_label(self.thumb_dir_label)
        dir_form.addRow(
            "缩略图缓存:",
            self._make_dir_row(
                self.thumb_dir_label,
                self._change_thumb_dir,
                lambda: open_in_file_manager(str(self.thumbnail_service.cache_dir)),
            ),
        )
        layout.addWidget(dir_group)

        # ===== 清理临时文件 =====
        temp_group = QGroupBox("🧹 清理临时文件")
        temp_layout = QVBoxLayout(temp_group)

        log_info_row = QHBoxLayout()
        self.log_info_label = QLabel("")
        self.log_info_label.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY};")
        log_info_row.addWidget(self.log_info_label, 1)
        self.delete_logs_btn = QPushButton("🗑 删除日志文件")
        self.delete_logs_btn.setToolTip("删除日志目录下的全部日志文件（含轮转文件）")
        self.delete_logs_btn.clicked.connect(self._delete_logs)
        log_info_row.addWidget(self.delete_logs_btn)
        temp_layout.addLayout(log_info_row)

        cache_row = QHBoxLayout()
        self.cache_info_label = QLabel("")
        self.cache_info_label.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY};")
        cache_row.addWidget(self.cache_info_label, 1)
        self.clear_cache_btn = QPushButton("🗑 清理缩略图缓存")
        self.clear_cache_btn.setToolTip("删除已生成的缩略图文件，下次查看时重新生成")
        self.clear_cache_btn.clicked.connect(self._clear_cache)
        cache_row.addWidget(self.clear_cache_btn)
        temp_layout.addLayout(cache_row)

        layout.addWidget(temp_group)

        # ===== 最近路径 =====
        recent_group = QGroupBox("📁 最近路径")
        recent_layout = QVBoxLayout(recent_group)
        recent_row = QHBoxLayout()
        self.recent_info_label = QLabel("")
        self.recent_info_label.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY};")
        recent_row.addWidget(self.recent_info_label, 1)
        self.clear_recent_btn = QPushButton("🗑 清空最近路径记录")
        self.clear_recent_btn.setToolTip("清空所有「选择目录」对话框的最近使用记录")
        self.clear_recent_btn.clicked.connect(self._clear_recent)
        recent_row.addWidget(self.clear_recent_btn)
        recent_layout.addLayout(recent_row)
        layout.addWidget(recent_group)

        # ===== 备份默认选项 =====
        backup_group = QGroupBox("📦 备份默认选项")
        backup_layout = QVBoxLayout(backup_group)
        self.verify_after_copy_check = QCheckBox("拷贝后默认验证完整性")
        self.verify_after_copy_check.setToolTip(
            "备份时「拷贝后验证完整性」的默认状态（可在备份视图临时调整）"
        )
        self.verify_after_copy_check.toggled.connect(self._on_verify_after_copy_toggled)
        backup_layout.addWidget(self.verify_after_copy_check)

        self.verify_source_reread_check = QCheckBox("备份完成后复读源卡校验（更安全）")
        self.verify_source_reread_check.setToolTip(
            "所有目标拷贝并验证通过后，重新读取源卡逐文件比对哈希。\n"
            "能发现拷贝间隔内源卡内容变化（卡劣化/坏块），代价是源读取时间翻倍。\n"
            "发现不一致时状态栏会警告「此卡可能劣化，请勿擦除」。"
        )
        self.verify_source_reread_check.toggled.connect(
            self._on_verify_source_reread_toggled
        )
        backup_layout.addWidget(self.verify_source_reread_check)
        # 极简模式无备份模块，隐藏其专属默认项
        backup_group.setVisible(not is_minimal_mode())
        layout.addWidget(backup_group)

        # ===== 存储卡自动识别 =====
        volume_group = QGroupBox("💾 存储卡")
        volume_layout = QVBoxLayout(volume_group)
        self.auto_detect_check = QCheckBox("检测到存储卡时自动跳转到导入视图")
        self.auto_detect_check.setToolTip(
            "插入包含素材的存储卡（如相机 SD/CF 卡）时，自动切换到「媒体导入」"
            "并预填源目录、开始扫描；不勾选则仅在状态栏提示。"
        )
        self.auto_detect_check.toggled.connect(self._on_auto_detect_toggled)
        volume_layout.addWidget(self.auto_detect_check)

        self.skip_processed_cards_check = QCheckBox("跳过已处理过的存储卡（多卡去重）")
        self.skip_processed_cards_check.setToolTip(
            "自动任务完成后记录卡的指纹；同一张卡再次插入时不再重复处理，"
            "多张卡会排队串行处理。"
        )
        self.skip_processed_cards_check.toggled.connect(
            self._on_skip_processed_cards_toggled
        )
        volume_layout.addWidget(self.skip_processed_cards_check)

        self.auto_card_automation_check = QCheckBox("检测到相机卡后自动执行配置")
        self.auto_card_automation_check.setToolTip(
            "按下面选择的项目和备份方案自动导入或备份；未配置完整时只提示，不会启动任务。"
        )
        self.auto_card_automation_check.toggled.connect(
            self._on_auto_card_automation_toggled
        )
        volume_layout.addWidget(self.auto_card_automation_check)

        self.auto_card_import_check = QCheckBox("自动导入素材")
        self.auto_card_import_check.toggled.connect(self._on_auto_card_import_toggled)
        volume_layout.addWidget(self.auto_card_import_check)

        self.auto_card_backup_check = QCheckBox("自动执行备份方案")
        self.auto_card_backup_check.toggled.connect(self._on_auto_card_backup_toggled)
        volume_layout.addWidget(self.auto_card_backup_check)

        # ---- SOP 模块化勾选：勾选即拼入流程（按 SOP_STEPS 固定顺序执行）----
        self.auto_card_steps_label = QLabel(
            "SOP 流程模块（勾选即拼入流程；全部不勾选时按上面两个开关执行）:"
        )
        volume_layout.addWidget(self.auto_card_steps_label)
        steps_grid = QGridLayout()
        steps_grid.setContentsMargins(0, 0, 0, 0)
        self._sop_step_checks: dict[str, QCheckBox] = {}
        for column, step in enumerate(SOP_STEPS):
            check = QCheckBox(_SOP_STEP_LABELS.get(step, step))
            check.setToolTip(f"步骤名：{step}")
            check.toggled.connect(self._save_advanced_card_automation)
            self._sop_step_checks[step] = check
            steps_grid.addWidget(check, 0, column)
        steps_grid.setColumnStretch(len(SOP_STEPS), 1)
        volume_layout.addLayout(steps_grid)

        # ---- RAW 提取输出目录：浏览选择 / 打开 / 清除 + 可手动编辑 ----
        self.auto_card_raw_output_label = QLabel("RAW 提取输出目录:")
        volume_layout.addWidget(self.auto_card_raw_output_label)
        self.auto_card_raw_output_field = PathField(
            "raw_extract 步骤的输出目录（可浏览选择或手动输入）"
        )
        self.auto_card_raw_output_field.set_editable(True)
        self.auto_card_raw_output_field.path_changed.connect(
            self._save_advanced_card_automation
        )
        volume_layout.addWidget(self.auto_card_raw_output_field)

        # ---- 自动重命名模板：预设下拉（可编辑）+ 手动输入混合 ----
        self.auto_card_rename_pattern_label = QLabel("自动重命名模板:")
        volume_layout.addWidget(self.auto_card_rename_pattern_label)
        self.auto_card_rename_combo = QComboBox()
        self.auto_card_rename_combo.setEditable(True)
        for pattern in _RENAME_PATTERN_PRESETS:
            self.auto_card_rename_combo.addItem(pattern, pattern)
        self.auto_card_rename_combo.setToolTip(
            "从预设模板选择后可再手动修改；支持变量：{scene} {shot} {take} "
            "{original} {number} {prefix} {suffix} {date}"
        )
        self.auto_card_rename_combo.activated.connect(self._on_rename_preset_activated)
        combo_line = self.auto_card_rename_combo.lineEdit()
        if combo_line is not None:
            combo_line.setPlaceholderText(
                "rename 步骤的命名模板，例如 {scene}_{number}"
            )
            combo_line.textEdited.connect(self._save_advanced_card_automation)
        volume_layout.addWidget(self.auto_card_rename_combo)

        # ---- 自动报告路径：浏览选择 + 手动输入混合 ----
        self.auto_card_report_path_label = QLabel("自动报告路径:")
        volume_layout.addWidget(self.auto_card_report_path_label)
        report_row = QHBoxLayout()
        self.auto_card_report_path_edit = QLineEdit()
        self.auto_card_report_path_edit.setPlaceholderText(
            "report 步骤的 PDF 路径，可留空使用默认目录"
        )
        self.auto_card_report_path_edit.textEdited.connect(
            self._save_advanced_card_automation
        )
        self.auto_card_report_browse_btn = QPushButton("浏览…")
        self.auto_card_report_browse_btn.setStyleSheet(SECONDARY_BUTTON_QSS)
        self.auto_card_report_browse_btn.setToolTip(
            "用系统文件对话框选择报告输出 PDF 路径"
        )
        self.auto_card_report_browse_btn.clicked.connect(self._browse_report_path)
        report_row.addWidget(self.auto_card_report_path_edit, 1)
        report_row.addWidget(self.auto_card_report_browse_btn)
        volume_layout.addLayout(report_row)

        # ---- post-ingest 钩子：常用预设下拉（可编辑）+ 手动输入混合 ----
        self.post_ingest_hook_label = QLabel("post-ingest 钩子命令:")
        volume_layout.addWidget(self.post_ingest_hook_label)
        self.post_ingest_hook_combo = QComboBox()
        self.post_ingest_hook_combo.setEditable(True)
        for preset_label, command in _post_ingest_hook_presets():
            self.post_ingest_hook_combo.addItem(preset_label, command)
        self.post_ingest_hook_combo.setToolTip(
            "自动流程完成后执行的 shell 命令（webhook/通知/同步脚本均可），留空禁用。\n"
            "从下拉选择常用预设后自动填入命令，可再手动修改；\n"
            "上下文经环境变量注入：DIT_SOURCE_PATH、DIT_PROJECT_ID、"
            "DIT_CAMERA_MAKE、DIT_CAMERA_MODEL、DIT_RESULT（JSON）。"
        )
        self.post_ingest_hook_combo.activated.connect(self._on_hook_preset_activated)
        hook_line = self.post_ingest_hook_combo.lineEdit()
        if hook_line is not None:
            hook_line.setPlaceholderText("留空禁用；可从下拉选择常用预设再修改")
            hook_line.textEdited.connect(self._save_advanced_card_automation)
        volume_layout.addWidget(self.post_ingest_hook_combo)

        volume_form = QFormLayout()
        self.auto_card_project_combo = QComboBox()
        self.auto_card_template_combo = QComboBox()
        self.auto_card_project_combo.currentIndexChanged.connect(
            self._on_auto_card_project_changed
        )
        self.auto_card_template_combo.currentIndexChanged.connect(
            self._on_auto_card_template_changed
        )
        self.auto_card_project_label = QLabel("自动关联项目:")
        self.auto_card_template_label = QLabel("自动备份方案:")
        volume_form.addRow(self.auto_card_project_label, self.auto_card_project_combo)
        volume_form.addRow(self.auto_card_template_label, self.auto_card_template_combo)
        volume_layout.addLayout(volume_form)
        layout.addWidget(volume_group)

        # 个人模式隐藏相机卡自动化配置（个人模式禁止启动自动化流程，
        # 只保留「检测到存储卡跳转导入」的手动辅助能力）
        self._automation_widgets = [
            self.auto_card_automation_check,
            self.auto_card_import_check,
            self.auto_card_backup_check,
            self.auto_card_steps_label,
            *self._sop_step_checks.values(),
            self.auto_card_raw_output_label,
            self.auto_card_raw_output_field,
            self.auto_card_rename_pattern_label,
            self.auto_card_rename_combo,
            self.auto_card_report_path_label,
            self.auto_card_report_path_edit,
            self.auto_card_report_browse_btn,
            self.post_ingest_hook_label,
            self.post_ingest_hook_combo,
            self.auto_card_project_label,
            self.auto_card_project_combo,
            self.auto_card_template_label,
            self.auto_card_template_combo,
        ]
        for w in self._automation_widgets:
            w.setVisible(is_enabled("card_automation"))

        # ===== 完整性校验调度 =====
        integrity_group = QGroupBox("🛡 完整性校验")
        integrity_layout = QVBoxLayout(integrity_group)
        self.integrity_enable_check = QCheckBox("启用定期完整性校验")
        self.integrity_enable_check.toggled.connect(self._on_integrity_enabled)
        integrity_layout.addWidget(self.integrity_enable_check)

        integrity_row = QHBoxLayout()
        integrity_row.addWidget(QLabel("校验间隔（小时）:"))
        self.integrity_interval_spin = QSpinBox()
        self.integrity_interval_spin.setRange(1, 24 * 90)
        self.integrity_interval_spin.setValue(168)
        self.integrity_interval_spin.valueChanged.connect(self._on_integrity_interval)
        integrity_row.addWidget(self.integrity_interval_spin)
        integrity_row.addWidget(QLabel("范围:"))
        self.integrity_scope_combo = QComboBox()
        self.integrity_scope_combo.addItem("全部项目", "all")
        self.integrity_scope_combo.addItem("仅备份过素材的项目", "backup")
        self.integrity_scope_combo.currentIndexChanged.connect(self._on_integrity_scope)
        integrity_row.addWidget(self.integrity_scope_combo)
        integrity_row.addStretch()
        integrity_layout.addLayout(integrity_row)

        integrity_hint = QLabel("应用闲置时自动校验备份完整性，结果写入操作审计日志")
        integrity_hint.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
        )
        integrity_layout.addWidget(integrity_hint)
        # 极简模式无备份/校验模块，隐藏该分组
        integrity_group.setVisible(not is_minimal_mode())
        layout.addWidget(integrity_group)

        settings_io_group = QGroupBox("设置迁移")
        settings_io_layout = QHBoxLayout(settings_io_group)
        settings_io_hint = QLabel("导入的设置将在重启应用后生效")
        settings_io_hint.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY};")
        settings_io_layout.addWidget(settings_io_hint, 1)
        self.export_settings_btn = QPushButton("导出设置...")
        self.export_settings_btn.clicked.connect(self._export_settings)
        settings_io_layout.addWidget(self.export_settings_btn)
        self.import_settings_btn = QPushButton("导入设置...")
        self.import_settings_btn.clicked.connect(self._import_settings)
        settings_io_layout.addWidget(self.import_settings_btn)
        layout.addWidget(settings_io_group)

        layout.addStretch()
        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    @staticmethod
    def _style_path_label(label: QLabel):
        label.setTextInteractionFlags(
            label.textInteractionFlags() | Qt.TextSelectableByMouse
        )
        label.setStyleSheet(
            f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
        )

    def _make_dir_row(self, path_label: QLabel, change_cb, open_cb) -> QHBoxLayout:
        """构造「路径标签 + 更改… + 打开」行。"""
        row = QHBoxLayout()
        row.addWidget(path_label, 1)
        change_btn = QPushButton("更改…")
        change_btn.clicked.connect(change_cb)
        row.addWidget(change_btn)
        open_btn = QPushButton("打开")
        open_btn.clicked.connect(open_cb)
        row.addWidget(open_btn)
        return row

    def _refresh_states(self):
        """刷新各项状态显示。"""
        # 使用场景：回填当前模式（阻断信号避免误触发保存/重启提示）
        self.usage_mode_combo.blockSignals(True)
        self.usage_mode_combo.setCurrentIndex(
            max(0, self.usage_mode_combo.findData(get_usage_mode().value))
        )
        self.usage_mode_combo.blockSignals(False)
        # 保存目录可见性跟随下拉当前选择（而非已持久化模式），
        # 保证用户切换到极简模式后可立即设置目录
        self._sync_minimal_dir_visibility()
        self._refresh_minimal_dir_label()

        self.db_dir_label.setText(str(config.effective_db_dir))
        self.report_dir_label.setText(str(config.report_dir))
        self.log_dir_label.setText(str(config.log_dir))
        self.thumb_dir_label.setText(str(self.thumbnail_service.cache_dir))

        log_count, log_size = log_files_summary()
        self.log_info_label.setText(
            f"日志目录：{config.log_dir}\n共 {log_count} 个文件，占用 {format_size(log_size)}"
        )
        self.cache_info_label.setText(
            f"缓存目录：{self.thumbnail_service.cache_dir}\n"
            f"当前占用：{format_size(self.thumbnail_service.cache_size())}"
        )
        recent_total = count_recent_paths()
        self.recent_info_label.setText(f"共 {recent_total} 条最近使用记录")
        self.verify_after_copy_check.setChecked(config.verify_after_copy)
        self.verify_source_reread_check.setChecked(
            getattr(config, "verify_source_reread", False)
        )
        self.auto_detect_check.setChecked(config.auto_detect_volume)
        self.skip_processed_cards_check.setChecked(
            bool(getattr(config, "skip_processed_cards", True))
        )
        interval = int(getattr(config, "integrity_check_interval_hours", 0) or 0)
        self.integrity_enable_check.setChecked(interval > 0)
        self.integrity_interval_spin.setValue(interval if interval > 0 else 168)
        self.integrity_scope_combo.blockSignals(True)
        idx = self.integrity_scope_combo.findData(config.integrity_check_scope)
        self.integrity_scope_combo.setCurrentIndex(max(0, idx))
        self.integrity_scope_combo.blockSignals(False)
        self._load_automation_options()

    def _load_automation_options(self):
        """加载相机卡自动化所需的项目和备份方案下拉项。"""
        try:
            db = get_db_service()
            projects = db.get_projects()
            templates = db.get_backup_templates()
        except Exception as e:
            logger.warning(f"加载相机卡自动化配置失败: {e}")
            projects, templates = [], []
        self.auto_card_project_combo.blockSignals(True)
        self.auto_card_project_combo.clear()
        self.auto_card_project_combo.addItem("（未选择项目）", "")
        for project in projects:
            self.auto_card_project_combo.addItem(project.name, project.project_id)
        self.auto_card_project_combo.setCurrentIndex(
            max(0, self.auto_card_project_combo.findData(config.auto_card_project_id))
        )
        self.auto_card_project_combo.blockSignals(False)

        self.auto_card_template_combo.blockSignals(True)
        self.auto_card_template_combo.clear()
        self.auto_card_template_combo.addItem("（不自动备份）", "")
        for template in templates:
            self.auto_card_template_combo.addItem(template.name, template.template_id)
        self.auto_card_template_combo.setCurrentIndex(
            max(0, self.auto_card_template_combo.findData(config.auto_card_template_id))
        )
        self.auto_card_template_combo.blockSignals(False)
        self.auto_card_automation_check.setChecked(config.auto_card_automation_enabled)
        self.auto_card_import_check.setChecked(config.auto_card_import)
        self.auto_card_backup_check.setChecked(config.auto_card_backup)
        saved_steps = set(getattr(config, "auto_card_steps", []) or [])
        for step, check in self._sop_step_checks.items():
            check.blockSignals(True)
            check.setChecked(step in saved_steps)
            check.blockSignals(False)
        self.auto_card_raw_output_field.set_path(config.auto_card_raw_output_dir)
        self.auto_card_rename_combo.blockSignals(True)
        self.auto_card_rename_combo.setEditText(config.auto_card_rename_pattern)
        self.auto_card_rename_combo.blockSignals(False)
        self.auto_card_report_path_edit.setText(config.auto_card_report_path)
        self.post_ingest_hook_combo.blockSignals(True)
        hook_line = self.post_ingest_hook_combo.lineEdit()
        if hook_line is not None:
            hook_line.setText(getattr(config, "post_ingest_hook_command", ""))
        self.post_ingest_hook_combo.blockSignals(False)

    # ===== 使用场景（功能模式开关）=====

    def _on_usage_mode_changed(self, index: int):
        """切换使用场景：立即写入 app_config.usage_mode，提示重启后生效。

        不尝试动态重建已存在的主窗口（设计文档 6.4：模式在启动时确定）。
        """
        value = self.usage_mode_combo.itemData(index)
        try:
            mode = UsageMode(value)
        except ValueError:
            logger.warning(f"非法使用模式值: {value!r}，忽略")
            return
        # 无论是否发生实际切换，都同步保存目录设置项的可见性，
        # 避免用户切到极简模式时看不到唯一的路径设置入口
        self._sync_minimal_dir_visibility()
        if mode == get_usage_mode():
            return
        try:
            set_usage_mode(mode)
        except ValueError:
            logger.warning(f"写入使用模式失败: {value!r}")
            return
        mode_text = _MODE_LABELS.get(mode, mode.value)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("使用场景已切换")
        extra = ""
        if mode == UsageMode.MINIMAL:
            extra = (
                "极简模式仅保留「媒体导入」，其余模块将全部隐藏。\n"
                "请在下方「媒体保存目录」中指定素材复制到本机的位置。\n\n"
            )
        box.setText(
            f"已切换为{mode_text}，重启应用后生效。\n"
            "数据库数据不受影响，可随时切回。\n\n" + extra + "是否立即重启？"
        )
        restart_btn = box.addButton("立即重启", QMessageBox.AcceptRole)
        later_btn = box.addButton("稍后重启", QMessageBox.RejectRole)
        box.setDefaultButton(later_btn)
        box.exec()
        if box.clickedButton() is restart_btn:
            self._restart_app()

    # ===== 极简模式媒体保存目录 =====

    def _sync_minimal_dir_visibility(self):
        """按「界面模式」下拉的当前选择显示/隐藏保存目录设置项。"""
        widget = getattr(self, "minimal_dir_widget", None)
        if widget is None:
            return
        widget.setVisible(
            self.usage_mode_combo.currentData() == UsageMode.MINIMAL.value
        )

    def _refresh_minimal_dir_label(self):
        """刷新保存目录显示（未设置时给出明确提示）。"""
        label = getattr(self, "minimal_dir_label", None)
        if label is None:
            return
        target = get_minimal_import_target_dir()
        if target:
            label.setText(target)
            label.setStyleSheet(
                f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;"
            )
        else:
            label.setText("（未设置，导入时会提示选择）")
            label.setStyleSheet(f"color: {COLOR.WARNING}; font-size: {FONT_SIZE.SM}px;")

    def _change_minimal_import_dir(self):
        """选择并持久化极简模式媒体保存目录（校验可写，失败给出明确提示）。"""
        current = get_minimal_import_target_dir()
        new_dir = pick_directory(
            self,
            "选择极简模式媒体保存目录",
            current or str(Path.home()),
            category="minimal_import",
        )
        if not new_dir:
            return
        try:
            saved = set_minimal_import_target_dir(new_dir)
        except ValueError as exc:
            QMessageBox.warning(self, "无法使用", str(exc))
            return
        self._refresh_minimal_dir_label()
        QMessageBox.information(
            self,
            "已更新",
            "极简模式媒体保存目录已更新：\n"
            f"{saved}\n\n"
            "导入的素材将复制到 <该目录>/<项目名>/ 下。",
        )

    def _clear_minimal_import_dir(self):
        """清除保存目录设置（导入时将重新提示选择）。"""
        if not get_minimal_import_target_dir():
            self._refresh_minimal_dir_label()
            return
        set_minimal_import_target_dir("")
        self._refresh_minimal_dir_label()
        QMessageBox.information(
            self, "已清除", "已清除媒体保存目录，导入时将提示重新选择。"
        )

    def _open_minimal_import_dir(self):
        """在文件管理器中打开保存目录。"""
        target = get_minimal_import_target_dir()
        if not target:
            QMessageBox.information(self, "提示", "尚未设置媒体保存目录。")
            return
        open_in_file_manager(target)

    # ===== 存储位置重设 =====

    def _change_db_dir(self):
        """重设数据库存储目录：关闭连接、移动数据库文件并提示重启。"""
        current = Path(config.effective_db_dir)
        new_dir = pick_directory(
            self, "选择新的数据库存储目录", str(current), category="db"
        )
        if not new_dir:
            return
        new_dir = Path(new_dir)
        # 校验可写
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
            test_file = new_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
        except OSError:
            QMessageBox.warning(self, "提示", "目标目录不可写，请选择其他位置。")
            return
        if new_dir.resolve() == current.resolve():
            QMessageBox.information(self, "提示", "数据库目录未变化。")
            return

        # 关闭连接池后移动数据库文件（含 WAL/SHM）
        try:
            get_db_service().close_all()
        except Exception as e:
            logger.warning(f"关闭数据库连接失败: {e}")
        moved = 0
        for suffix in ("", "-wal", "-shm"):
            src = current / f"{config.db_name}{suffix}"
            if src.exists():
                try:
                    shutil.move(str(src), str(new_dir / src.name))
                    moved += 1
                except OSError as e:
                    logger.warning(f"移动数据库文件失败 {src}: {e}")
        if moved == 0:
            QMessageBox.warning(self, "提示", "未找到数据库文件，无法移动。")
            return

        config.db_dir = new_dir
        save_app_settings(db_dir=str(new_dir))
        self._refresh_states()
        reply = QMessageBox.question(
            self,
            "重启应用",
            "数据库已移动到新位置。\n"
            "为避免数据不一致，需要重启应用后生效。\n"
            "是否立即重启？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._restart_app()

    def _change_report_dir(self):
        """重设报告输出目录（后续生成的报告保存到新位置）。"""
        new_dir = pick_directory(
            self, "选择新的报告输出目录", str(config.report_dir), category="report"
        )
        if not new_dir:
            return
        config.report_dir = Path(new_dir)
        save_app_settings(report_dir=str(config.report_dir))
        config.ensure_dirs()
        self._refresh_states()
        QMessageBox.information(
            self,
            "已更新",
            "报告输出目录已更新，后续生成的报告将保存到新位置。\n"
            "历史报告仍保留在旧目录，可手动迁移。",
        )

    def _change_log_dir(self):
        """重设日志目录（立即生效，旧日志保留在旧目录）。"""
        new_dir = pick_directory(
            self, "选择新的日志存储目录", str(config.log_dir), category="log"
        )
        if not new_dir:
            return
        config.log_dir = Path(new_dir)
        save_app_settings(log_dir=str(config.log_dir))
        config.ensure_dirs()
        logger.set_log_dir(config.log_dir)
        self._refresh_states()
        QMessageBox.information(
            self,
            "已更新",
            "日志目录已更新，新的日志将写入新位置。\n"
            "旧日志仍保留在旧目录，可在「删除日志文件」后手动清理。",
        )

    def _change_thumb_dir(self):
        """重设缩略图缓存目录（立即生效，旧缓存保留待清理）。"""
        new_dir = pick_directory(
            self,
            "选择新的缩略图缓存目录",
            str(self.thumbnail_service.cache_dir),
            category="thumbnail",
        )
        if not new_dir:
            return
        config.thumbnail_cache_dir = Path(new_dir)
        save_app_settings(thumbnail_cache_dir=str(config.thumbnail_cache_dir))
        config.ensure_dirs()
        self.thumbnail_service.set_cache_dir(config.effective_thumbnail_dir)
        self._refresh_states()
        QMessageBox.information(
            self,
            "已更新",
            "缩略图缓存目录已更新，新缩略图将写入新位置。\n"
            "旧缓存可在「清理缩略图缓存」中删除。",
        )

    @staticmethod
    def _restart_app():
        """尝试自动重启应用；失败时提示手动重启。"""
        exe = sys.executable
        args = list(sys.argv)
        if getattr(sys, "frozen", False):
            # 打包态：可执行文件自身即应用入口，直接启动
            exe = sys.argv[0]
            args = []
        started = QProcess.startDetached(exe, args)
        if started:
            QApplication.quit()
        else:
            QMessageBox.information(
                None,
                "请手动重启",
                "未能自动重启应用，请关闭后重新打开。",
            )

    # ===== 临时文件清理 =====

    def _delete_logs(self):
        count, size = log_files_summary()
        if count == 0:
            QMessageBox.information(self, "提示", "当前没有日志文件。")
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"将删除 {count} 个日志文件（{format_size(size)}），是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        removed = delete_log_files()
        self._refresh_states()
        QMessageBox.information(self, "清理完成", f"已删除 {removed} 个日志文件。")

    def _clear_cache(self):
        removed = self.thumbnail_service.clear_cache()
        self._refresh_states()
        QMessageBox.information(self, "清理完成", f"已清理 {removed} 个缩略图缓存文件")

    def _clear_recent(self):
        count = clear_recent_paths("all")
        self._refresh_states()
        QMessageBox.information(self, "清理完成", f"已清空 {count} 条最近路径记录")

    def _export_settings(self):
        default_path = str(Path.home() / "DITWorkstation_settings.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出设置", default_path, "JSON 文件 (*.json)"
        )
        if not path:
            return
        if export_settings(path):
            QMessageBox.information(self, "导出成功", f"设置已导出到：\n{path}")
        else:
            QMessageBox.warning(
                self, "导出失败", "无法写入设置文件，请检查目标目录权限。"
            )

    def _import_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入设置", str(Path.home()), "JSON 文件 (*.json)"
        )
        if not path:
            return
        reply = QMessageBox.question(
            self,
            "确认导入",
            "导入将合并同名配置项，重启应用后生效。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if import_settings(path, merge=True):
            QMessageBox.information(
                self, "导入成功", "设置已导入。请重启应用以使更改生效。"
            )
        else:
            QMessageBox.warning(
                self, "导入失败", "文件不是有效的设置 JSON，或无法保存导入结果。"
            )

    # ===== 存储卡多卡去重 =====

    def _on_skip_processed_cards_toggled(self, checked: bool):
        config.skip_processed_cards = checked
        save_app_settings(skip_processed_cards=checked)

    # ===== 完整性校验调度 =====

    def _on_integrity_enabled(self, checked: bool):
        interval = self.integrity_interval_spin.value() if checked else 0
        config.integrity_check_interval_hours = interval
        save_app_settings(integrity_check_interval_hours=interval)

    def _on_integrity_interval(self, value: int):
        if self.integrity_enable_check.isChecked():
            config.integrity_check_interval_hours = value
            save_app_settings(integrity_check_interval_hours=value)

    def _on_integrity_scope(self, index: int):
        scope = self.integrity_scope_combo.currentData() or "all"
        config.integrity_check_scope = scope
        save_app_settings(integrity_check_scope=scope)

    # ===== 运行参数 =====

    def _on_verify_after_copy_toggled(self, checked: bool):
        config.verify_after_copy = checked
        save_app_settings(verify_after_copy=checked)

    def _on_verify_source_reread_toggled(self, checked: bool):
        config.verify_source_reread = checked
        save_app_settings(verify_source_reread=checked)

    def _on_auto_detect_toggled(self, checked: bool):
        config.auto_detect_volume = checked
        save_app_settings(auto_detect_volume=checked)

    def _on_auto_card_automation_toggled(self, checked: bool):
        config.auto_card_automation_enabled = checked
        save_app_settings(auto_card_automation_enabled=checked)

    def _on_auto_card_import_toggled(self, checked: bool):
        config.auto_card_import = checked
        save_app_settings(auto_card_import=checked)

    def _on_auto_card_backup_toggled(self, checked: bool):
        config.auto_card_backup = checked
        save_app_settings(auto_card_backup=checked)

    def _save_advanced_card_automation(self):
        """保存 SOP 模块勾选与各路径/模板/钩子字段；由编辑完成信号调用。

        全部模块不勾选时存空列表，运行期由兼容开关（自动导入/自动备份）
        推导步骤，保持历史行为。
        """
        steps = [s for s in SOP_STEPS if self._sop_step_checks[s].isChecked()]
        config.auto_card_steps = steps
        config.auto_card_raw_output_dir = self.auto_card_raw_output_field.path().strip()
        config.auto_card_rename_pattern = (
            self.auto_card_rename_combo.currentText().strip()
        )
        config.auto_card_report_path = self.auto_card_report_path_edit.text().strip()
        hook_line = self.post_ingest_hook_combo.lineEdit()
        config.post_ingest_hook_command = (
            hook_line.text().strip() if hook_line is not None else ""
        )
        save_app_settings(
            auto_card_steps=steps,
            auto_card_raw_output_dir=config.auto_card_raw_output_dir,
            auto_card_rename_pattern=config.auto_card_rename_pattern,
            auto_card_report_path=config.auto_card_report_path,
            post_ingest_hook_command=config.post_ingest_hook_command,
        )

    def _on_rename_preset_activated(self, index: int):
        """选择重命名预设：把预设模板填入编辑框并保存。"""
        pattern = self.auto_card_rename_combo.itemData(index)
        if pattern:
            self.auto_card_rename_combo.setEditText(pattern)
        self._save_advanced_card_automation()

    def _on_hook_preset_activated(self, index: int):
        """选择钩子预设：把预设命令填入编辑框并保存。"""
        command = self.post_ingest_hook_combo.itemData(index)
        if command:
            self.post_ingest_hook_combo.setEditText(command)
        self._save_advanced_card_automation()

    def _browse_report_path(self):
        """用系统文件对话框选择自动报告输出 PDF 路径。"""
        current = self.auto_card_report_path_edit.text().strip()
        path = pick_save_file(
            self,
            "选择自动报告输出路径",
            current,
            "PDF 文件 (*.pdf);;所有文件 (*)",
            default_suffix="pdf",
        )
        if not path:
            return
        self.auto_card_report_path_edit.setText(path)
        self._save_advanced_card_automation()

    def _on_auto_card_project_changed(self, index: int):
        value = self.auto_card_project_combo.itemData(index) or ""
        config.auto_card_project_id = value
        save_app_settings(auto_card_project_id=value)

    def _on_auto_card_template_changed(self, index: int):
        value = self.auto_card_template_combo.itemData(index) or ""
        config.auto_card_template_id = value
        save_app_settings(auto_card_template_id=value)
