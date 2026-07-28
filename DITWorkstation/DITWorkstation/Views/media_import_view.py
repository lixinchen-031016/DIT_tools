"""媒体导入页面"""
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QGroupBox,
    QTextEdit, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QSplitter, QComboBox
)
from PySide6.QtCore import Qt, Slot

from DITWorkstation.Models import Project
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Utils import format_size, generate_log_message, WorkerThread, get_db_service, pick_directory, find_overwrite_conflicts
from DITWorkstation.Views.Widgets import WorkspaceProjectSelector, RefreshOnShowView
from DITWorkstation.Views.Widgets.empty_state import attach_empty_state, sync_empty_state
from DITWorkstation.Views.Styles.theme import MONO_FONT_QSS


class MediaImportView(RefreshOnShowView):
    """媒体导入视图"""

    def __init__(self):
        super().__init__()
        # 注入共享 db_service 单例，避免与其它视图各自新建 DatabaseService
        self.db_service = get_db_service()
        self.import_service = MediaImportService(db_service=self.db_service)
        self.current_project: Optional[Project] = None
        self.pending_files = []
        self.worker = None
        self._cancel_requested = False
        self._setup_ui()
        # 监听选择控件的 项目切换 信号，做本视图业务联动
        # （工作区/项目下拉与全局信号同步已由 WorkspaceProjectSelector 内部处理）
        self.selector.project_changed.connect(self._on_project_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("媒体导入")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")
        layout.addWidget(title)

        subtitle = QLabel("将图片、视频、RAW文件导入项目，原文件位置保持不动")
        subtitle.setStyleSheet("font-size: 13px; color: #86868b;")
        layout.addWidget(subtitle)

        main_splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
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

        main_splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

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

        right_layout.addWidget(source_group)

        files_group = QGroupBox("待导入文件")
        files_layout = QVBoxLayout(files_group)

        self.files_table = QTableWidget()
        self.files_table.setColumnCount(4)
        self.files_table.setHorizontalHeaderLabels(["文件名", "类型", "大小", "路径"])
        self.files_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.files_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.verticalHeader().setDefaultSectionSize(32)
        self.files_table.setSortingEnabled(True)
        # 双击打开所在目录
        self.files_table.doubleClicked.connect(self._on_file_double_clicked)
        # 右键菜单：打开目录 / 复制路径
        self.files_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files_table.customContextMenuRequested.connect(self._on_files_context_menu)
        files_layout.addWidget(self.files_table, 1)
        attach_empty_state(self.files_table, "📁", "暂无待导入文件", "点击上方「浏览…」选择存储卡目录")

        self.files_label = QLabel("")
        self.files_label.setStyleSheet("color: #86868b; font-size: 12px;")
        files_layout.addWidget(self.files_label)

        right_layout.addWidget(files_group, 1)

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
        self.copy_dest_label.setStyleSheet("color: #86868b; font-size: 12px;")
        self.copy_mode_check.toggled.connect(self._on_copy_check_toggled)
        opt_row1.addWidget(self.copy_dest_label, 1)

        options_layout.addLayout(opt_row1)

        opt_row2 = QHBoxLayout()
        opt_row2.addWidget(QLabel("关联拍摄日志:"))
        self.log_combo = QComboBox()
        self.log_combo.addItem("不关联", None)
        self.log_combo.setEnabled(False)
        self.log_combo.setMinimumWidth(200)
        opt_row2.addWidget(self.log_combo)
        opt_row2.addStretch()
        options_layout.addLayout(opt_row2)

        right_layout.addWidget(options_group)

        action_row = QHBoxLayout()
        self.import_btn = QPushButton("📥 开始导入")
        self.import_btn.setToolTip("开始导入选中的文件到当前项目")
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #0a84ff;
                color: white;
                padding: 10px 32px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0070e0; }
            QPushButton:disabled { background-color: #c7c7cc; }
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
        right_layout.addLayout(action_row)

        # 执行状态（合并进度条 + 状态标签 + 日志输出）
        status_group = QGroupBox("执行状态")
        status_layout = QVBoxLayout(status_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        status_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #86868b; font-size: 12px;")
        status_layout.addWidget(self.status_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(60)
        self.log_text.setStyleSheet(MONO_FONT_QSS)
        status_layout.addWidget(self.log_text)
        right_layout.addWidget(status_group)

        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 4)

        layout.addWidget(main_splitter, 1)

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
            self.import_btn.setEnabled(len(self.pending_files) > 0)
            self._update_copy_dest_label()

    def _get_current_workspace(self):
        """返回当前选中的工作区对象（委托给选择控件）"""
        return self.selector.get_current_workspace()

    def _sync_copy_check_state(self):
        """根据当前工作区是否有 path，启用/禁用「复制到工作区」复选框。

        - 未选具体工作区：禁用（无目标目录）
        - 工作区 path 为空：禁用并提示用户去编辑工作区补充目录
        - 工作区 path 非空：启用
        """
        if not hasattr(self, 'copy_mode_check'):
            return
        ws = self._get_current_workspace()
        if ws is None:
            self.copy_mode_check.setEnabled(False)
            self.copy_mode_check.setChecked(False)
            self.copy_mode_check.setToolTip("请先选择具体工作区")
        elif not ws.path:
            self.copy_mode_check.setEnabled(False)
            self.copy_mode_check.setChecked(False)
            self.copy_mode_check.setToolTip(
                "当前工作区未设置目录，请到「项目概览」看板编辑工作区补充目录"
            )
        else:
            self.copy_mode_check.setEnabled(True)
            self.copy_mode_check.setToolTip(
                f"勾选后将素材复制到：{ws.path}/<项目名>/ 下"
            )

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
        path = pick_directory(self, "选择要导入的文件夹")
        if path:
            self.source_edit.setText(path)

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
            self.copy_dest_label.setText("（未选择有效工作区或工作区无目录）")
            self.copy_dest_label.setStyleSheet("color: #ff3b30; font-size: 12px;")
            return
        if self.current_project:
            dest = str(Path(ws.path) / self.current_project.name)
        else:
            dest = str(Path(ws.path) / "<项目名>")
        self.copy_dest_label.setText(f"→ {dest}")
        self.copy_dest_label.setStyleSheet("color: #86868b; font-size: 12px;")

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
            self.pending_files = [str(f) for f in files]
            self._display_files(files)
            self.import_btn.setEnabled(len(files) > 0 and self.current_project is not None)
            total_size = sum(f.stat().st_size for f in files if f.exists())
            self._log(f"扫描完成: 发现 {len(files)} 个媒体文件, 总大小 {format_size(total_size)}")
        except Exception as e:
            QMessageBox.critical(self, "扫描错误", str(e))
            self._log(f"扫描失败: {e}")

    def _display_files(self, files):
        self.files_table.setRowCount(len(files))
        sync_empty_state(self.files_table)
        for i, f in enumerate(files):
            stat = f.stat()
            self.files_table.setItem(i, 0, QTableWidgetItem(f.name))
            asset_type = self.import_service.classify_media_type(str(f))
            type_map = {
                "image": "🖼️ 图片",
                "video": "🎬 视频",
                "raw": "📷 RAW",
                "audio": "🎵 音频",
                "other": "📄 其他"
            }
            self.files_table.setItem(i, 1, QTableWidgetItem(type_map.get(asset_type.value, asset_type.value)))
            self.files_table.setItem(i, 2, QTableWidgetItem(format_size(stat.st_size)))
            self.files_table.setItem(i, 3, QTableWidgetItem(str(f.parent)))

        self.files_label.setText(f"共 {len(files)} 个文件")

    def _on_file_double_clicked(self, index):
        """双击待导入文件 → 打开所在目录（通过表格单元格文本定位，避免排序后行号错位）"""
        if not index.isValid():
            return
        row = index.row()
        name_item = self.files_table.item(row, 0)
        parent_item = self.files_table.item(row, 3)
        if name_item is None or parent_item is None:
            return
        from pathlib import Path as _Path
        path = str(_Path(parent_item.text()) / name_item.text())
        from DITWorkstation.Utils import open_in_file_manager
        open_in_file_manager(path)

    def _on_files_context_menu(self, pos):
        """右键菜单：打开所在目录 / 复制路径"""
        from PySide6.QtWidgets import QMenu, QApplication
        from pathlib import Path as _Path
        index = self.files_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        name_item = self.files_table.item(row, 0)
        parent_item = self.files_table.item(row, 3)
        if name_item is None or parent_item is None:
            return
        path = str(_Path(parent_item.text()) / name_item.text())
        menu = QMenu(self)
        action_open = menu.addAction("打开所在目录")
        action_copy = menu.addAction("复制文件路径")
        chosen = menu.exec(self.files_table.viewport().mapToGlobal(pos))
        if chosen is action_open:
            from DITWorkstation.Utils import open_in_file_manager
            open_in_file_manager(path)
        elif chosen is action_copy:
            QApplication.clipboard().setText(path)

    def _start_import(self):
        if not self.current_project:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return

        if not self.pending_files:
            QMessageBox.warning(self, "提示", "没有可导入的文件")
            return

        copy_to_workspace = self.copy_mode_check.isChecked()
        workspace_dir = None
        if copy_to_workspace:
            # 复制目录自动基于当前工作区.path / 项目名
            ws = self._get_current_workspace()
            if ws is None or not ws.path:
                QMessageBox.warning(
                    self, "提示",
                    "当前工作区未设置目录，请到「项目概览」看板编辑工作区补充目录后再勾选复制模式"
                )
                return
            workspace_dir = str(Path(ws.path) / self.current_project.name)

            # 覆盖确认：检查工作区目标目录中是否已存在同名文件
            try:
                source_names = [p.name for p in self.pending_files]
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

        log_id = self.log_combo.currentData()
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
            self.pending_files,
            compute_checksum=self.checksum_check.isChecked(),
            copy_to_workspace=copy_to_workspace,
            workspace_dir=workspace_dir,
            log_id=log_id,
            scene=scene,
            shot=shot,
            cancel_check=lambda: self._cancel_requested
        )
        self.worker.finished.connect(self._on_import_finished)
        self.worker.error.connect(self._on_import_error)
        self.worker.progress.connect(self._on_progress)
        # 线程结束后自动释放，避免 QThread 对象泄漏
        self.worker.finished.connect(self.worker.deleteLater)
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
            from DITWorkstation.App.session_context import get_data_bus
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
                if hasattr(main_window, 'nav_list'):
                    from DITWorkstation.Views.main_window import get_nav_index
                    main_window.nav_list.setCurrentRow(get_nav_index("backup"))
            except Exception as e:
                from DITWorkstation.Utils import logger
                logger.warning(f"跳转备份视图失败: {e}")

    @Slot(str)
    def _on_import_error(self, error: str):
        self.import_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(f"❌ 错误: {error}")
        self._log(f"导入错误: {error}")
        self.worker = None
        QMessageBox.critical(self, "导入错误", error)

    def _log(self, message: str):
        self.log_text.append(generate_log_message(message))
