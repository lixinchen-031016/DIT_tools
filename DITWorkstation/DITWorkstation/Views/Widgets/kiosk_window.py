"""KIOSK 跟机模式窗口：插卡即自动备份，大字状态展示。

面向跟机员/摄影助理的极简界面：
- 全屏大字状态：待机 → 处理中（进度）→ 成功（绿灯）/ 失败（红灯）；
- 插卡后按「设置 → 存储卡」的自动化配置执行同一套 SOP 链
  （复用 CardAutomationService，与主窗口自动化行为一致）；
- 复用主窗口 VolumeMonitor 信号，避免双重检测；
- 完成后展示擦除保护状态（verified 才显示「可安全格式化」）；
- Esc / 关闭按钮退出回到主窗口。
"""

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from DITWorkstation.App import config
from DITWorkstation.Models import BackupStatus
from DITWorkstation.Utils import logger
from DITWorkstation.Utils.workers import WorkerThread
from DITWorkstation.Views.Styles.theme import (
    COLOR,
    DANGER_BUTTON_QSS,
    FONT_SIZE,
    SECONDARY_BUTTON_QSS,
    TYPO,
)


class KioskWindow(QMainWindow):
    """KIOSK 跟机模式主窗口（模态使用：打开期间主窗口让出卡片事件）。"""

    def __init__(self, main_window):
        super().__init__()
        self._main = main_window
        self._worker: WorkerThread | None = None
        self._current_card = ""
        self.setWindowTitle("DIT 工作站 — KIOSK 跟机模式")
        self._build_ui()
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

    # ===== UI =====

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(48, 48, 48, 32)
        layout.setSpacing(24)

        self.state_label = QLabel("等待插入存储卡…")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet(
            f"font-size: {FONT_SIZE.XXL}px; font-weight: 600;"
            f" color: {COLOR.TEXT_PRIMARY};"
        )
        layout.addWidget(self.state_label, stretch=1)

        self.detail_label = QLabel(
            "插卡后将按「设置 → 存储卡」的自动化配置执行备份/导入。\n"
            "完成且显示「可安全格式化」前，请勿拔卡或格式化。"
        )
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(
            f"font-size: {TYPO.SECTION_TITLE}px; color: {COLOR.TEXT_SECONDARY};"
        )
        layout.addWidget(self.detail_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        layout.addWidget(self.progress_bar)

        self.log_list = QListWidget()
        self.log_list.setStyleSheet(
            f"font-size: {TYPO.BODY}px; color: {COLOR.TEXT_SECONDARY};"
        )
        layout.addWidget(self.log_list, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("取消当前任务")
        self.cancel_btn.setStyleSheet(SECONDARY_BUTTON_QSS)
        self.cancel_btn.clicked.connect(self._cancel_current)
        btn_row.addWidget(self.cancel_btn)
        self.exit_btn = QPushButton("退出 KIOSK 模式")
        self.exit_btn.setStyleSheet(DANGER_BUTTON_QSS)
        self.exit_btn.clicked.connect(self.close)
        btn_row.addWidget(self.exit_btn)
        layout.addLayout(btn_row)

        self.setCentralWidget(central)

    # ===== 生命周期 =====

    def showEvent(self, event):
        super().showEvent(event)
        self.showFullScreen()
        main = self._main
        main._kiosk_active = True
        main.volume_monitor.volume_mounted.connect(self._on_volume_mounted)
        self._log("KIOSK 模式已启动，等待插卡")

    def closeEvent(self, event):
        main = self._main
        try:
            main.volume_monitor.volume_mounted.disconnect(self._on_volume_mounted)
        except (RuntimeError, TypeError):
            pass
        main._kiosk_active = False
        main._kiosk_window = None
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
            self._worker = None
        super().closeEvent(event)

    # ===== 卡片事件 =====

    @Slot(str)
    def _on_volume_mounted(self, path: str):
        """主窗口 VolumeMonitor 转发而来的插卡事件。"""
        if self._worker is not None and self._worker.isRunning():
            self._log(f"⚠️ 已有任务执行中，忽略新卡: {Path(path).name}")
            return
        from DITWorkstation.Services.card_identity import (
            card_wipe_warning,
            compute_card_fingerprint,
            is_fingerprint_processed,
        )

        self._current_card = path
        warning = card_wipe_warning(path)
        if warning:
            self._log(f"⚠️ {warning}")
        fingerprint = compute_card_fingerprint(path)
        if fingerprint and is_fingerprint_processed(fingerprint) and getattr(
            config, "skip_processed_cards", True
        ):
            self._set_state("此卡已处理过", COLOR.TEXT_MUTED,
                            "如需重新处理，请在主窗口手动执行导入/备份。")
            return

        project_id = getattr(config, "auto_card_project_id", "")
        template_id = getattr(config, "auto_card_template_id", "")
        steps = getattr(config, "auto_card_steps", None) or None
        do_import = getattr(config, "auto_card_import", True)
        do_backup = getattr(config, "auto_card_backup", False)
        db_service = self._main.backup_view.db_service if self._main.backup_view else None
        if not project_id or db_service is None or (
            (do_backup or (steps and "backup" in steps))
            and not (template_id and db_service.get_backup_template(template_id))
        ):
            self._set_state(
                "自动化配置不完整",
                COLOR.WARNING,
                "请退出后在「设置 → 存储卡」选择项目和备份方案。",
            )
            return

        template = db_service.get_backup_template(template_id) if template_id else None
        service = self._main.card_automation_service
        if service is None:
            self._set_state("自动化不可用", COLOR.WARNING,
                            "当前功能模式未启用相机卡自动化。")
            return

        self._set_state("正在处理存储卡…", COLOR.WARNING,
                        f"来源: {Path(path).name or path}")
        self._log(f"开始自动处理: {path}")
        self._worker = WorkerThread(
            service.execute,
            path,
            project_id,
            template=template,
            do_import=do_import,
            do_backup=do_backup,
            steps=steps,
            raw_config={"output_folder": getattr(config, "auto_card_raw_output_dir", "")}
            if steps and "raw_extract" in steps
            else None,
            rename_config={"rule": {"pattern": config.auto_card_rename_pattern}}
            if steps and "rename" in steps
            and getattr(config, "auto_card_rename_pattern", "")
            else None,
            report_path=getattr(config, "auto_card_report_path", "") or None,
            inject_progress=True,
            inject_cancel_check=True,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    @Slot(str, float, str)
    def _on_progress(self, target: str, progress: float, message: str):
        self.progress_bar.setValue(int(progress * 1000))
        self.detail_label.setText(f"[{target}] {message} ({int(progress * 100)}%)")

    def _on_finished(self, result):
        self._worker = None
        self.progress_bar.setValue(1000)
        from DITWorkstation.Services.card_identity import (
            card_state_note_from_automation_result,
            compute_card_fingerprint,
            mark_fingerprint_processed,
        )

        fingerprint = (
            compute_card_fingerprint(self._current_card) if self._current_card else ""
        )
        if fingerprint:
            mark_fingerprint_processed(fingerprint)
        note = card_state_note_from_automation_result(result, fingerprint)
        backup = result.get("backup") if isinstance(result, dict) else None
        if note.startswith("✅"):
            self._set_state("✅ 备份完成，可安全格式化", COLOR.SUCCESS, note)
        elif note:
            self._set_state("⚠️ 未完成验证，请勿拔卡/擦除", COLOR.DANGER, note)
        elif isinstance(result, dict) and result.get("cancelled"):
            self._set_state("已取消", COLOR.TEXT_MUTED, "任务被取消，卡未做任何标记。")
        else:
            imported = (result or {}).get("import") or {}
            self._set_state(
                "处理完成",
                COLOR.SUCCESS,
                f"导入 {imported.get('imported', 0)} 个素材（未配置校验备份）",
            )
        if backup is not None:
            self._log(
                f"备份状态: {backup.status.value}"
                + ("" if backup.status == BackupStatus.COMPLETED else "（存在失败目标）")
            )
        self._log(f"卡片处理结束: {self._current_card}")

    def _on_error(self, error: str):
        self._worker = None
        self.progress_bar.setValue(0)
        self._set_state("❌ 处理失败", COLOR.DANGER,
                        "素材可能未完成备份，请勿格式化此卡；详情见日志。")
        self._log(f"错误: {error}")
        logger.error(f"KIOSK 自动任务失败: {error}")

    # ===== 辅助 =====

    def _cancel_current(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._log("用户取消当前任务")
            self.cancel_btn.setEnabled(False)

    def _set_state(self, text: str, color, detail: str = ""):
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            f"font-size: {FONT_SIZE.XXL}px; font-weight: 600; color: {color};"
        )
        if detail:
            self.detail_label.setText(detail)
        self.cancel_btn.setEnabled(self._worker is not None)

    def _log(self, message: str):
        self.log_list.addItem(message)
        self.log_list.scrollToBottom()
