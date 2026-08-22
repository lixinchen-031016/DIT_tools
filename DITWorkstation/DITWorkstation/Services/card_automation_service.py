"""相机卡自动化 SOP：按配置串联导入、备份、整理和交付步骤。"""

from collections.abc import Callable
from pathlib import Path

from DITWorkstation.Models import BackupTemplate, RenameRule
from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Services.raw_extraction_service import RawExtractionService
from DITWorkstation.Services.rename_service import RenameService
from DITWorkstation.Utils import get_report_service

STEP_IMPORT = "import"
STEP_BACKUP = "backup"
STEP_RAW_EXTRACT = "raw_extract"
STEP_RENAME = "rename"
STEP_REPORT = "report"
SOP_STEPS = (STEP_IMPORT, STEP_BACKUP, STEP_RAW_EXTRACT, STEP_RENAME, STEP_REPORT)


class CardAutomationService:
    """把存储卡检测后的自动操作放在一个可测试的后台任务函数中。"""

    def __init__(self, db_service):
        self.db_service = db_service
        self.backup_service = BackupService(db_service=db_service)
        self.import_service = MediaImportService(db_service=db_service)
        self.raw_service = RawExtractionService()
        self.rename_service = RenameService()
        self.report_service = get_report_service()

    def execute(
        self,
        source_path: str,
        project_id: str,
        template: BackupTemplate | None = None,
        do_import: bool = True,
        do_backup: bool = False,
        steps: list[str] | None = None,
        raw_config: dict | None = None,
        rename_config: dict | None = None,
        report_path: str | None = None,
        report_service=None,
        progress_callback: Callable[[str, float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict:
        """执行可配置 SOP 链，旧的 ``do_import/do_backup`` 参数继续兼容。

        ``raw_config`` 支持 ``jpg_folder``、``raw_folder``、``output_folder``、
        ``verify`` 和 ``algorithm``；``rename_config`` 支持 ``files`` 与
        ``rule``（``RenameRule`` 或同名字段字典）。失败异常直接终止后续步骤，
        由 WorkerThread/UI 统一告警。
        """
        if not project_id:
            raise ValueError("自动化配置未选择项目")

        if steps is None:
            # 保持历史行为：旧入口先备份，再导入，并在最后回写备份位置。
            steps = []
            if do_backup:
                steps.append(STEP_BACKUP)
            if do_import:
                steps.append(STEP_IMPORT)
        steps = list(dict.fromkeys(steps))
        unknown = [step for step in steps if step not in SOP_STEPS]
        if unknown:
            raise ValueError(f"不支持的相机卡自动化步骤: {', '.join(unknown)}")
        if not steps:
            raise ValueError("至少启用一个相机卡自动化步骤")
        if STEP_BACKUP in steps and template is None:
            raise ValueError("自动备份未选择备份方案")

        files = self.backup_service.scan_source(source_path)
        paths = [item["path"] for item in files]
        result = {
            "source_path": source_path,
            "files": len(paths),
            "steps": steps,
            "import": None,
            "backup": None,
            "raw_extract": None,
            "rename": None,
            "report": None,
            "cancelled": False,
        }

        def check_cancel():
            return bool(cancel_check and cancel_check())

        def emit(step: str, progress: float, message: str):
            if progress_callback:
                progress_callback(step, max(0.0, min(1.0, progress)), message)

        for step in steps:
            if check_cancel():
                result["cancelled"] = True
                result["cancelled_step"] = step
                break

            if step == STEP_BACKUP:
                if template is None:
                    raise ValueError("自动备份未选择备份方案")
                targets = self.backup_service.resolve_template_targets(
                    template.target_paths, source_path
                )
                job = self.backup_service.create_backup_job(
                    source_path, targets, template.algorithm
                )
                result["backup"] = self.backup_service.execute_backup(
                    job,
                    progress_callback=lambda target, progress, message: emit(
                        STEP_BACKUP, progress, message
                    ),
                    project_id=project_id,
                    verify=template.verify_after_copy,
                )
            elif step == STEP_IMPORT:
                result["import"] = self.import_service.import_assets(
                    project_id=project_id,
                    file_paths=paths,
                    compute_checksum=True,
                    read_metadata=True,
                    copy_to_workspace=False,
                    cancel_check=cancel_check,
                    progress_callback=lambda target, progress, message: emit(
                        STEP_IMPORT, progress, message
                    ),
                )
            elif step == STEP_RAW_EXTRACT:
                options = dict(raw_config or {})
                output_folder = options.pop("output_folder", "")
                if not output_folder:
                    raise ValueError("RAW 提取步骤缺少 output_folder")
                jpg_folder = options.pop("jpg_folder", source_path)
                raw_folder = options.pop("raw_folder", source_path)
                verify = options.pop("verify", True)
                algorithm = options.pop("algorithm", None)
                if algorithm is None:
                    matches = self.raw_service.scan_jpg_folder(jpg_folder)
                    raw_index = self.raw_service.scan_raw_folder(raw_folder)
                    matches = self.raw_service.match_raw_files(matches, raw_index)
                    result["raw_extract"] = (
                        self.raw_service.extract_raw_files_streaming(
                            matches,
                            output_folder,
                            verify=verify,
                            progress_callback=lambda current, total, message: emit(
                                STEP_RAW_EXTRACT,
                                current / total if total else 1.0,
                                message,
                            ),
                        )
                    )
                else:
                    from DITWorkstation.Models import ChecksumAlgorithm

                    result["raw_extract"] = self.raw_service.extract_raw_files(
                        jpg_folder,
                        raw_folder,
                        output_folder,
                        verify=verify,
                        algorithm=ChecksumAlgorithm(algorithm),
                        progress_callback=lambda current, total, message: emit(
                            STEP_RAW_EXTRACT, current / total if total else 1.0, message
                        ),
                    )
            elif step == STEP_RENAME:
                options = dict(rename_config or {})
                rename_files = options.get("files") or paths
                raw_rule = options.get("rule") or {}
                rule = (
                    raw_rule
                    if isinstance(raw_rule, RenameRule)
                    else RenameRule(**raw_rule)
                )
                result["rename"] = self.rename_service.execute_rename(
                    rename_files,
                    rule,
                    progress_callback=lambda current, total, message: emit(
                        STEP_RENAME, current / total if total else 1.0, message
                    ),
                )
                synced = 0
                for old_path, new_path in result["rename"]:
                    if self.db_service.update_asset_path_by_old_path(
                        old_path, new_path, Path(new_path).name
                    ):
                        synced += 1
                result["rename_synced"] = synced
            elif step == STEP_REPORT:
                project = self.db_service.get_project(project_id)
                result["report"] = (
                    report_service or self.report_service
                ).generate_backup_report(
                    project,
                    self.db_service.get_backup_jobs(project_id),
                    report_path,
                )

        backup = result.get("backup")
        if backup is not None and result.get("import") is not None:
            for target in backup.targets:
                if target.status.value == "completed":
                    self.db_service.add_backup_location_to_assets(
                        paths, target.path, project_id=project_id
                    )
        return result
