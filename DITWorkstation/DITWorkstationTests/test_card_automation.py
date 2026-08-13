"""相机卡自动化流程测试。"""
from DITWorkstation.Models import ChecksumAlgorithm
from DITWorkstation.Services.card_automation_service import CardAutomationService


def test_card_automation_import_and_backup(tmp_dir, db_service):
    project = db_service.create_project(name="自动化项目")
    card = tmp_dir / "CARD"
    card.mkdir()
    source = card / "IMG_001.jpg"
    source.write_bytes(b"camera-data")
    destination = tmp_dir / "backup"
    template = db_service.create_backup_template(
        name="自动方案",
        target_paths=[str(destination / "{source_name}")],
        algorithm=ChecksumAlgorithm.XXHASH64,
        verify_after_copy=False,
    )

    result = CardAutomationService(db_service).execute(
        str(card), project.project_id, template=template,
        do_import=True, do_backup=True,
    )

    assert result["import"]["imported"] == 1
    assert result["backup"].status.value == "completed"
    assert (destination / "CARD" / "IMG_001.jpg").read_bytes() == b"camera-data"
    asset = db_service.get_media_assets(project.project_id)[0]
    assert str(destination / "CARD") in asset.backup_locations
