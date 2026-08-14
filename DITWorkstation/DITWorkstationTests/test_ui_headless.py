"""无头 UI 测试：布局、分页、设置持久化与模板入口（复用 conftest 的 offscreen QApplication）"""
from PySide6.QtWidgets import QGridLayout, QPushButton, QComboBox
from PySide6.QtGui import QShortcut

from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Views.Widgets.workspace_project_selector import (
    WorkspaceProjectSelector,
)


# ===== 工作区选择器：新建按钮置于下拉框下方 =====

def test_selector_buttons_below_layout(tmp_dir):
    db = DatabaseService(db_path=tmp_dir / "test.db")
    sel = WorkspaceProjectSelector(
        project_widget="combo",
        show_new_project=True,
        buttons_below=True,
        db_service=db,
    )
    sel.show()
    layout = sel.layout()
    assert isinstance(layout, QGridLayout)
    combos = sel.findChildren(QComboBox)
    buttons = [b for b in sel.findChildren(QPushButton)]
    assert buttons, "应存在新建按钮"
    combo_bottom = max(c.mapTo(sel, c.rect().bottomLeft()).y() for c in combos)
    btn_top = min(b.mapTo(sel, b.rect().topLeft()).y() for b in buttons)
    assert btn_top >= combo_bottom - 2, "新建按钮应位于下拉框下方，不能重叠"


def test_selector_horizontal_layout_no_buttons_below(tmp_dir):
    """默认（非 buttons_below）仍为水平布局，不影响其他视图"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    sel = WorkspaceProjectSelector(
        project_widget="combo",
        show_new_project=True,
        buttons_below=False,
        db_service=db,
    )
    sel.show()
    from PySide6.QtWidgets import QHBoxLayout
    assert isinstance(sel.layout(), QHBoxLayout)


# ===== 素材检索分页 =====

def test_search_view_pagination(tmp_dir, monkeypatch):
    from DITWorkstation.Utils import common, reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    from DITWorkstation.Models import MediaAsset
    reset_singletons(); reset_session_state()
    db = DatabaseService(db_path=tmp_dir / "test.db")
    common._shared_db_service = db
    try:
        project = db.create_project(name="分页项目")
        assets = [
            MediaAsset(
                asset_id=f"a{i}", project_id=project.project_id,
                file_path=f"/p/{i}.jpg", file_name=f"DSC_{i:04d}.jpg",
                tags="日戏",
            )
            for i in range(1200)
        ]
        db.add_media_assets_batch(assets)

        from DITWorkstation.Views.search_view import SearchView
        view = SearchView()
        view.project_combo.clear()
        view.project_combo.addItem("分页项目", project.project_id)
        view._search()
        assert view._total == 1200
        assert view.result_table.rowCount() == 500
        assert view.page_label.text() == "第 1 / 3 页"
        assert not view.prev_page_btn.isEnabled()
        assert view.next_page_btn.isEnabled()

        view._search(go_to_page=2)
        assert view.result_table.rowCount() == 200
        assert view.page_label.text() == "第 3 / 3 页"
        assert view.next_page_btn.isEnabled() is False

        # 标签自动补全来自 asset_tags
        completer = view.tag_edit.completer()
        assert completer is not None
        assert completer.model().rowCount() == 1
    finally:
        reset_singletons(); reset_session_state()


# ===== 设置对话框：开关写入 settings.json =====

def test_settings_dialog_persists_toggles(tmp_dir, monkeypatch):
    from DITWorkstation.Utils import common, load_app_settings
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_dir / "settings.json"
    )
    from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    dlg._on_verify_after_copy_toggled(False)
    dlg._on_auto_detect_toggled(False)
    cfg = load_app_settings()
    assert cfg["verify_after_copy"] is False
    assert cfg["auto_detect_volume"] is False


def test_settings_dialog_has_location_and_cleanup_controls(tmp_dir, monkeypatch):
    """设置对话框应包含存储位置重设与日志清理控件"""
    from DITWorkstation.Utils import common
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_dir / "settings.json"
    )
    from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    # 存储位置：四个目录行均有「更改…」按钮
    for label in (
        dlg.db_dir_label, dlg.report_dir_label,
        dlg.log_dir_label, dlg.thumb_dir_label,
    ):
        assert label.text(), "路径标签应显示当前目录"
    assert hasattr(dlg, "delete_logs_btn")
    assert hasattr(dlg, "clear_cache_btn")
    assert dlg.delete_logs_btn.text() == "🗑 删除日志文件"
    # 标签内容应包含「共 x 个文件」之类的统计信息
    assert "日志" in dlg.log_info_label.text() or "共" in dlg.log_info_label.text()
    assert hasattr(dlg, "auto_card_automation_check")
    assert hasattr(dlg, "auto_card_project_combo")
    assert hasattr(dlg, "auto_card_template_combo")


def test_settings_dialog_change_db_dir_moves_database(tmp_dir, monkeypatch):
    """更改数据库目录：移动数据库文件并持久化新位置（不重启分支）"""
    from DITWorkstation.Utils import common, reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    from DITWorkstation.App import config
    reset_singletons(); reset_session_state()
    monkeypatch.setattr(common, "_get_settings_path", lambda: tmp_dir / "settings.json")
    monkeypatch.setattr(config, "db_dir", tmp_dir / "old")
    monkeypatch.setattr(config, "db_name", "test.db")
    old = tmp_dir / "old"
    old.mkdir()
    db = DatabaseService(db_path=old / "test.db")
    db.create_project(name="迁移项目")
    common._shared_db_service = db
    try:
        import PySide6.QtWidgets as QW
        from DITWorkstation.Views.Widgets import settings_dialog as sd
        monkeypatch.setattr(
            sd, "pick_directory", lambda *a, **k: str(tmp_dir / "new")
        )
        monkeypatch.setattr(
            QW.QMessageBox, "question",
            lambda *a, **k: QW.QMessageBox.No,
        )
        dlg = sd.SettingsDialog()
        dlg._change_db_dir()
        new = tmp_dir / "new"
        assert (new / "test.db").exists(), "数据库文件应移动到新目录"
        assert config.db_dir == new
        assert common.load_app_settings()["db_dir"] == str(new)
    finally:
        reset_singletons(); reset_session_state()


# ===== 项目概览：模板入口存在 =====
def test_dashboard_has_template_buttons(tmp_dir, monkeypatch):
    from DITWorkstation.Utils import common, reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    reset_singletons(); reset_session_state()
    db = DatabaseService(db_path=tmp_dir / "test.db")
    common._shared_db_service = db
    try:
        from DITWorkstation.Views.project_dashboard_view import ProjectDashboardView
        view = ProjectDashboardView()
        assert hasattr(view, "btn_template")
        assert hasattr(view, "btn_save_template")
        assert view.btn_template.text().startswith("🧩")
        # 回归：_setup_ui 必须完整创建全部控件（recent_ops_label / task_progress）
        assert hasattr(view, "recent_ops_label")
        assert hasattr(view, "task_progress")
        # 触发刷新不应抛 AttributeError（模拟 showEvent 后的 _refresh 路径）
        view._on_show_refresh()
    finally:
        reset_singletons(); reset_session_state()


# ===== 功能模式：主窗口构建与导航（对应设计文档 9.2 节）=====

def _build_main_window(tmp_dir, monkeypatch, mode: str):
    """以指定功能模式构建 MainWindow（隔离 settings.json 与数据库）。"""
    from DITWorkstation.Utils import common, reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    from DITWorkstation.App import config
    reset_singletons(); reset_session_state()
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_dir / "settings.json"
    )
    monkeypatch.setattr(config, "usage_mode", mode)
    db = DatabaseService(db_path=tmp_dir / "test.db")
    common._shared_db_service = db
    from DITWorkstation.Views.main_window import MainWindow
    return MainWindow()


def _teardown_main_window(window):
    from DITWorkstation.Utils import reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    try:
        window.volume_monitor.stop()
        window.close()
        window.deleteLater()
    except Exception:
        pass
    reset_singletons(); reset_session_state()


def test_main_window_team_mode_builds_all_nav(tmp_dir, monkeypatch):
    """团队模式：导航 9 项且与视图栈一一对应"""
    window = _build_main_window(tmp_dir, monkeypatch, "team")
    try:
        assert window.nav_list.count() == 9
        assert window.stack.count() == 9
        assert [k for k, _, _ in window.active_nav_items] == [
            "dashboard", "import", "backup", "log", "raw",
            "rename", "search", "asset_info", "report",
        ]
        # 团队模式注册 Ctrl+L
        keys = [s.key().toString() for s in window.findChildren(QShortcut)]
        assert "Ctrl+L" in keys
        assert "Ctrl+9" in keys
    finally:
        _teardown_main_window(window)


def test_main_window_personal_mode_builds_trimmed_nav(tmp_dir, monkeypatch):
    """个人模式：导航 7 项（无日志/报告），视图栈与导航数量一致"""
    window = _build_main_window(tmp_dir, monkeypatch, "personal")
    try:
        assert window.nav_list.count() == 7
        assert window.stack.count() == 7
        assert [k for k, _, _ in window.active_nav_items] == [
            "dashboard", "import", "backup", "raw",
            "rename", "search", "asset_info",
        ]
        keys = [s.key().toString() for s in window.findChildren(QShortcut)]
        # 个人模式不注册 Ctrl+L，Ctrl+数字不越界（最多 Ctrl+7）
        assert "Ctrl+L" not in keys
        assert "Ctrl+7" in keys
        assert "Ctrl+8" not in keys
        assert "Ctrl+9" not in keys
        # 隐藏视图仍被实例化（关闭流程/worker 检查依赖视图属性）
        assert window.view_by_key["log"] is window.log_view
        assert window.view_by_key["report"] is window.report_view
    finally:
        _teardown_main_window(window)


def test_main_window_personal_f5_and_navigation_safe(tmp_dir, monkeypatch):
    """个人模式：F5 逐页刷新不访问隐藏页面；跳转到隐藏页静默忽略"""
    window = _build_main_window(tmp_dir, monkeypatch, "personal")
    try:
        for row in range(window.nav_list.count()):
            window.nav_list.setCurrentRow(row)
            window._refresh_current_view()  # 不应抛异常
        # 跳转到隐藏页面：get_nav_index 返回 None，静默忽略不越界
        window._navigate_to("log")
        window._navigate_to("report")
        assert window.stack.currentIndex() == window.nav_list.currentRow()
        # 跨视图跳转入口（概览 SOP 按钮）不应抛异常
        window.dashboard_view._jump_to(None)
    finally:
        _teardown_main_window(window)


def test_main_window_personal_hides_team_dashboard_entries(tmp_dir, monkeypatch):
    """个人模式：概览页隐藏日志/报告/模板/归档/审计入口"""
    window = _build_main_window(tmp_dir, monkeypatch, "personal")
    try:
        dash = window.dashboard_view
        assert dash.btn_log.isHidden()
        assert dash.btn_report.isHidden()
        assert dash.btn_archive.isHidden()
        assert dash.btn_template.isHidden()
        assert dash.recent_ops_label.isHidden()
        assert dash.card_logs.isHidden()
        # 个人模式 SOP 文案不出现日志/报告
        dash._refresh()
        assert "拍摄日志" not in dash.sop_hint.text()
        assert "报告" not in dash.sop_hint.text()
    finally:
        _teardown_main_window(window)


# ===== 功能模式：项目选择器 =====

def test_selector_personal_hides_workspace_and_shows_all_projects(tmp_dir, monkeypatch):
    """个人模式：工作区控件隐藏，项目列表显示全部工作区的项目"""
    from DITWorkstation.App import config
    monkeypatch.setattr(config, "usage_mode", "personal")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    ws_a = db.create_workspace(name="工作区A")
    ws_b = db.create_workspace(name="工作区B")
    db.create_project(name="项目甲", workspace_id=ws_a.workspace_id)
    db.create_project(name="项目乙", workspace_id=ws_b.workspace_id)

    sel = WorkspaceProjectSelector(
        project_widget="combo", show_new_project=True, db_service=db,
    )
    sel.show()
    assert sel._show_workspace is False
    assert not sel.workspace_combo.isVisible()
    assert sel.project_combo.isVisible()
    sel.refresh()
    # 「（未选择项目）」+ 全部 2 个项目（跨工作区均可见）
    assert sel.project_combo.count() == 3
    # 新建项目按钮始终可用（数据库自动归入 default 工作区）
    assert sel.new_proj_btn.isEnabled()


def test_selector_personal_create_project_uses_default_workspace(tmp_dir, monkeypatch):
    """个人模式：新建项目传入 workspace_id=None，由数据库归入 default 工作区"""
    import PySide6.QtWidgets as QW
    from DITWorkstation.App import config
    monkeypatch.setattr(config, "usage_mode", "personal")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    sel = WorkspaceProjectSelector(
        project_widget="combo", show_new_project=True, db_service=db,
    )
    sel.show()
    sel.refresh()
    monkeypatch.setattr(
        QW.QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("个人新项目", True)),
    )
    sel._create_project()
    project = db.get_projects()[0]
    assert project.name == "个人新项目"
    assert project.workspace_id == "default"
    # default 工作区由数据库自动创建（名称为「默认工作区」，id 固定为 default）
    workspaces = db.get_workspaces()
    assert any(w.workspace_id == "default" for w in workspaces)


# ===== 功能模式：备份页单目标限制 =====

def test_backup_view_personal_single_target(tmp_dir, monkeypatch):
    """个人模式：可添加第一个备份目标，禁止添加第二个；模板/MHL 控件隐藏"""
    import PySide6.QtWidgets as QW
    from DITWorkstation.App import config
    monkeypatch.setattr(config, "usage_mode", "personal")
    # 拦截模态提示框，避免 offscreen 环境下 exec() 阻塞
    monkeypatch.setattr(QW.QMessageBox, "information", lambda *a, **k: None)
    db = DatabaseService(db_path=tmp_dir / "test.db")
    from DITWorkstation.Views.backup_view import BackupView
    view = BackupView(db_service=db)
    try:
        # 模板与 MHL 控件隐藏
        assert view.template_combo.isHidden()
        assert view.mhl_btn.isHidden()
        # 添加第一个目标
        monkeypatch.setattr(
            BackupView, "_pick_directory",
            lambda self, *a, **k: str(tmp_dir / "target1"),
        )
        view._add_target()
        assert len(view._target_paths) == 1
        assert not view.add_target_btn.isEnabled()
        # 添加第二个目标被拦截
        view._add_target()
        assert len(view._target_paths) == 1
        # 移除后可再次添加
        view._remove_target_at(0)
        assert view.add_target_btn.isEnabled()
    finally:
        view.close()
        view.deleteLater()


# ===== 功能模式：检索/素材信息页不访问隐藏控件 =====

def test_search_view_personal_ignores_hidden_filters(tmp_dir, monkeypatch):
    """个人模式：日志/评级筛选隐藏，搜索显式传入 log_id=None / rating=None"""
    from DITWorkstation.App import config
    monkeypatch.setattr(config, "usage_mode", "personal")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    from DITWorkstation.Utils import common
    common._shared_db_service = db
    try:
        from DITWorkstation.Views.search_view import SearchView
        view = SearchView()
        assert view.log_combo.isHidden()
        assert view.rating_combo.isHidden()
        assert view.result_table.isColumnHidden(5)  # 关联日志列
        assert view.result_table.isColumnHidden(6)  # 评级列
        # 即使隐藏控件被写入值，filters 也强制为 None
        view.log_combo.addItem("某日志", "log-1")
        view.log_combo.setCurrentIndex(1)
        view.rating_combo.setCurrentIndex(3)
        filters = view._collect_filters()
        assert filters["log_id"] is None
        assert filters["rating"] is None
        view.close()
        view.deleteLater()
    finally:
        from DITWorkstation.Utils import reset_singletons
        from DITWorkstation.App.session_context import reset_session_state
        reset_singletons(); reset_session_state()


def test_asset_info_view_personal_hides_ratings(tmp_dir, monkeypatch):
    """个人模式：素材信息页隐藏评级行与批量评级按钮"""
    from DITWorkstation.App import config
    monkeypatch.setattr(config, "usage_mode", "personal")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    from DITWorkstation.Utils import common
    common._shared_db_service = db
    try:
        from DITWorkstation.Views.asset_info_view import AssetInfoView
        view = AssetInfoView()
        assert view.rating_label.isHidden()
        for _value, btn in view._rating_buttons:
            assert btn.isHidden()
        for btn in view.batch_rating_buttons:
            assert btn.isHidden()
        # 删除按钮保留
        assert not view.batch_delete_btn.isHidden()
        view.close()
        view.deleteLater()
    finally:
        from DITWorkstation.Utils import reset_singletons
        from DITWorkstation.App.session_context import reset_session_state
        reset_singletons(); reset_session_state()


# ===== 素材信息页：文件存在性验证 + 批量清理丢失素材 =====

def test_asset_info_view_marks_and_cleans_missing_files(tmp_dir, monkeypatch):
    """素材信息页自动校验文件存在性并标识/清理「文件已丢失」记录（含二次确认）"""
    import PySide6.QtWidgets as QW
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from DITWorkstation.App import config
    from DITWorkstation.Models import MediaAsset
    from DITWorkstation.Utils import common, reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    from DITWorkstation.Views.asset_info_view import AssetInfoView

    reset_singletons(); reset_session_state()
    monkeypatch.setattr(config, "usage_mode", "personal")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    common._shared_db_service = db
    project = db.create_project(name="丢失检测项目")

    real = tmp_dir / "real.cr2"
    real.write_text("data", encoding="utf-8")
    db.add_media_asset(MediaAsset(
        asset_id="a_present", project_id=project.project_id,
        file_path=str(real), file_name="real.cr2",
    ))
    db.add_media_asset(MediaAsset(
        asset_id="a_missing", project_id=project.project_id,
        file_path="/no/such/file.cr2", file_name="lost.cr2",
    ))
    try:
        view = AssetInfoView()
        # 让选择器返回测试项目（避免依赖全局会话状态）
        monkeypatch.setattr(
            view.selector, "get_current_project_id",
            lambda: project.project_id,
        )
        view._load_assets()

        def wait_until(predicate, timeout_ms=2000):
            elapsed = 0
            while elapsed < timeout_ms and not predicate():
                QTest.qWait(20)
                elapsed += 20
            assert predicate()

        wait_until(lambda: not view._missing_scan_pending)

        # 1) 列表渲染：共 2 行，状态列正确标识
        assert view.asset_table.rowCount() == 2
        status_by_id = {}
        for r in range(view.asset_table.rowCount()):
            aid = view.asset_table.item(r, 0).data(Qt.UserRole)
            status_by_id[aid] = view.asset_table.item(r, 4).text()
        assert status_by_id["a_present"] == "✓ 正常"
        assert status_by_id["a_missing"] == "⚠ 文件已丢失"
        # 计数文案包含丢失提示，清理按钮启用
        assert "文件已丢失" in view.asset_count_label.text()
        assert view.cleanup_missing_btn.isEnabled()

        # 2) 选中丢失素材：详情面板显示警告横幅（不触发缩略图生成）
        missing_row = next(
            r for r in range(view.asset_table.rowCount())
            if view.asset_table.item(r, 0).data(Qt.UserRole) == "a_missing"
        )
        view.asset_table.selectRow(missing_row)
        view._on_asset_selected()
        assert not view.missing_banner.isHidden()
        assert "文件已丢失" in view.missing_banner.text()

        # 3) 一键清理：二次确认拦截默认 No；确认 Yes 后仅删除丢失记录
        monkeypatch.setattr(
            QW.QMessageBox, "question",
            staticmethod(lambda *a, **k: QW.QMessageBox.No),
        )
        monkeypatch.setattr(QW.QMessageBox, "information", lambda *a, **k: None)
        view._batch_cleanup_missing()
        wait_until(lambda: not view._missing_scan_pending)
        # 默认 No：记录仍在
        assert db.get_media_asset("a_missing") is not None

        monkeypatch.setattr(
            QW.QMessageBox, "question",
            staticmethod(lambda *a, **k: QW.QMessageBox.Yes),
        )
        view._batch_cleanup_missing()
        wait_until(
            lambda: db.get_media_asset("a_missing") is None
            and not view._missing_scan_pending
        )
        # 完成后：丢失记录删除，正常记录保留
        assert db.get_media_asset("a_missing") is None
        assert db.get_media_asset("a_present") is not None
        assert view.asset_table.rowCount() == 1
        assert not view.cleanup_missing_btn.isEnabled()
        # 磁盘真实文件不受影响
        assert real.exists()

        view.close()
        view.deleteLater()
    finally:
        reset_singletons(); reset_session_state()


# ===== 功能模式：设置对话框入口 =====

def test_settings_dialog_usage_mode_switch(tmp_dir, monkeypatch):
    """设置对话框包含「使用场景」；切换写入 app_config.usage_mode 并提示重启"""
    import PySide6.QtWidgets as QW
    from DITWorkstation.Utils import common, load_app_settings
    from DITWorkstation.App import config
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_dir / "settings.json"
    )
    # 注册原值以便测试后恢复（set_usage_mode 会直接写全局 config）
    monkeypatch.setattr(config, "usage_mode", "team")
    from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    assert hasattr(dlg, "usage_mode_combo")
    assert dlg.usage_mode_combo.currentData() == "team"
    # 选择「稍后重启」分支，避免真的退出进程
    monkeypatch.setattr(QW.QMessageBox, "exec", lambda self: 0)
    personal_idx = dlg.usage_mode_combo.findData("personal")
    dlg.usage_mode_combo.setCurrentIndex(personal_idx)
    assert load_app_settings()["usage_mode"] == "personal"
    dlg.close()
    dlg.deleteLater()


def test_settings_dialog_personal_hides_card_automation(tmp_dir, monkeypatch):
    """个人模式：设置对话框隐藏相机卡自动化配置，保留存储卡检测开关"""
    from DITWorkstation.Utils import common
    from DITWorkstation.App import config
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_dir / "settings.json"
    )
    monkeypatch.setattr(config, "usage_mode", "personal")
    from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    assert dlg.auto_card_automation_check.isHidden()
    assert dlg.auto_card_project_combo.isHidden()
    assert not dlg.auto_detect_check.isHidden()
    dlg.close()
    dlg.deleteLater()


# ===== 功能模式：数据兼容（对应设计文档 9.3 节）=====

def test_personal_mode_keeps_team_data_readable(tmp_dir, monkeypatch):
    """个人模式不删除团队数据；切回团队模式后日志/评级数据仍在"""
    from DITWorkstation.App import config
    db = DatabaseService(db_path=tmp_dir / "test.db")
    project = db.create_project(name="兼容项目")
    from DITWorkstation.Models import ShootingLog, MediaAsset
    log = db.create_shooting_log(ShootingLog(
        log_id="log-1", project_id=project.project_id,
        scene="S001", shot="001A", take="01",
    ))
    db.add_media_asset(MediaAsset(
        asset_id="a1", project_id=project.project_id,
        file_path="/p/1.cr2", file_name="1.cr2",
        log_id=log.log_id, rating=2,
    ))
    # 个人模式读取：数据完整
    monkeypatch.setattr(config, "usage_mode", "personal")
    asset = db.get_media_asset("a1")
    assert asset.log_id == "log-1"
    assert asset.rating == 2
    assert len(db.get_shooting_logs(project.project_id)) == 1
    # 个人模式新增项目可在团队模式看到
    personal_project = db.create_project(name="个人期项目", workspace_id=None)
    monkeypatch.setattr(config, "usage_mode", "team")
    names = [p.name for p in db.get_projects()]
    assert "个人期项目" in names
    assert "兼容项目" in names
    assert db.get_shooting_log("log-1") is not None


# ===== 个人模式：默认工作区路径（对应优化方案步骤1/步骤4）=====

def test_ensure_personal_default_workspace_path(tmp_dir, monkeypatch):
    """个人模式：确保 default 工作区拥有合法物理路径（引用配置项、创建目录）"""
    from DITWorkstation.App import config
    from DITWorkstation.App.feature_flags import (
        ensure_personal_default_workspace_path,
    )
    monkeypatch.setattr(config, "usage_mode", "personal")
    default_dir = tmp_dir / "DIT_Projects"
    monkeypatch.setattr(config, "personal_default_workspace_path", default_dir)
    db = DatabaseService(db_path=tmp_dir / "test.db")
    result = ensure_personal_default_workspace_path(db)
    assert result == str(default_dir)
    assert default_dir.exists()
    ws = db.get_workspace("default")
    assert ws is not None
    assert ws.path == str(default_dir)


def test_ensure_personal_fills_existing_empty_default(tmp_dir, monkeypatch):
    """个人模式：旧库 default 工作区 path 为空时自动补填默认路径"""
    from DITWorkstation.App import config
    from DITWorkstation.App.feature_flags import (
        ensure_personal_default_workspace_path,
    )
    monkeypatch.setattr(config, "usage_mode", "personal")
    default_dir = tmp_dir / "DIT_Projects"
    monkeypatch.setattr(config, "personal_default_workspace_path", default_dir)
    db = DatabaseService(db_path=tmp_dir / "test.db")
    # 模拟旧库：default 工作区已存在但 path 为空
    db.create_project(name="旧项目")  # 触发 default 工作区创建（path=""）
    assert db.get_workspace("default").path == ""
    result = ensure_personal_default_workspace_path(db)
    assert result == str(default_dir)
    assert db.get_workspace("default").path == str(default_dir)


def test_ensure_personal_default_workspace_path_team_noop(tmp_dir, monkeypatch):
    """团队模式：ensure 直接跳过，不影响其工作区管理"""
    from DITWorkstation.App import config
    from DITWorkstation.App.feature_flags import (
        ensure_personal_default_workspace_path,
    )
    monkeypatch.setattr(config, "usage_mode", "team")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    assert ensure_personal_default_workspace_path(db) is None


def test_writable_directory_preserves_existing_probe_file(tmp_dir):
    """可写性检查不能删除目录中原有的同名文件"""
    from DITWorkstation.Utils import is_writable_directory

    probe = tmp_dir / ".write_test"
    probe.write_text("keep", encoding="utf-8")
    assert is_writable_directory(tmp_dir)
    assert probe.read_text(encoding="utf-8") == "keep"


# ===== 个人模式：导入界面路径选择（对应优化方案步骤2/步骤3）=====

def test_import_view_copy_check_enabled_when_ws_path_empty(tmp_dir, monkeypatch):
    """导入界面：工作区 path 为空时不禁用复选框，并显示「选择目录…」按钮"""
    from DITWorkstation.App import config
    from DITWorkstation.Utils import common, reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    from DITWorkstation.Views.media_import_view import MediaImportView
    reset_singletons(); reset_session_state()
    monkeypatch.setattr(config, "usage_mode", "personal")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    common._shared_db_service = db
    try:
        view = MediaImportView()
        # 当前工作区 path 为空（个人模式 default 工作区初始情形）
        empty_ws = db.get_or_create_default_workspace()  # path=""
        view._get_current_workspace = lambda: empty_ws
        view.show()
        view._sync_copy_check_state()
        assert view.copy_mode_check.isEnabled()
        assert view.path_picker_btn.isVisible()
        # 补上 path 后重新同步：复选框仍可用，按钮隐藏
        empty_ws.path = str(tmp_dir / "ws")
        view._sync_copy_check_state()
        assert view.copy_mode_check.isEnabled()
        assert not view.path_picker_btn.isVisible()
        view.close()
        view.deleteLater()
    finally:
        reset_singletons(); reset_session_state()


def test_import_view_picker_sets_workspace_path(tmp_dir, monkeypatch):
    """导入界面：点击「选择目录…」写回工作区路径并持久化（mock 文件对话框）"""
    import PySide6.QtWidgets as QW
    from DITWorkstation.App import config
    from DITWorkstation.Utils import common, reset_singletons
    from DITWorkstation.App.session_context import reset_session_state
    from DITWorkstation.Views.media_import_view import MediaImportView
    reset_singletons(); reset_session_state()
    monkeypatch.setattr(config, "usage_mode", "personal")
    db = DatabaseService(db_path=tmp_dir / "test.db")
    common._shared_db_service = db
    try:
        view = MediaImportView()
        empty_ws = db.get_or_create_default_workspace()  # path=""
        view._get_current_workspace = lambda: empty_ws
        view.show()
        picked = str(tmp_dir / "picked_ws")
        monkeypatch.setattr(
            QW.QFileDialog, "getExistingDirectory",
            staticmethod(lambda *a, **k: picked),
        )
        view._on_pick_workspace_path()
        # 路径写回并持久化
        assert empty_ws.path == picked
        assert db.get_workspace("default").path == picked
        assert view.path_picker_btn.isHidden()
        view.close()
        view.deleteLater()
    finally:
        reset_singletons(); reset_session_state()
