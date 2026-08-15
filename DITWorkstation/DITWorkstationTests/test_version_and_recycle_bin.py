"""版本标识与回收站入口的回归测试。"""
from datetime import date

from DITWorkstation.App.version import APP_VERSION
from DITWorkstation.Models import MediaAsset
from DITWorkstation.Views.Widgets.recycle_bin_dialog import RecycleBinDialog


def test_alpha_version_uses_current_date():
    assert APP_VERSION == f"alpha.{date.today():%Y%m%d}"


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
