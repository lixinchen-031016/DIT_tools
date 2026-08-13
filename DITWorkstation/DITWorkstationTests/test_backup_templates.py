"""备份方案模板持久化与占位符测试。"""
from DITWorkstation.Models import ChecksumAlgorithm
from DITWorkstation.Services.backup_service import BackupService


def test_backup_template_crud(db_service):
    template = db_service.create_backup_template(
        name="双盘方案",
        target_paths=["/backup/a/{source_name}", "/backup/b/{source_name}"],
        algorithm=ChecksumAlgorithm.MD5,
        verify_after_copy=False,
        description="现场双盘",
    )
    loaded = db_service.get_backup_template(template.template_id)
    assert loaded.name == "双盘方案"
    assert loaded.target_paths == ["/backup/a/{source_name}", "/backup/b/{source_name}"]
    assert loaded.algorithm == ChecksumAlgorithm.MD5
    assert loaded.verify_after_copy is False
    assert db_service.update_backup_template(
        template.template_id, name="更新方案", target_paths=["/backup/{source_name}"]
    )
    assert db_service.get_backup_template(template.template_id).name == "更新方案"
    assert db_service.delete_backup_template(template.template_id)
    assert db_service.get_backup_template(template.template_id) is None


def test_backup_template_target_placeholder():
    assert BackupService.resolve_template_targets(
        ["/backup/{source_name}", "/backup/fixed"], "/Volumes/CARD"
    ) == ["/backup/CARD", "/backup/fixed"]
