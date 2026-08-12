"""安全拷贝与多重备份服务"""
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Callable, Dict
from datetime import datetime

from DITWorkstation.App import config
from DITWorkstation.Models import (
    BackupJob, BackupTarget, BackupStatus, CopyStatus,
    CopyTask, ChecksumAlgorithm
)
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Utils import logger, get_checksum_service


class BackupService:
    """安全拷贝与多重备份服务"""

    def __init__(self, db_service=None, checksum_service: Optional[ChecksumService] = None):
        """注入 db_service 后，备份完成会持久化 job + 回写 asset.backup_locations。

        checksum_service 可注入；不传时取全局单例，与 MediaImportService 复用同一缓存，
        避免同一文件在「导入」和「备份」阶段被重复哈希。
        """
        self.checksum_service = checksum_service or get_checksum_service()
        self.db_service = db_service  # 可选，None 时退化为旧行为（仅文件系统操作）
        self._cancelled = False
        self._cancelled_lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._executor_lock = threading.Lock()

    def scan_source(self, source_path: str) -> List[Dict]:
        """
        扫描源目录，获取文件列表

        Returns:
            文件信息列表 [{path, size, name, relative}]
        """
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"源路径不存在: {source_path}")

        files = []
        if source.is_file():
            files.append({
                "path": str(source),
                "size": source.stat().st_size,
                "name": source.name,
                "relative": source.name
            })
        else:
            for item in source.rglob("*"):
                if item.is_file() and not item.name.startswith("."):
                    files.append({
                        "path": str(item),
                        "size": item.stat().st_size,
                        "name": item.name,
                        "relative": str(item.relative_to(source))
                    })
        return files

    def create_backup_job(
        self,
        source_path: str,
        target_paths: List[str],
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64
    ) -> BackupJob:
        """创建备份作业"""
        job_id = str(uuid.uuid4())[:8]
        files = self.scan_source(source_path)
        total_bytes = sum(f["size"] for f in files)

        targets = []
        for tp in target_paths:
            target_name = Path(tp).name or tp
            targets.append(BackupTarget(
                path=tp,
                name=target_name,
                total_files=len(files),
                total_bytes=total_bytes
            ))

        job = BackupJob(
            job_id=job_id,
            source_path=source_path,
            targets=targets,
            algorithm=algorithm,
            total_files=len(files),
            total_bytes=total_bytes
        )
        job.__dict__['_files_cache'] = files
        return job

    def execute_backup(
        self,
        job: BackupJob,
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
        file_completed_callback: Optional[Callable[[str, CopyTask], None]] = None,
        project_id: Optional[str] = None,
        verify: Optional[bool] = None
    ) -> BackupJob:
        """
        执行备份作业（并行多目标）

        Args:
            job: 备份作业
            progress_callback: 进度回调 (target_path, progress, status_msg)
            file_completed_callback: 单文件完成回调
            project_id: 关联项目 ID。提供且 db_service 已注入时，
                        备份完成后会持久化 job + 把每个 target_path 回写到
                        匹配 asset 的 backup_locations 字段。
            verify: 拷贝后是否验证目标校验和；None 时取 config.verify_after_copy

        Returns:
            更新后的备份作业
        """
        self._set_cancelled(False)
        job.status = BackupStatus.RUNNING
        source = Path(job.source_path)

        files = getattr(job, '_files_cache', None)
        if files is None:
            logger.warning("文件缓存未找到，重新扫描源目录")
            files = self.scan_source(job.source_path)

        target_count = len(job.targets)
        if target_count == 0:
            job.status = BackupStatus.FAILED
            return job

        with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ThreadPoolExecutor(max_workers=target_count)

        futures = {}
        for target in job.targets:
            future = self._executor.submit(
                self._copy_to_target,
                source, files, target, job.algorithm,
                progress_callback, file_completed_callback, verify
            )
            futures[future] = target

        for future in as_completed(futures):
            target = futures[future]
            try:
                future.result()
            except Exception as e:
                target.status = CopyStatus.FAILED
                target.error_message = str(e)
                logger.error(f"备份目标失败 {target.path}: {e}")

        with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=False)
                self._executor = None

        completed = all(t.status == CopyStatus.COMPLETED for t in job.targets)
        any_failed = any(t.status == CopyStatus.FAILED for t in job.targets)

        if completed:
            job.status = BackupStatus.COMPLETED
        elif any_failed and any(t.status == CopyStatus.COMPLETED for t in job.targets):
            job.status = BackupStatus.PARTIAL
        else:
            job.status = BackupStatus.FAILED

        job.completed_at = datetime.now()

        # 数据闭环：持久化 job + 回写 asset.backup_locations
        if self.db_service is not None:
            try:
                self.db_service.save_backup_job(job, project_id=project_id)
                # 仅对成功/部分成功的目标回写 backup_locations
                file_paths = [f["path"] for f in files]
                for t in job.targets:
                    if t.status == CopyStatus.COMPLETED:
                        self.db_service.add_backup_location_to_assets(
                            file_paths, t.path, project_id=project_id
                        )
            except Exception as e:
                logger.error(f"备份结果回写 DB 失败（不影响文件备份）: {e}", exc_info=True)

        return job

    def _copy_to_target(
        self,
        source: Path,
        files: List[Dict],
        target: BackupTarget,
        algorithm: ChecksumAlgorithm,
        progress_callback: Optional[Callable],
        file_completed_callback: Optional[Callable],
        verify: Optional[bool] = None
    ):
        """拷贝文件到单个目标。

        单个文件失败不中断整个目标：记录错误后继续拷贝其余文件，
        结束时目标状态为 FAILED（部分文件成功），由 execute_backup 汇总
        为 BackupStatus.PARTIAL / FAILED。
        """
        if verify is None:
            verify = config.verify_after_copy
        target_path = Path(target.path)
        target_path.mkdir(parents=True, exist_ok=True)
        target.status = CopyStatus.COPYING

        file_count = len(files)
        failed_files = []
        for i, file_info in enumerate(files):
            if self._is_cancelled():
                target.status = CopyStatus.CANCELLED
                return

            src_file = Path(file_info["path"])
            relative = file_info.get("relative", src_file.name)
            dest_file = target_path / relative

            dest_file.parent.mkdir(parents=True, exist_ok=True)

            try:
                start_time = time.time()
                # 边拷贝边计算源校验和：单次读盘，替代「先哈希再拷贝」的两次读盘
                source_checksum = self.checksum_service.copy_file_with_checksum(
                    str(src_file), str(dest_file), algorithm,
                    lambda p: self._report_progress(
                        progress_callback, target.path,
                        (i + p) / file_count, f"拷贝: {src_file.name}"
                    ),
                    cancel_check=self._is_cancelled,
                )
                checksum_time = time.time()

                if verify:
                    self._report_progress(
                        progress_callback, target.path,
                        (i + 0.9) / file_count, f"验证: {src_file.name}"
                    )
                    verified = self.checksum_service.verify_file(
                        str(dest_file), source_checksum.hash_value, algorithm,
                        cancel_check=self._is_cancelled,
                    )
                    if not verified:
                        raise IOError(f"校验和验证失败: {src_file.name}")

                target.completed_files += 1
                target.copied_bytes += file_info["size"]

                elapsed = checksum_time - start_time
                speed = calculate_speed(elapsed, file_info["size"])

                if file_completed_callback:
                    task = CopyTask(
                        source_path=str(src_file),
                        dest_path=str(dest_file),
                        file_size=file_info["size"],
                        status=CopyStatus.COMPLETED,
                        progress=1.0,
                        source_checksum=source_checksum.hash_value,
                        speed_mbps=speed
                    )
                    file_completed_callback(target.path, task)

            except Exception as e:
                failed_files.append(f"{src_file.name}: {str(e)}")
                logger.error(f"文件拷贝失败 {src_file.name}: {e}")
                # 清理残缺/损坏的目标文件，避免残留半成品影响后续复检
                try:
                    dest_file.unlink(missing_ok=True)
                except OSError:
                    pass

        if self._is_cancelled():
            target.status = CopyStatus.CANCELLED
            return
        if failed_files:
            target.status = CopyStatus.FAILED
            shown = "；".join(failed_files[:20])
            if len(failed_files) > 20:
                shown += f"…（共 {len(failed_files)} 个文件失败）"
            target.error_message = shown
            logger.error(f"备份目标部分失败 {target.path}: {len(failed_files)} 个文件")
            return
        target.status = CopyStatus.COMPLETED
        target.verified = True
        self._report_progress(progress_callback, target.path, 1.0, "完成")

    def _report_progress(
        self,
        callback: Optional[Callable],
        target_path: str,
        progress: float,
        message: str
    ):
        """报告进度"""
        if callback:
            callback(target_path, progress, message)

    def _is_cancelled(self) -> bool:
        """线程安全检查取消状态"""
        with self._cancelled_lock:
            return self._cancelled

    def _set_cancelled(self, value: bool):
        """线程安全设置取消状态"""
        with self._cancelled_lock:
            self._cancelled = value

    def cancel(self):
        """取消备份"""
        self._set_cancelled(True)
        with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)

    def generate_mhl_report(self, job: BackupJob) -> str:
        """
        生成ASC MHL格式的哈希值列表

        Returns:
            MHL格式字符串
        """
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<hashlist version="2.0">',
            f'  <creatorinfo>',
            f'    <tool>DIT工作站 v1.0</tool>',
            f'    <creationdate>{datetime.now().isoformat()}</creationdate>',
            f'  </creatorinfo>',
        ]

        files = getattr(job, '_files_cache', None)
        if files is None:
            files = self.scan_source(job.source_path)

        source = Path(job.source_path)
        for file_info in files:
            src_file = Path(file_info["path"])
            relative = file_info.get("relative", src_file.name)
            checksum = self.checksum_service.compute_file_checksum(
                str(src_file), job.algorithm
            )
            lines.append(f'  <hash>')
            lines.append(f'    <path>{relative}</path>')
            lines.append(f'    <size>{file_info["size"]}</size>')
            lines.append(f'    <hashformat>{job.algorithm.value}</hashformat>')
            lines.append(f'    <hashvalue>{checksum.hash_value}</hashvalue>')
            lines.append(f'  </hash>')

        lines.append('</hashlist>')
        return "\n".join(lines)


def calculate_speed(elapsed_seconds: float, bytes_transferred: int) -> float:
    """计算传输速度（Mbps）"""
    if elapsed_seconds <= 0:
        return 0.0
    return (bytes_transferred / 1024 / 1024) / elapsed_seconds
