"""设置对话框：缩略图缓存清理 / 最近路径管理 / 数据目录信息 / 备份默认选项"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QCheckBox, QMessageBox,
)
from PySide6.QtCore import Qt

from DITWorkstation.App import config
from DITWorkstation.Services.thumbnail_service import ThumbnailService
from DITWorkstation.Utils import (
    format_size, clear_recent_paths, count_recent_paths, open_in_file_manager,
)
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS, TITLE_QSS, SUBTITLE_QSS


class SettingsDialog(QDialog):
    """应用设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(560, 480)
        self.thumbnail_service = ThumbnailService()
        self._setup_ui()
        self._refresh_states()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("设置")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel("维护缓存、最近路径与数据目录等运行参数")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # 缩略图缓存
        cache_group = QGroupBox("🖼 缩略图缓存")
        cache_layout = QVBoxLayout(cache_group)
        self.cache_info_label = QLabel("")
        self.cache_info_label.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY};")
        cache_layout.addWidget(self.cache_info_label)
        cache_btn_row = QHBoxLayout()
        self.clear_cache_btn = QPushButton("🗑 清理缩略图缓存")
        self.clear_cache_btn.setToolTip("删除已生成的缩略图文件，下次查看时重新生成")
        self.clear_cache_btn.clicked.connect(self._clear_cache)
        cache_btn_row.addWidget(self.clear_cache_btn)
        cache_btn_row.addStretch()
        cache_layout.addLayout(cache_btn_row)
        layout.addWidget(cache_group)

        # 最近路径
        recent_group = QGroupBox("📁 最近路径")
        recent_layout = QVBoxLayout(recent_group)
        self.recent_info_label = QLabel("")
        self.recent_info_label.setStyleSheet(f"color: {COLOR.TEXT_PRIMARY};")
        recent_layout.addWidget(self.recent_info_label)
        recent_btn_row = QHBoxLayout()
        self.clear_recent_btn = QPushButton("🗑 清空最近路径记录")
        self.clear_recent_btn.setToolTip("清空所有「选择目录」对话框的最近使用记录")
        self.clear_recent_btn.clicked.connect(self._clear_recent)
        recent_btn_row.addWidget(self.clear_recent_btn)
        recent_btn_row.addStretch()
        recent_layout.addLayout(recent_btn_row)
        layout.addWidget(recent_group)

        # 备份默认选项
        backup_group = QGroupBox("📦 备份默认选项")
        backup_layout = QVBoxLayout(backup_group)
        self.verify_after_copy_check = QCheckBox("拷贝后默认验证完整性")
        self.verify_after_copy_check.setToolTip(
            "备份时「拷贝后验证完整性」的默认状态（可在备份视图临时调整）"
        )
        self.verify_after_copy_check.toggled.connect(
            lambda checked: setattr(config, "verify_after_copy", checked)
        )
        backup_layout.addWidget(self.verify_after_copy_check)
        layout.addWidget(backup_group)

        # 数据目录
        dir_group = QGroupBox("💾 数据目录")
        dir_form = QFormLayout(dir_group)
        self.db_dir_label = QLabel(str(config.effective_db_dir))
        self.db_dir_label.setTextInteractionFlags(
            self.db_dir_label.textInteractionFlags() | Qt.TextSelectableByMouse
        )
        db_dir_row = QHBoxLayout()
        db_dir_row.addWidget(self.db_dir_label, 1)
        open_db_btn = QPushButton("打开")
        open_db_btn.clicked.connect(
            lambda: open_in_file_manager(str(config.effective_db_dir))
        )
        db_dir_row.addWidget(open_db_btn)
        dir_form.addRow("数据库:", db_dir_row)

        report_row = QHBoxLayout()
        report_label = QLabel(str(config.report_dir))
        report_label.setTextInteractionFlags(
            report_label.textInteractionFlags() | Qt.TextSelectableByMouse
        )
        report_row.addWidget(report_label, 1)
        open_report_btn = QPushButton("打开")
        open_report_btn.clicked.connect(
            lambda: open_in_file_manager(str(config.report_dir))
        )
        report_row.addWidget(open_report_btn)
        dir_form.addRow("报告输出:", report_row)
        layout.addWidget(dir_group)

        # 关闭
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # 按钮统一样式
        for btn in (self.clear_cache_btn, self.clear_recent_btn):
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR.BORDER_LIGHT};
                    border: 1px solid {COLOR.BORDER};
                    border-radius: {RADIUS.INPUT}px;
                    padding: 6px 14px;
                    font-size: {FONT_SIZE.SM}px;
                    color: {COLOR.TEXT_PRIMARY};
                }}
                QPushButton:hover {{ background-color: {COLOR.BORDER}; }}
            """)

    def _refresh_states(self):
        """刷新各项状态显示。"""
        self.cache_info_label.setText(
            f"缓存目录：{self.thumbnail_service.cache_dir}\n"
            f"当前占用：{format_size(self.thumbnail_service.cache_size())}"
        )
        recent_total = count_recent_paths()
        self.recent_info_label.setText(f"共 {recent_total} 条最近使用记录")
        self.verify_after_copy_check.setChecked(config.verify_after_copy)

    def _clear_cache(self):
        removed = self.thumbnail_service.clear_cache()
        self._refresh_states()
        QMessageBox.information(self, "清理完成", f"已清理 {removed} 个缩略图缓存文件")

    def _clear_recent(self):
        count = clear_recent_paths("all")
        self._refresh_states()
        QMessageBox.information(self, "清理完成", f"已清空 {count} 条最近路径记录")
