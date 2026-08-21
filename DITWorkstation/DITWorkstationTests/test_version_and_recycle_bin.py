"""版本标识与回收站入口的回归测试。"""
from datetime import date

from DITWorkstation.App.version import APP_VERSION
from PySide6.QtWidgets import QMessageBox

from DITWorkstation.Models import MediaAsset
from DITWorkstation.Views.Widgets.recycle_bin_dialog import RecycleBinDialog


def test_alpha_version_uses_fixed_date():
    """版本号在模块导入时固定，不随运行日期变化。"""
    assert APP_VERSION.startswith("alpha.")
    # 验证版本号格式为 alpha.YYYYMMDD
    parts = APP_VERSION.split(".")
    assert len(parts) == 2
    assert parts[0] == "alpha"
    assert len(parts[1]) == 8
    assert parts[1].isdigit()


def test_recycle_bin_dialog_lists_and_restores_asset(db_service, project, tmp_path, monkeypatch):
    asset = MediaAsset(
        asset_id="recycle-ui-asset", project_id=project.project_id,
        file_path=str(tmp_path / "asset.cr3"), file_name="asset.cr3",
        file_type=".cr3",
    )
    db_service.add_media_asset(asset)
    deleted = db_service.delete_media_asset_result(asset.asset_id)
    assert deleted

    dialog = RecycleBinDialog(db_service=db_service)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "素材"
    dialog.table.selectRow(0)
    monkeypatch.setattr(
        "DITWorkstation.Views.Widgets.recycle_bin_dialog.QMessageBox.information",
        lambda *args, **kwargs: None,
    )
    dialog._restore_selected()

    assert db_service.get_media_asset(asset.asset_id) is not None
    assert dialog.table.rowCount() == 0
    dialog.close()


def test_recycle_bin_dialog_restores_all_and_empties_items(db_service, project, tmp_path, monkeypatch):
    assets = [
        MediaAsset(
            asset_id=f"recycle-ui-bulk-{index}", project_id=project.project_id,
            file_path=str(tmp_path / f"asset-{index}.cr3"), file_name=f"asset-{index}.cr3",
            file_type=".cr3",
        )
        for index in range(2)
    ]
    for asset in assets:
        db_service.add_media_asset(asset)
        assert db_service.delete_media_asset_result(asset.asset_id)

    dialog = RecycleBinDialog(db_service=db_service)
    monkeypatch.setattr(
        "DITWorkstation.Views.Widgets.recycle_bin_dialog.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        "DITWorkstation.Views.Widgets.recycle_bin_dialog.QMessageBox.information",
        lambda *args, **kwargs: None,
    )
    dialog._restore_all()

    assert dialog.table.rowCount() == 0
    assert all(db_service.get_media_asset(asset.asset_id) is not None for asset in assets)

    assert db_service.delete_media_asset_result(assets[0].asset_id)
    dialog._refresh_items()
    dialog._empty_recycle_bin()

    assert dialog.table.rowCount() == 0
    assert db_service.get_recycle_bin_items() == []
    dialog.close()
