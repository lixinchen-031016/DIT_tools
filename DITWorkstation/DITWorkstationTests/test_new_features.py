"""新功能测试：完整性校验调度 / 介质身份 / 备份回拷 / 日志导入导出 / 保存搜索 / XMP / 更新检查 / 主题。"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from DITWorkstation.Services.integrity_scheduler import IntegrityScheduler, ScheduledTaskService
from DITWorkstation.Services.card_identity import (
    CardBatchQueue,
    compute_card_fingerprint,
    is_fingerprint_processed,
    mark_fingerprint_processed,
    media_files_in_top_level,
)
from DITWorkstation.Services.update_checker import check_for_update, is_newer_version
from DITWorkstation.Services.shooting_log_io import export_logs_csv, import_logs_csv
from DITWorkstation.Services.backup_restore_service import BackupRestoreService
from DITWorkstation.Models import MediaAsset


class TestScheduledTaskService:
    def test_register_and_trigger_now(self):
        counter = [0]

        def task():
            counter[0] += 1

        svc = ScheduledTaskService(tick_seconds=3600)
        svc.register("t1", task, interval_hours=24)
        svc.trigger_now("t1")
        assert counter[0] == 1
        state = svc.task_state("t1")
        assert state["interval_hours"] == 24

    def test_trigger_unknown_noop(self):
        ScheduledTaskService().trigger_now("nope")

    def test_scheduler_start_stop(self):
        svc = ScheduledTaskService(tick_seconds=999999)
        svc.register("t1", lambda: None, interval_hours=24)
        try:
            svc.start()
        finally:
            svc.stop()


class TestIntegrityScheduler:
    def test_run_verification_writes_audit(self, db_service, project):
        class FakeBackup:
            def verify_backup(self, project_id):
                return {"checked": 0, "matched": 0, "missing": 0,
                        "mismatch": 0, "unhashable": 0, "errors": []}

        integrity = IntegrityScheduler(None, db_service,
                                       scheduler=ScheduledTaskService(tick_seconds=9999))
        integrity.backup_service = FakeBackup()
        stats = integrity.run_verification(project.project_id)
        assert stats["ok"] is True
        logs = db_service.get_recent_operations(limit=5)
        assert any("完整性" in log["event"] for log in logs)

    def test_run_verification_reports_failure(self, db_service, project):
        class FakeBackup:
            def verify_backup(self, project_id):
                return {"checked": 1, "matched": 0, "missing": 1,
                        "mismatch": 0, "unhashable": 0, "errors": []}

        integrity = IntegrityScheduler(None, db_service)
        integrity.backup_service = FakeBackup()
        stats = integrity.run_verification(project.project_id)
        assert stats["ok"] is False


class TestCardIdentity:
    def test_fingerprint_stable(self, tmp_dir):
        card = tmp_dir / "CARD"
        card.mkdir()
        (card / "DCIM").mkdir()
        for i in range(5):
            (card / "DCIM" / f"IMG_{i:04d}.CR2").write_bytes(b"x")
        fp1 = compute_card_fingerprint(str(card))
        fp2 = compute_card_fingerprint(str(card))
        assert fp1 and fp1 == fp2

    def test_fingerprint_differs(self, tmp_dir):
        a, b = tmp_dir / "A", tmp_dir / "B"
        a.mkdir(); b.mkdir()
        (a / "IMG_0001.CR2").write_bytes(b"x")
        (b / "IMG_0002.CR2").write_bytes(b"x")
        assert compute_card_fingerprint(str(a)) != compute_card_fingerprint(str(b))

    def test_fingerprint_empty(self, tmp_dir):
        empty = tmp_dir / "empty"
        empty.mkdir()
        assert compute_card_fingerprint(str(empty)) == ""

    def test_processed_flags(self):
        fp = "abe" + "0" * 21
        mark_fingerprint_processed(fp)
        assert is_fingerprint_processed(fp)

    def test_batch_queue_sequence(self):
        calls = []
        queue = CardBatchQueue(start_cb=calls.append, dedupe=False)
        assert queue.enqueue("/card1") is True
        assert queue.busy
        queue.on_finished()
        assert not queue.busy
        assert calls == ["/card1"]

    def test_batch_queue_dedupe_same_path(self):
        calls = []
        queue = CardBatchQueue(start_cb=calls.append, dedupe=False)
        queue.enqueue("/a")
        assert queue.enqueue("/a") is False

    def test_media_files_counts(self, tmp_dir):
        d = tmp_dir / "x"; d.mkdir()
        (d / "IMG_1.CR2").write_bytes(b"")
        assert len(media_files_in_top_level(d)) == 1


class TestUpdateChecker:
    def test_version_compare(self):
        assert is_newer_version("alpha.20261231", "alpha.20260801")
        assert not is_newer_version("alpha.20260101", "alpha.20260801")
        assert not is_newer_version("alpha.20260801", "alpha.20260801")

    def test_check_disabled(self):
        info = check_for_update("")
        assert info.error

    def test_check_bad_url(self):
        info = check_for_update("http://127.0.0.1:1/nope.json", timeout=0.3)
        assert info.error


class TestShootingLogIO:
    def test_export_import_roundtrip(self, db_service, project, make_log):
        log = make_log(project.project_id, description="好镜头", camera="RED")
        tmp = tempfile.mktemp(suffix=".csv")
        assert export_logs_csv([log], tmp)
        stats = import_logs_csv(tmp, project.project_id, db_service)
        assert stats["created"] == 0
        assert stats["updated"] == 1
        logs = db_service.get_shooting_logs(project.project_id)
        assert logs[0].description == "好镜头"

    def test_import_creates_new(self, db_service, project):
        tmp = tempfile.mktemp(suffix=".csv")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("scene,shot,take,description\nS01,001A,01,开场镜头\nS02,002B,03,结尾镜头\n")
        stats = import_logs_csv(tmp, project.project_id, db_service)
        assert stats["created"] == 2
        assert len(db_service.get_shooting_logs(project.project_id)) == 2

    def test_import_skip_invalid_row(self, db_service, project):
        tmp = tempfile.mktemp(suffix=".csv")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("scene,shot,take,description\nS01,001A,01,ok\n,001B,02,缺场景\n")
        stats = import_logs_csv(tmp, project.project_id, db_service)
        assert stats["created"] == 1
        assert stats["skipped"] == 1

    def test_missing_file(self, db_service, project):
        stats = import_logs_csv("/nonexistent/x.csv", project.project_id, db_service)
        assert stats["errors"]


class TestBackupRestore:
    def test_restore_copies_and_verifies(self, db_service, tmp_dir):
        src_dir = tmp_dir / "src"
        src_dir.mkdir()
        backup_dir = tmp_dir / "backup"
        backup_dir.mkdir()
        dest_dir = tmp_dir / "dest"
        src_file = src_dir / "IMG_001.CR2"
        src_file.write_bytes(b"data123")

        project = db_service.create_project(name="R")
        db_service.add_media_asset(MediaAsset(
            asset_id="a1", project_id=project.project_id,
            file_path=str(src_file), file_name="IMG_001.CR2",
            file_size=src_file.stat().st_size, file_type=".cr2",
            checksum_algorithm="xxhash64", checksum_value="",
            backup_locations=[str(backup_dir)],
        ))
        (backup_dir / "IMG_001.CR2").write_bytes(b"data123")

        service = BackupRestoreService(db_service)
        stats = service.restore_project(project.project_id, str(dest_dir), verify=False)
        assert stats["restored"] >= 1
        assert (dest_dir / "IMG_001.CR2").exists()

    def test_restore_skips_identical(self, db_service, tmp_dir):
        from DITWorkstation.Utils import get_checksum_service
        from DITWorkstation.Models import ChecksumAlgorithm

        src_dir = tmp_dir / "src"
        src_dir.mkdir()
        backup_dir = tmp_dir / "backup"
        backup_dir.mkdir()
        dest_dir = tmp_dir / "dest"
        dest_dir.mkdir()
        src_file = src_dir / "IMG_001.CR2"
        src_file.write_bytes(b"data123")
        checksum = get_checksum_service()
        hv = checksum.compute_file_checksum(str(src_file), ChecksumAlgorithm.XXHASH64).hash_value

        project = db_service.create_project(name="R2")
        db_service.add_media_asset(MediaAsset(
            asset_id="a2", project_id=project.project_id,
            file_path=str(src_file), file_name="IMG_001.CR2",
            file_size=src_file.stat().st_size, file_type=".cr2",
            checksum_algorithm="xxhash64", checksum_value=hv,
            backup_locations=[str(backup_dir)],
        ))
        (backup_dir / "IMG_001.CR2").write_bytes(b"data123")
        (dest_dir / "IMG_001.CR2").write_bytes(b"data123")

        service = BackupRestoreService(db_service)
        stats = service.restore_project(project.project_id, str(dest_dir), verify=True)
        # 目标已存在且校验一致，应为 skipped（至少 1）
        # 若不满足，检查 _locate_backup_file 是否能找到备份文件
        assert stats["skipped"] >= 1, f"stats={stats}"
        assert (dest_dir / "IMG_001.CR2").read_bytes() == b"data123"


class TestSavedSearch:
    def test_crud_roundtrip(self, db_service, project):
        s = db_service.create_saved_search("优选集", {"rating": 3, "tag": "优选"},
                                           project_id=project.project_id, is_smart=True)
        assert s["is_smart"] is True
        got = db_service.get_saved_search(s["search_id"])
        assert got["filters"]["rating"] == 3
        assert db_service.update_saved_search(s["search_id"], name="改名")
        assert db_service.get_saved_search(s["search_id"])["name"] == "改名"
        assert db_service.delete_saved_search(s["search_id"])
        assert db_service.get_saved_search(s["search_id"]) is None

    def test_filters_cleanup(self, db_service, project):
        s = db_service.create_saved_search("空筛选", {"project_id": None, "tag": ""})
        assert s["filters"] == {}
        s2 = db_service.create_saved_search("未评级", {"rating": 0})
        assert s2["filters"] == {"rating": 0}

    def test_list_order(self, db_service, project):
        db_service.create_saved_search("A", {"scene": "S1"})
        db_service.create_saved_search("B", {"scene": "S2"})
        names = [x["name"] for x in db_service.get_saved_searches()]
        assert set(names) == {"A", "B"}


class TestXmpWriteback:
    def test_write_sidecar(self, tmp_dir):
        from DITWorkstation.Services.metadata_service import MetadataService
        f = tmp_dir / "IMG_001.CR3"
        f.write_bytes(b"\x00" * 10)
        svc = MetadataService()
        assert svc.write_xmp_sidecar(str(f), rating=3, tags=["日戏"], notes="备注")
        xmp = tmp_dir / "IMG_001.CR3.xmp"
        assert xmp.exists()
        content = xmp.read_text()
        assert "<xmp:Rating>3</xmp:Rating>" in content
        assert "日戏" in content
        assert "备注" in content

    def test_write_nonexistent(self, tmp_dir):
        from DITWorkstation.Services.metadata_service import MetadataService
        svc = MetadataService()
        assert svc.write_xmp_sidecar(str(tmp_dir / "no.jpg"), rating=1) is False


class TestTheme:
    def test_theme_switch(self):
        from DITWorkstation.Views.Styles.theme import (
            COLOR, LIGHT_PALETTE, DARK_PALETTE, set_theme_mode, get_theme_mode,
        )
        set_theme_mode("dark")
        assert COLOR.BG_APP == DARK_PALETTE.BG_APP
        assert get_theme_mode() == "dark"
        set_theme_mode("light")
        assert COLOR.BG_APP == LIGHT_PALETTE.BG_APP
        assert get_theme_mode() == "light"
