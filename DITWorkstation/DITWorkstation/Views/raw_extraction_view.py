"""JPG筛选后RAW提取页面"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QGroupBox,
    QFormLayout, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QSplitter
)
from PySide6.QtCore import Qt, Slot, Signal

from DITWorkstation.Services.raw_extraction_service import RawExtractionService
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Utils.workers import SimpleWorkerThread
from DITWorkstation.Utils import get_db_service, logger, pick_directory, find_overwrite_conflicts
from DITWorkstation.App.session_context import get_data_bus
from DITWorkstation.Views.Widgets import RefreshOnShowView, WorkspaceProjectSelector
from DITWorkstation.Views.Widgets.empty_state import attach_empty_state, sync_empty_state
from DITWorkstation.Views.Widgets.status_panel import StatusPanel
from DITWorkstation.Views.Widgets.table_factory import make_table
from DITWorkstation.Views.Widgets.error_dialog import show_error
from DITWorkstation.Views.Styles.theme import COLOR, FONT_SIZE, TITLE_QSS, SUBTITLE_QSS, PRIMARY_BUTTON_QSS


class RawExtractionView(RefreshOnShowView):
    """RAW提取视图"""

    # 跨线程进度信号（current, total, message），在工作线程发射、主线程消费
    _progress_sig = Signal(int, int, str)

    def __init__(self):
        super().__init__()
        self.service = RawExtractionService()
        self.db_service = get_db_service()
        # 复用共享 db_service 单例，避免再创建一份
        self.import_service = MediaImportService(db_service=self.db_service)
        self.worker = None
        self._setup_ui()
        self._progress_sig.connect(self._on_progress)
        # 项目切换由共享控件处理（broadcast_none=False 保留"不关联"语义）

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题
        title = QLabel("JPG筛选后提取RAW")
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        subtitle = QLabel("选择客户筛选后的JPG文件夹，自动匹配并提取对应的RAW文件")
        subtitle.setStyleSheet(SUBTITLE_QSS)
        layout.addWidget(subtitle)

        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setChildrenCollapsible(False)
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(16)

        # 关联项目 + 自动入库
        link_group = QGroupBox("项目关联（用于提取后自动入库）")
        link_layout = QHBoxLayout(link_group)
        link_layout.addWidget(QLabel("目标项目:"))
        # 共享控件：broadcast_none=False 保留"不关联"语义
        self.selector = WorkspaceProjectSelector(
            project_widget="combo",
            show_new_project=True,
            none_label="（不关联项目，仅提取文件）",
            broadcast_none=False,
            db_service=self.db_service,
        )
        link_layout.addWidget(self.selector, 1)
        self.auto_import_check = QCheckBox("提取后自动入库到所选项目（继承原 JPG 的 log_id）")
        self.auto_import_check.setChecked(True)
        link_layout.addWidget(self.auto_import_check)
        link_layout.addStretch()
        config_layout.addWidget(link_group)

        # 路径配置
        path_group = QGroupBox("路径配置")
        path_layout = QFormLayout(path_group)

        # JPG文件夹
        jpg_row = QHBoxLayout()
        self.jpg_edit = QLineEdit()
        self.jpg_edit.setPlaceholderText("选择筛选后的JPG文件夹...")
        self.jpg_edit.setReadOnly(True)
        jpg_btn = QPushButton("浏览…")
        jpg_btn.clicked.connect(lambda: self._select_folder(self.jpg_edit, "选择JPG文件夹", category="raw_jpg"))
        jpg_row.addWidget(self.jpg_edit, 1)
        jpg_row.addWidget(jpg_btn)
        path_layout.addRow("JPG文件夹:", jpg_row)

        # RAW源文件夹
        raw_row = QHBoxLayout()
        self.raw_edit = QLineEdit()
        self.raw_edit.setPlaceholderText("选择RAW源文件夹...")
        self.raw_edit.setReadOnly(True)
        self.raw_edit.setToolTip(
            "RAW 文件是相机传感器直接输出的未压缩数据，保留最大动态范围和后期空间。"
            "常见格式：CR2/CR3（佳能）、NEF（尼康）、ARW（索尼）。"
        )
        raw_btn = QPushButton("浏览…")
        raw_btn.clicked.connect(lambda: self._select_folder(self.raw_edit, "选择RAW源文件夹", category="raw_raw_dir"))
        raw_row.addWidget(self.raw_edit, 1)
        raw_row.addWidget(raw_btn)
        path_layout.addRow("RAW源文件夹:", raw_row)

        # 输出文件夹
        out_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择RAW输出文件夹...")
        self.output_edit.setReadOnly(True)
        out_btn = QPushButton("浏览…")
        out_btn.clicked.connect(lambda: self._select_folder(self.output_edit, "选择输出文件夹", category="raw_output"))
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(out_btn)
        path_layout.addRow("输出文件夹:", out_row)

        config_layout.addWidget(path_group)

        # 选项
        opt_group = QGroupBox("选项")
        opt_layout = QHBoxLayout(opt_group)
        self.verify_check = QCheckBox("提取后验证完整性")
        self.verify_check.setChecked(True)
        opt_layout.addWidget(self.verify_check)
        opt_layout.addStretch()
        config_layout.addWidget(opt_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.scan_btn = QPushButton("扫描匹配")
        self.scan_btn.setToolTip("扫描 JPG 目录")
        self.scan_btn.clicked.connect(self._scan_match)
        self.extract_btn = QPushButton("开始提取")
        self.extract_btn.setToolTip("从选中的 JPG 提取对应的 RAW 文件")
        self.extract_btn.setStyleSheet(PRIMARY_BUTTON_QSS)
        self.extract_btn.clicked.connect(self._start_extraction)
        self.extract_btn.setEnabled(False)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setToolTip("取消正在进行的提取任务")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_extraction)

        btn_layout.addWidget(self.scan_btn)
        btn_layout.addWidget(self.extract_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        config_layout.addLayout(btn_layout)

        config_layout.addStretch()
        main_splitter.addWidget(config_widget)

        # 匹配结果表格
        result_group = QGroupBox("匹配结果")
        result_layout = QVBoxLayout(result_group)

        self.result_table = make_table(["JPG文件", "匹配RAW", "状态"])
        self.result_table.setMinimumHeight(120)
        result_layout.addWidget(self.result_table)
        attach_empty_state(self.result_table, "🔍", "暂无匹配结果", "选择 JPG 和 RAW 目录后点击「扫描匹配」")

        self.match_label = QLabel("")
        self.match_label.setStyleSheet(f"color: {COLOR.TEXT_SECONDARY}; font-size: {FONT_SIZE.SM}px;")
        result_layout.addWidget(self.match_label)

        # 下段容器
        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)
        result_layout.addWidget(result_group)

        # 进度
        self.status_panel = StatusPanel(show_log=False)
        result_layout.addWidget(self.status_panel)
        # 保留别名以减少方法体内 self.progress_bar / status_label 的改动
        self.progress_bar = self.status_panel.progress_bar
        self.status_label = self.status_panel.status_label

        main_splitter.addWidget(result_widget)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 3)
        main_splitter.setMinimumHeight(400)
        layout.addWidget(main_splitter, 1)

    def _select_folder(self, edit: QLineEdit, title: str, category: str = "default"):
        path = pick_directory(self, title, category=category)
        if path:
            edit.setText(path)

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑"""
        self.selector.refresh()

    def _scan_match(self):
        jpg_folder = self.jpg_edit.text()
        raw_folder = self.raw_edit.text()

        if not jpg_folder or not raw_folder:
            QMessageBox.warning(self, "提示", "请先选择JPG文件夹和RAW源文件夹")
            return

        try:
            jpg_files = self.service.scan_jpg_folder(jpg_folder)
            raw_index = self.service.scan_raw_folder(raw_folder)
            matches = self.service.match_raw_files(jpg_files, raw_index)
            # 缓存最新扫描结果，供 _start_extraction 做覆盖冲突检测
            self._last_matches = matches

            # 更新表格
            self.result_table.setRowCount(len(matches))
            sync_empty_state(self.result_table)
            matched_count = 0
            for i, (jpg, raw) in enumerate(matches):
                self.result_table.setItem(i, 0, QTableWidgetItem(jpg.name))
                if raw:
                    self.result_table.setItem(i, 1, QTableWidgetItem(raw.name))
                    self.result_table.setItem(i, 2, QTableWidgetItem("✅ 已匹配"))
                    matched_count += 1
                else:
                    self.result_table.setItem(i, 1, QTableWidgetItem("-"))
                    self.result_table.setItem(i, 2, QTableWidgetItem("❌ 未找到"))

            self.match_label.setText(
                f"共 {len(matches)} 个JPG文件，成功匹配 {matched_count} 个RAW文件"
            )
            self.extract_btn.setEnabled(matched_count > 0)
            self.status_label.setText(f"扫描完成: {matched_count}/{len(matches)} 匹配成功")

        except Exception as e:
            import traceback
            show_error(
                title="扫描错误",
                description=str(e),
                details=traceback.format_exc(),
                parent=self,
            )

    def _start_extraction(self):
        output = self.output_edit.text()
        if not output:
            QMessageBox.warning(self, "提示", "请选择输出文件夹")
            return

        # 覆盖确认：检查 output 目录中是否已存在同名 RAW 文件
        try:
            matches = getattr(self, "_last_matches", []) or []
            raw_names = [raw.name for _, raw in matches if raw]
            if raw_names:
                conflicts = find_overwrite_conflicts(raw_names, [output])
                if conflicts:
                    names = next(iter(conflicts.values()))
                    preview = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
                    reply = QMessageBox.question(
                        self, "存在同名 RAW 文件",
                        f"输出目录中已存在 {len(names)} 个同名 RAW 文件，继续提取将覆盖：\n\n  · {preview}\n\n是否继续？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        self.status_label.setText("已取消：输出目录存在同名 RAW 文件")
                        return
        except Exception as e:
            self.status_label.setText(f"（警告）覆盖冲突检测失败: {e}")

        self.extract_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)

        self.worker = SimpleWorkerThread(
            self.service.extract_raw_files,
            self.jpg_edit.text(),
            self.raw_edit.text(),
            output,
            verify=self.verify_check.isChecked(),
            progress_callback=lambda c, t, m: self._progress_sig.emit(c, t, m)
        )
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        # 线程结束后自动释放，避免 QThread 对象泄漏
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()
        self.status_label.setText("正在提取RAW文件...")

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, message: str):
        if total > 0:
            percent = int((current / total) * 100)
            self.progress_bar.setValue(percent)
            self.status_label.setText(f"{message} ({percent}%)")

    def _cancel_extraction(self):
        self.service.cancel()
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("正在取消...")

    @Slot(object)
    def _on_finished(self, result):
        self.extract_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        # worker 已连 deleteLater，这里清空引用避免悬挂
        self.worker = None

        success = result['extracted']
        not_found = result['not_found']
        failed = result['failed']

        status_text = f"✅ 提取完成: 成功 {success} 个"
        if not_found > 0:
            status_text += f", 未找到 {not_found} 个"
        if failed > 0:
            status_text += f", 失败 {failed} 个"

        self.status_label.setText(status_text)

        # 数据闭环：提取后自动入库到所选项目，并继承原 JPG 的 log_id
        project_id = self.selector.get_current_project_id()
        auto_import = self.auto_import_check.isChecked() and project_id is not None
        imported_count = 0
        log_inherited = 0
        if auto_import and success > 0:
            try:
                imported_count, log_inherited = self._auto_import_extracted(result, project_id)
                if imported_count > 0:
                    self.status_label.setText(
                        status_text + f"，已入库 {imported_count} 个（{log_inherited} 个继承 log_id）"
                    )
            except Exception as e:
                logger.error(f"RAW 提取后自动入库失败: {e}", exc_info=True)
                QMessageBox.warning(
                    self, "自动入库失败",
                    f"RAW 文件已提取到输出目录，但自动入库失败：\n{e}\n"
                    "可稍后到「媒体导入」视图手动入库。"
                )

        details = []
        if failed > 0:
            for item in result['details']:
                if item['status'] == 'failed':
                    details.append(f"- {item['raw']}: {item.get('error', '未知错误')}")

        msg_text = f"RAW文件提取完成！\n\n成功: {success} 个\n未找到: {not_found} 个\n失败: {failed} 个"
        if imported_count > 0:
            msg_text += f"\n已自动入库: {imported_count} 个（{log_inherited} 个继承原 JPG 的 log_id）"
        if details:
            msg_text += f"\n\n失败详情:\n{chr(10).join(details)}"

        QMessageBox.information(self, "提取完成", msg_text)

        # 广播 assets_changed，让其他视图刷新
        if imported_count > 0:
            try:
                get_data_bus().emit_data_changed("assets_changed")
            except Exception as e:
                logger.error(f"广播 RAW 提取完成事件失败: {e}")

    def _auto_import_extracted(self, result: dict, project_id: str) -> tuple:
        """把提取出的 RAW 文件入库到 project_id，并按原 JPG 的 log_id 继承。

        Returns:
            (imported_count, log_inherited_count)
        """
        # 建立 jpg_stem -> log_id 映射（从 DB 查原 JPG 的 asset.log_id）
        stem_to_log_id = {}
        for item in result.get("details", []):
            if item.get("status") != "success":
                continue
            jpg_path = item.get("jpg")
            if not jpg_path:
                continue
            try:
                # 通过公开方法查 JPG 关联的 log_id（不再穿透到 _get_conn 私有方法）
                from pathlib import Path as _P
                log_id = self.db_service.get_asset_log_id_by_path(jpg_path)
                stem_to_log_id[_P(jpg_path).stem.lower()] = log_id
            except Exception as e:
                logger.debug(f"查 JPG log_id 失败 {jpg_path}: {e}")

        # 收集所有成功提取的 RAW 输出路径，按 stem 分组
        output_files = []
        for item in result.get("details", []):
            if item.get("status") == "success" and item.get("output"):
                output_files.append(item["output"])

        if not output_files:
            return (0, 0)

        # 按 stem 分组（同 stem 共享 log_id）
        from pathlib import Path as _P
        file_log_pairs = []
        for fp in output_files:
            stem = _P(fp).stem.lower()
            log_id = stem_to_log_id.get(stem)
            file_log_pairs.append((fp, log_id))

        imported_count = 0
        log_inherited = 0
        # 按 log_id 分批导入（import_assets 接受单一 log_id，所以分组）
        from collections import defaultdict
        groups = defaultdict(list)
        for fp, lid in file_log_pairs:
            groups[lid].append(fp)

        for lid, files in groups.items():
            try:
                r = self.import_service.import_assets(
                    project_id=project_id,
                    file_paths=files,
                    compute_checksum=True,
                    read_metadata=True,
                    log_id=lid,
                )
                imported_count += r.get("imported", 0)
                if lid:
                    log_inherited += r.get("imported", 0)
            except Exception as e:
                logger.error(f"RAW 批量入库失败 (log_id={lid}): {e}")

        return (imported_count, log_inherited)

    @Slot(str)
    def _on_error(self, error: str):
        self.extract_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(f"❌ 错误: {error}")
        self.worker = None
        show_error(
            title="提取错误",
            description=error,
            details=error,
            parent=self,
        )
