"""报告生成页面"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QComboBox, QMessageBox,
    QTextEdit, QLineEdit, QFormLayout
)
from PySide6.QtCore import Slot

from DITWorkstation.Services.report_service import ReportService
from DITWorkstation.Utils import get_db_service, safe_slot, pick_save_file
from DITWorkstation.Utils.workers import SimpleWorkerThread
from DITWorkstation.Views.Widgets import RefreshOnShowView
from DITWorkstation.Views.Styles.theme import MONO_FONT_QSS


class ReportView(RefreshOnShowView):
    """报告生成视图"""

    def __init__(self):
        super().__init__()
        self.db_service = get_db_service()
        self.report_service = ReportService()
        self.worker = None
        self._setup_ui()
        # 项目切换由共享控件广播，本视图无需额外处理（生成时实时读取选中项目）

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("报告生成")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        layout.addWidget(title)

        subtitle = QLabel("生成数据管理报告、素材统计报告等专业PDF报告")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
        layout.addWidget(subtitle)

        # 报告配置
        config_group = QGroupBox("报告配置")
        config_layout = QFormLayout(config_group)

        # 共享控件：工作区 + 项目两级选择
        from DITWorkstation.Views.Widgets import WorkspaceProjectSelector
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_new_project=True,
            none_label="（暂无项目，请先在拍摄日志中创建）",
            db_service=self.db_service,
        )
        config_layout.addRow("选择项目:", self.selector)

        self.report_type_combo = QComboBox()
        self.report_type_combo.addItems([
            "数据备份报告",
            "素材统计报告",
        ])
        config_layout.addRow("报告类型:", self.report_type_combo)

        # 输出路径（用 QLineEdit 避免多行输入混入换行符）
        out_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("默认输出到 ~/Documents/DIT_Reports/")
        out_btn = QPushButton("浏览…")
        out_btn.clicked.connect(self._select_output)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(out_btn)
        config_layout.addRow("输出路径:", out_row)

        layout.addWidget(config_group)

        # 生成按钮
        btn_layout = QHBoxLayout()
        self.generate_btn = QPushButton("📊 生成报告")
        self.generate_btn.setToolTip("生成 PDF 报告")
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #0a84ff;
                color: white;
                padding: 12px 28px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0070e0; }
            QPushButton:disabled { background-color: #c7c7cc; }
        """)
        self.generate_btn.clicked.connect(self._generate_report)
        btn_layout.addWidget(self.generate_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 状态
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #86868b; font-size: 13px;")
        layout.addWidget(self.status_label)

        # 报告预览/日志
        log_group = QGroupBox("生成日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(MONO_FONT_QSS)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group, 1)

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self.selector.refresh()

    def _select_output(self):
        path = pick_save_file(
            self, "选择报告输出路径", "", "PDF文件 (*.pdf)", default_suffix="pdf"
        )
        if path:
            self.output_edit.setText(path)

    @safe_slot("生成报告失败")
    def _generate_report(self):
        project_id = self.selector.get_current_project_id()
        if not project_id:
            QMessageBox.warning(self, "提示", "请先创建项目")
            return

        project = self.db_service.get_project(project_id)
        output_path = self.output_edit.text().strip() or None
        report_type = self.report_type_combo.currentIndex()

        # 备份报告功能尚未对接历史记录，提前告知用户
        if report_type == 0:
            QMessageBox.information(
                self, "提示",
                "数据备份报告当前不会包含具体备份记录（备份历史功能尚未对接），\n"
                "生成的报告仅含项目基本信息。如需完整素材统计，请选择「素材统计报告」。"
            )

        self.generate_btn.setEnabled(False)
        self.status_label.setText("正在生成报告...")
        self._log(f"开始生成报告: {project.name}")

        if report_type == 0:
            # 备份报告（使用空jobs列表，实际可从历史记录获取）
            self.worker = SimpleWorkerThread(
                self.report_service.generate_backup_report,
                project, [], output_path
            )
        else:
            # 素材统计报告
            assets = self.db_service.get_media_assets(project_id)
            logs = self.db_service.get_shooting_logs(project_id)
            self.worker = SimpleWorkerThread(
                self.report_service.generate_asset_report,
                project, assets, logs, output_path
            )

        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        # 线程结束后自动释放，避免 QThread 对象泄漏
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    @Slot(object)
    def _on_finished(self, result):
        self.generate_btn.setEnabled(True)
        self.status_label.setText(f"✅ 报告已生成: {result}")
        self._log(f"报告生成成功: {result}")
        # worker 已连 deleteLater，这里清空引用避免悬挂
        self.worker = None
        QMessageBox.information(self, "完成", f"报告已生成:\n{result}")

    @Slot(str)
    def _on_error(self, error: str):
        self.generate_btn.setEnabled(True)
        self.status_label.setText(f"❌ 生成失败: {error}")
        self._log(f"错误: {error}")
        self.worker = None
        QMessageBox.critical(self, "报告生成失败", error)

    def _log(self, message: str):
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
