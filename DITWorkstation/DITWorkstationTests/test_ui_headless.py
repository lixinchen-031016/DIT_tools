"""无头 UI 测试：布局、分页、设置持久化与模板入口（复用 conftest 的 offscreen QApplication）"""
from PySide6.QtWidgets import QGridLayout, QPushButton, QComboBox

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
