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


def test_card_automation_custom_sop_runs_raw_and_rename_steps(tmp_dir, db_service):
    project = db_service.create_project(name="SOP项目")
    card = tmp_dir / "CARD"
    card.mkdir()
    jpg = card / "IMG_001.jpg"
    raw = card / "IMG_001.cr2"
    jpg.write_bytes(b"jpg")
    raw.write_bytes(b"raw")
    raw_output = tmp_dir / "raw-output"

    result = CardAutomationService(db_service).execute(
        str(card), project.project_id,
        steps=["import", "raw_extract", "rename"],
        raw_config={"output_folder": str(raw_output)},
        rename_config={
            "files": [str(jpg)],
            "rule": {"pattern": "S001_{number}", "padding": 3},
        },
    )

    assert result["steps"] == ["import", "raw_extract", "rename"]
    assert result["raw_extract"]["extracted"] == 1
    assert result["rename"]
    assert (raw_output / "IMG_001.cr2").read_bytes() == b"raw"
    assert (card / "S001_001.jpg").read_bytes() == b"jpg"


def test_card_automation_report_step_uses_injected_report_service(tmp_dir, db_service):
    project = db_service.create_project(name="报告 SOP")

    class FakeReportService:
        def generate_backup_report(self, report_project, jobs, output_path):
            assert report_project.project_id == project.project_id
            assert jobs == []
            return output_path or "default-report.pdf"

    result = CardAutomationService(db_service).execute(
        str(tmp_dir), project.project_id,
        steps=["report"], report_path="report.pdf", report_service=FakeReportService(),
    )

    assert result["report"] == "report.pdf"
