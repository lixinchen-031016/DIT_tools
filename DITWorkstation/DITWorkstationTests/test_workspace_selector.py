"""WorkspaceProjectSelector 新建项目/工作区交互测试"""

from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Views.Widgets.workspace_project_selector import (
    WorkspaceProjectSelector,
)
from PySide6.QtWidgets import QInputDialog, QMessageBox


def _make_selector(db_service):
    sel = WorkspaceProjectSelector(
        project_widget="list",
        show_new_project=True,
        db_service=db_service,
    )
    sel.show()  # 可见才能接收全局项目切换信号同步下拉
    return sel


def test_single_workspace_auto_selected(tmp_dir, monkeypatch):
    """无全局工作区但只有一个工作区时，刷新后自动选中并启用新建项目按钮"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    ws = db.create_workspace(name="唯一工作区", path="/tmp/ws")
    sel = _make_selector(db)
    sel.refresh()
    assert sel.workspace_combo.currentData() == ws.workspace_id
    assert sel.new_proj_btn.isEnabled()


def test_new_project_disabled_without_workspace(tmp_dir):
    """没有任何工作区时，新建项目按钮保持禁用"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    sel = _make_selector(db)
    sel.refresh()
    assert not sel.new_proj_btn.isEnabled()
    assert sel.workspace_combo.currentData() is None


def test_create_project_with_sole_workspace_from_all(tmp_dir, monkeypatch):
    """唯一工作区自动选中；用户手动切回「全部工作区」后新建仍落到该工作区"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    ws = db.create_workspace(name="唯一工作区", path="/tmp/ws")
    sel = _make_selector(db)
    sel.refresh()
    assert sel.workspace_combo.currentData() == ws.workspace_id  # 自动选中
    sel.workspace_combo.setCurrentIndex(0)  # 手动切回「全部工作区」
    assert sel.workspace_combo.currentData() is None
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("新项目", True))
    sel._create_project()
    projects = db.get_projects(workspace_id=ws.workspace_id)
    assert len(projects) == 1
    assert projects[0].name == "新项目"
    assert sel.project_combo.currentData() == projects[0].project_id


def test_create_project_picks_workspace_when_multiple(tmp_dir, monkeypatch):
    """多个工作区且停在「全部工作区」时，弹选择框并创建到所选工作区"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    ws1 = db.create_workspace(name="工作区A", path="/tmp/a")
    ws2 = db.create_workspace(name="工作区B", path="/tmp/b")
    sel = _make_selector(db)
    sel.refresh()
    assert sel.workspace_combo.currentData() is None  # 多个工作区不自动选
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *a, **k: ("工作区B  [/tmp/b]", True),
    )
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("项目B", True))
    sel._create_project()
    assert db.get_projects(workspace_id=ws1.workspace_id) == []
    projects = db.get_projects(workspace_id=ws2.workspace_id)
    assert len(projects) == 1
    assert projects[0].name == "项目B"


def test_create_project_prompts_workspace_first_when_none(tmp_dir, monkeypatch):
    """没有任何工作区时，新建项目给出引导提示且不创建"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    sel = _make_selector(db)
    sel.refresh()
    messages = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        staticmethod(lambda *a, **k: messages.append("shown")),
    )
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("项目X", True))
    sel._create_project()
    assert messages == ["shown"]
    assert db.get_projects() == []


def test_create_workspace_reloads_selector(tmp_dir, monkeypatch):
    """新建工作区后下拉立即刷新并选中新工作区，新建项目按钮启用"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    sel = _make_selector(db)
    sel.refresh()
    assert not sel.new_proj_btn.isEnabled()
    monkeypatch.setattr(
        "DITWorkstation.Views.Widgets.workspace_project_selector.WorkspaceDialog.create",
        staticmethod(lambda **k: db.create_workspace(name="新工作区", path="/tmp/ws")),
    )
    sel._create_workspace()
    assert sel.workspace_combo.count() == 2
    assert sel.workspace_combo.currentData() is not None
    assert sel.new_proj_btn.isEnabled()


def _make_deletable_selector(db_service):
    sel = WorkspaceProjectSelector(
        project_widget="list",
        show_edit_workspace=True,
        show_delete_workspace=True,
        show_new_project=True,
        show_delete_project=True,
        db_service=db_service,
    )
    sel.show()
    return sel


def test_delete_workspace_reassigns_projects(tmp_dir, monkeypatch):
    """删除非默认工作区：工作区消失，项目归入 default，全局会话清空"""
    from DITWorkstation.App.session_context import (
        get_current_workspace_id,
        set_current_workspace,
    )

    db = DatabaseService(db_path=tmp_dir / "test.db")
    db.get_or_create_default_workspace()
    ws = db.create_workspace(name="待删工作区", path="/tmp/ws")
    project = db.create_project(name="归属项目", workspace_id=ws.workspace_id)
    sel = _make_deletable_selector(db)
    set_current_workspace(ws.workspace_id)
    sel.refresh()
    assert sel._del_ws_btn.isEnabled()

    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.Yes),
    )
    sel._delete_workspace()

    assert db.get_workspace(ws.workspace_id) is None
    moved = db.get_project(project.project_id)
    assert moved.workspace_id == "default"
    assert get_current_workspace_id() is None
    # 下拉刷新后不再包含已删除工作区
    ids = [sel.workspace_combo.itemData(i) for i in range(sel.workspace_combo.count())]
    assert ws.workspace_id not in ids


def test_delete_workspace_default_guarded(tmp_dir, monkeypatch):
    """默认工作区：按钮禁用；绕过按钮直接调用时给出提示且不删除"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    db.get_or_create_default_workspace()
    sel = _make_deletable_selector(db)
    sel.refresh()
    # 选中 default → 按钮禁用
    sel.workspace_combo.setCurrentIndex(sel.workspace_combo.findData("default"))
    assert not sel._del_ws_btn.isEnabled()

    messages = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        staticmethod(lambda *a, **k: messages.append("shown")),
    )
    sel.workspace_combo.setCurrentIndex(sel.workspace_combo.findData("default"))
    sel._del_ws_btn.setEnabled(True)  # 绕过禁用状态直接触发
    sel._delete_workspace()
    assert messages == ["shown"]
    assert db.get_workspace("default") is not None


def test_delete_workspace_cancelled_keeps_data(tmp_dir, monkeypatch):
    """确认框选否：工作区与项目均保留"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    db.get_or_create_default_workspace()
    ws = db.create_workspace(name="保留工作区", path="/tmp/keep")
    project = db.create_project(name="保留项目", workspace_id=ws.workspace_id)
    sel = _make_deletable_selector(db)
    sel.refresh()
    sel.workspace_combo.setCurrentIndex(sel.workspace_combo.findData(ws.workspace_id))
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.No),
    )
    sel._delete_workspace()
    assert db.get_workspace(ws.workspace_id) is not None
    assert db.get_project(project.project_id) is not None


def test_delete_project_refreshes_combo(tmp_dir, monkeypatch):
    """删除项目后下拉刷新，全局项目清空"""
    from DITWorkstation.App.session_context import (
        get_current_project_id,
        set_current_project,
    )

    db = DatabaseService(db_path=tmp_dir / "test.db")
    db.get_or_create_default_workspace()
    project = db.create_project(name="待删项目")
    sel = _make_deletable_selector(db)
    set_current_project(project.project_id)
    sel.refresh()
    sel.project_combo.setCurrentIndex(sel.project_combo.findData(project.project_id))
    assert sel.del_proj_btn.isEnabled()

    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.Yes),
    )
    sel._delete_project()
    assert get_current_project_id() is None
    ids = [sel.project_combo.itemData(i) for i in range(sel.project_combo.count())]
    assert project.project_id not in ids
