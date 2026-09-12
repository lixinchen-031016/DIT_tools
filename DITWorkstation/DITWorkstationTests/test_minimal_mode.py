"""极简模式：媒体导入复制到自定义保存目录（服务层 + 视图层）

覆盖：
- 服务层：复制模式下素材落到 <保存目录>/<项目名>/，且原文件保持不动
- 服务层：取消复制时仍按引用方式导入
- 视图层：复制目标解析为 <保存目录>/<项目名>/
- 视图层：保存目录未设置时引导选择，用户取消则放弃导入
- 视图层：极简模式下复制选项文案与默认勾选
- 端到端：走视图 _start_import 完整导入链路（含线程与信号）
"""

from pathlib import Path

import pytest
from DITWorkstation.App import config
from DITWorkstation.App.feature_flags import (
    get_minimal_import_target_dir,
    resolve_minimal_import_target_dir,
    set_minimal_import_target_dir,
)
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Utils import common


@pytest.fixture
def isolate_minimal(monkeypatch, tmp_path):
    """隔离 settings.json，并把当前模式固定为极简模式、保存目录清空。"""
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_path / "settings.json"
    )
    monkeypatch.setattr(config, "usage_mode", "minimal")
    monkeypatch.setattr(config, "minimal_import_target_dir", "")
    yield


def _make_source(tmp_path, name="IMG_0001.jpg", payload=b"fake-jpeg-data") -> Path:
    src_dir = tmp_path / "card"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / name
    src.write_bytes(payload)
    return src


# ===== 服务层 =====


def test_service_copies_into_minimal_target(tmp_path, db_service, isolate_minimal):
    """极简模式核心链路：素材复制到 <保存目录>/<项目名>/，原文件不动。"""
    project = db_service.create_project(name="极简项目")
    src = _make_source(tmp_path)
    target = tmp_path / "media_out"
    set_minimal_import_target_dir(target)
    dest_dir = resolve_minimal_import_target_dir(project.name)

    service = MediaImportService(db_service=db_service)
    result = service.import_assets(
        project.project_id,
        [str(src)],
        compute_checksum=False,
        read_metadata=False,
        copy_to_workspace=True,
        workspace_dir=dest_dir,
    )

    assert result["imported"] == 1
    assert result["failed"] == 0
    copied = Path(dest_dir) / src.name
    assert copied.is_file(), "素材应复制到自定义保存目录下的项目子目录"
    assert copied.read_bytes() == src.read_bytes()
    assert src.is_file(), "原文件应保持不动"

    assets = db_service.get_media_assets(project.project_id)
    assert len(assets) == 1
    assert assets[0].is_working_copy is True
    assert Path(assets[0].file_path) == copied
    assert assets[0].original_path == str(src)


def test_service_reference_mode_unaffected_by_target(
    tmp_path, db_service, isolate_minimal
):
    """取消复制时仍按引用方式导入，保存目录不参与。"""
    project = db_service.create_project(name="引用项目")
    src = _make_source(tmp_path, name="IMG_0002.jpg")

    service = MediaImportService(db_service=db_service)
    result = service.import_assets(
        project.project_id,
        [str(src)],
        compute_checksum=False,
        read_metadata=False,
        copy_to_workspace=False,
    )

    assert result["imported"] == 1
    assets = db_service.get_media_assets(project.project_id)
    assert Path(assets[0].file_path) == src
    assert assets[0].is_working_copy is False


def test_service_minimal_target_name_conflict_keeps_both(
    tmp_path, db_service, isolate_minimal
):
    """同目录重复导入同名文件时不覆盖，追加序号保底。"""
    project = db_service.create_project(name="冲突项目")
    target = tmp_path / "media_out"
    set_minimal_import_target_dir(target)
    dest_dir = resolve_minimal_import_target_dir(project.name)

    service = MediaImportService(db_service=db_service)
    first = _make_source(tmp_path / "a", name="DUP.jpg", payload=b"A")
    second = _make_source(tmp_path / "b", name="DUP.jpg", payload=b"B")

    # 两次导入使用不同源路径（避免同一路径被查重跳过）
    service.import_assets(
        project.project_id,
        [str(first)],
        compute_checksum=False,
        read_metadata=False,
        copy_to_workspace=True,
        workspace_dir=dest_dir,
    )
    service.import_assets(
        project.project_id,
        [str(second)],
        compute_checksum=False,
        read_metadata=False,
        copy_to_workspace=True,
        workspace_dir=dest_dir,
    )

    files = sorted(p.name for p in Path(dest_dir).iterdir())
    assert files == ["DUP.jpg", "DUP_1.jpg"]
    assert (Path(dest_dir) / "DUP.jpg").read_bytes() == b"A"
    assert (Path(dest_dir) / "DUP_1.jpg").read_bytes() == b"B"


# ===== 视图层 =====


def _make_view(monkeypatch, db_service):
    """构造绑定到隔离数据库的 MediaImportView（QApplication 由 conftest 提供）。"""
    import DITWorkstation.Views.media_import_view as miv

    monkeypatch.setattr(miv, "get_db_service", lambda: db_service)
    return miv, miv.MediaImportView()


def test_view_minimal_defaults_and_target_resolution(
    tmp_path, db_service, isolate_minimal, monkeypatch
):
    """极简模式：复制项文案/默认勾选正确，目标解析为 <保存目录>/<项目名>/。"""
    _miv, view = _make_view(monkeypatch, db_service)
    try:
        project = db_service.create_project(name="视图项目")
        target = tmp_path / "view_out"
        set_minimal_import_target_dir(target)

        view._sync_copy_check_state()
        assert view.copy_mode_check.text() == "复制到保存目录"
        assert view.copy_mode_check.isChecked() is True
        assert view.minimal_dir_widget.isVisibleTo(view) is True
        assert str(target) in view.minimal_dir_label.text()

        view.current_project = project
        assert view._resolve_minimal_dest_dir() == str(target / project.name)

        view._update_copy_dest_label()
        assert str(target / project.name) in view.copy_dest_label.text()
    finally:
        view.deleteLater()


def test_view_minimal_prompts_when_target_missing(
    tmp_path, db_service, isolate_minimal, monkeypatch
):
    """保存目录未设置时：取消选择放弃导入；选择后持久化并可用于导入。"""
    miv, view = _make_view(monkeypatch, db_service)
    try:
        project = db_service.create_project(name="待设置项目")
        view.current_project = project

        # 保存目录为空 → 未设置提示
        view._sync_copy_check_state()
        assert "未设置" in view.minimal_dir_label.text()
        assert "请点击" in view.copy_dest_label.text()

        # 用户取消目录选择 → 放弃本次导入
        monkeypatch.setattr(miv, "pick_directory", lambda *a, **k: "")
        assert view._resolve_minimal_dest_dir() is None
        assert get_minimal_import_target_dir() == ""

        # 用户完成选择 → 目录被持久化，并返回 <目录>/<项目名>
        chosen = tmp_path / "picked_out"
        monkeypatch.setattr(miv, "pick_directory", lambda *a, **k: str(chosen))
        assert view._resolve_minimal_dest_dir() == str(chosen / project.name)
        assert get_minimal_import_target_dir() == str(chosen)
        assert chosen.is_dir()
    finally:
        view.deleteLater()


def test_view_minimal_copy_dest_label_highlights_missing_target(
    tmp_path, db_service, isolate_minimal, monkeypatch
):
    """未设置保存目录时目标提示应为高亮告警文案。"""
    _miv, view = _make_view(monkeypatch, db_service)
    try:
        view._sync_copy_check_state()
        view._update_copy_dest_label()
        assert "指定保存目录" in view.copy_dest_label.text()
    finally:
        view.deleteLater()


# ===== 端到端：视图完整导入链路 =====


def test_view_start_import_flow_copies_to_target(
    tmp_path, db_service, isolate_minimal, monkeypatch
):
    """端到端：极简模式走视图 _start_import，素材复制到保存目录并入库。

    屏蔽模态弹窗并驱动事件循环，验证「扫描 → 勾选 → 开始导入 → 复制到
    <保存目录>/<项目名>/ → 写入数据库」这条完整链路可用。
    """
    import DITWorkstation.Views.media_import_view as miv
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QMessageBox

    # 屏蔽模态弹窗：导入完成提示 / 覆盖确认 / 错误提示均不应阻塞测试
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok)
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok)
    )
    monkeypatch.setattr(QMessageBox, "exec", lambda _self: 0)

    monkeypatch.setattr(miv, "get_db_service", lambda: db_service)

    project = db_service.create_project(name="端到端项目")
    src = _make_source(tmp_path, name="E2E_0001.jpg", payload=b"e2e-payload")
    target = tmp_path / "e2e_out"
    set_minimal_import_target_dir(target)

    view = miv.MediaImportView()
    try:
        view.current_project = project
        view.source_edit.setText(str(src.parent))
        view._sync_copy_check_state()
        view._scan_folder()
        assert view.files_table.rowCount() == 1
        assert view.copy_mode_check.isChecked() is True

        loop = QEventLoop()
        view.task_vm.finished.connect(lambda *_: loop.quit())
        view.task_vm.error.connect(lambda *_: loop.quit())
        QTimer.singleShot(15000, loop.quit)

        view._start_import()
        if view.task_vm.is_running():
            loop.exec()

        copied = target / project.name / src.name
        assert copied.is_file(), "视图导入流程应把素材复制到自定义保存目录"
        assert copied.read_bytes() == b"e2e-payload"
        assert src.is_file(), "原文件应保持不动"

        assets = db_service.get_media_assets(project.project_id)
        assert len(assets) == 1
        assert assets[0].is_working_copy is True
        assert Path(assets[0].file_path) == copied
    finally:
        view.deleteLater()
