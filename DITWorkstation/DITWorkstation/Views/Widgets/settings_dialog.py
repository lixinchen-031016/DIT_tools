"""设置对话框：数据存储位置重设 / 临时文件清理 / 最近路径管理 / 运行参数"""
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QCheckBox, QMessageBox, QApplication,
)
from PySide6.QtCore import Qt, QProcess

from DITWorkstation.App import config
from DITWorkstation.Services.thumbnail_service import ThumbnailService
from DITWorkstation.Utils import (
    format_size, clear_recent_paths, count_recent_paths, open_in_file_manager,
    save_app_settings, pick_directory, get_db_service, logger,
    log_files_summary, delete_log_files,
)
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, RADIUS, TITLE_QSS, SUBTITLE_QSS


class SettingsDialog(QDialog):
    """应用设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(640, 640)
        self.thumbnail_service = ThumbnailService()
        self._setup_ui()
        self._refresh_states()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("设置")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel("数据存储位置、临时文件清理与运行参数")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        # ===== 数据存储位置 =====
        dir_group = QGroupBox("📂 数据存储位置")
        dir_form = QFormLayout(dir_group)
        dir_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.db_dir_label = QLabel("")
        self._style_path_label(self.db_dir_label)
        dir_form.addRow("数据库目录:", self._make_dir_row(
            self.db_dir_label, self._change_db_dir,
            lambda: open_in_file_manager(str(config.effective_db_dir)),
        ))

        self.report_dir_label = QLabel("")
        self._style_path_label(self.report_dir_label)
        dir_form.addRow("报告输出:", self._make_dir_row(
            self.report_dir_label, self._change_report_dir,
            lambda: open_in_file_manager(str(config.report_dir)),
        ))

        self.log_dir_label = QLabel("")
        self._style_path_label(self.log_dir_label)
        dir_form.addRow("日志目录:", self._make_dir_row(
            self.log_dir_label, self._change_log_dir,
            lambda: open_in_file_manager(str(config.log_dir)),
        ))

        self.thumb_dir_label = QLabel("")
        self._style_path_label(self.thumb_dir_label)
        dir_form.addRow("缩略图缓存:", self._make_dir_row(
            self.thumb_dir_label, self._change_thumb_dir,
            lambda: open_in_file_manager(str(self.thumbnail_service.cache_dir)),
        ))
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
        self.verify_after_copy_check.toggled.connect(
            self._on_verify_after_copy_toggled
        )
        backup_layout.addWidget(self.verify_after_copy_check)
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
        layout.addWidget(volume_group)

        layout.addStretch()

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
        self.auto_detect_check.setChecked(config.auto_detect_volume)

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
            self, "重启应用",
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
            self, "已更新",
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
            self, "已更新",
            "日志目录已更新，新的日志将写入新位置。\n"
            "旧日志仍保留在旧目录，可在「删除日志文件」后手动清理。",
        )

    def _change_thumb_dir(self):
        """重设缩略图缓存目录（立即生效，旧缓存保留待清理）。"""
        new_dir = pick_directory(
            self, "选择新的缩略图缓存目录",
            str(self.thumbnail_service.cache_dir), category="thumbnail",
        )
        if not new_dir:
            return
        config.thumbnail_cache_dir = Path(new_dir)
        save_app_settings(thumbnail_cache_dir=str(config.thumbnail_cache_dir))
        config.ensure_dirs()
        self.thumbnail_service.set_cache_dir(config.effective_thumbnail_dir)
        self._refresh_states()
        QMessageBox.information(
            self, "已更新",
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
            exe = sys.executable
            args = []
        started = QProcess.startDetached(exe, args)
        if started:
            QApplication.quit()
        else:
            QMessageBox.information(
                None, "请手动重启",
                "未能自动重启应用，请关闭后重新打开。",
            )

    # ===== 临时文件清理 =====

    def _delete_logs(self):
        count, size = log_files_summary()
        if count == 0:
            QMessageBox.information(self, "提示", "当前没有日志文件。")
            return
        reply = QMessageBox.question(
            self, "确认删除",
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

    # ===== 运行参数 =====

    def _on_verify_after_copy_toggled(self, checked: bool):
        config.verify_after_copy = checked
        save_app_settings(verify_after_copy=checked)

    def _on_auto_detect_toggled(self, checked: bool):
        config.auto_detect_volume = checked
        save_app_settings(auto_detect_volume=checked)
