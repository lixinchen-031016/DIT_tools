"""相机卡自动化流程：按配置完成导入和/或备份。"""
from pathlib import Path
from typing import Callable, Optional

from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Models import BackupTemplate


class CardAutomationService:
    """把存储卡检测后的自动操作放在一个可测试的后台任务函数中。"""

    def __init__(self, db_service):
        self.db_service = db_service
        self.backup_service = BackupService(db_service=db_service)
        self.import_service = MediaImportService(db_service=db_service)

    def execute(
        self,
        source_path: str,
        project_id: str,
        template: Optional[BackupTemplate] = None,
        do_import: bool = True,
        do_backup: bool = False,
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> dict:
        """执行相机卡自动化流程，返回导入与备份结果。"""
        if not project_id:
            raise ValueError("自动化配置未选择项目")
        if not do_import and not do_backup:
            raise ValueError("至少启用自动导入或自动备份")
        if do_backup and template is None:
            raise ValueError("自动备份未选择备份方案")

        files = self.backup_service.scan_source(source_path)
        paths = [item["path"] for item in files]
        result = {"source_path": source_path, "files": len(paths), "import": None, "backup": None}

        def check_cancel():
            return bool(cancel_check and cancel_check())

        if do_backup:
            targets = self.backup_service.resolve_template_targets(
                template.target_paths, source_path
            )
            job = self.backup_service.create_backup_job(
                source_path, targets, template.algorithm
            )
            result["backup"] = self.backup_service.execute_backup(
                job,
                progress_callback=progress_callback,
                project_id=project_id,
                verify=template.verify_after_copy,
            )

        if check_cancel():
            return result

        if do_import:
            result["import"] = self.import_service.import_assets(
                project_id=project_id,
                file_paths=paths,
                compute_checksum=True,
                read_metadata=True,
                copy_to_workspace=False,
                cancel_check=cancel_check,
                progress_callback=progress_callback,
            )
            if do_backup and result["backup"] is not None:
                for target in result["backup"].targets:
                    if target.status.value == "completed":
                        self.db_service.add_backup_location_to_assets(
                            paths, target.path, project_id=project_id
                        )
        return result
