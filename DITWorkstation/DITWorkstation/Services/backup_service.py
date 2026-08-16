"""安全拷贝与多重备份服务"""
import shutil
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
from DITWorkstation.Utils import logger, get_checksum_service, scan_files


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
        self._persist_lock = threading.Lock()

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
            stat = source.stat()
            files.append({
                "path": str(source),
                "size": stat.st_size,
                "name": source.name,
                "relative": source.name,
                "mtime_ns": stat.st_mtime_ns,
            })
        else:
            for item in scan_files(source):
                stat = item.stat()
                files.append({
                    "path": str(item),
                    "size": stat.st_size,
                    "name": item.name,
                    "relative": str(item.relative_to(source)),
                    "mtime_ns": stat.st_mtime_ns,
                })
        return files

    @staticmethod
    def resolve_template_targets(target_paths: List[str], source_path: str) -> List[str]:
        """展开备份模板目标路径中的安全占位符。"""
        source_name = Path(source_path).name or "source"
        return [str(path).replace("{source_name}", source_name) for path in target_paths]

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

    def create_incremental_backup_job(
        self,
        source_path: str,
        target_paths: List[str],
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        project_id: Optional[str] = None,
    ) -> BackupJob:
        """创建增量备份作业。

        与最近一次覆盖同一来源及全部目标的成功备份快照比对 size/mtime。
        不变文件仍会在执行时确认目标文件存在且大小一致，避免错误跳过被
        人为删除的目标文件；它们不会再次复制或读取源文件计算哈希。
        """
        job = self.create_backup_job(source_path, target_paths, algorithm)
        if self.db_service is None:
            job.__dict__["_incremental"] = True
            return job

        requested = set(target_paths)
        previous = None
        for candidate in self.db_service.get_backup_jobs(project_id):
            if candidate.get("source_path") != source_path:
                continue
            completed_targets = {
                target.get("path") for target in candidate.get("targets", [])
                if target.get("status") == CopyStatus.COMPLETED.value
            }
            if requested.issubset(completed_targets) and candidate.get("file_snapshots"):
                previous = candidate
                break

        previous_by_relative = {
            item.get("relative"): item for item in (previous or {}).get("file_snapshots", [])
            if item.get("relative")
        }
        unchanged = 0
        for item in job.__dict__["_files_cache"]:
            old = previous_by_relative.get(item["relative"])
            if old and old.get("size") == item["size"] and old.get("mtime_ns") == item["mtime_ns"]:
                item["unchanged"] = True
                item["checksum"] = old.get("checksum", "")
                unchanged += 1
        job.__dict__["_incremental"] = True
        job.__dict__["_incremental_base_job_id"] = (previous or {}).get("job_id", "")
        job.__dict__["_unchanged_files"] = unchanged
        return job

    def check_target_space(
        self,
        target_paths: List[str],
        total_bytes: int
    ) -> List[Dict]:
        """备份前检查各目标磁盘剩余空间是否足够。

        Args:
            target_paths: 备份目标目录列表
            total_bytes: 需要的空间（源文件总大小）

        Returns:
            每个目标一项：{"path", "free", "needed", "sufficient", "error"}
            - sufficient: free >= needed
            - error: 目录不可达/磁盘查询失败时非空
        """
        results = []
        for tp in target_paths:
            try:
                usage = shutil.disk_usage(tp)
            except Exception as e:
                results.append({
                    "path": tp, "free": 0, "needed": total_bytes,
                    "sufficient": False, "error": str(e),
                })
                continue
            results.append({
                "path": tp, "free": usage.free, "needed": total_bytes,
                "sufficient": usage.free >= total_bytes, "error": "",
            })
        return results

    def verify_backup(
        self,
        project_id: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> Dict:
        """独立校验项目已有备份的完整性。

        遍历项目素材的 backup_locations，借助备份作业记录推导每个目标
        目录下的文件相对路径，逐一检查文件存在性；素材已有校验和时再
        比对哈希，检测备份盘上的位错误或文件丢失。

        Args:
            project_id: 目标项目
            progress_callback: (current, total, message)
            cancel_check: 返回 True 时中止校验

        Returns:
            {"checked", "matched", "missing", "mismatch", "unhashable",
             "errors": [str]}
        """
        if self.db_service is None:
            raise ValueError("verify_backup 需要注入 db_service")

        self._set_cancelled(False)

        assets = (
            asset for asset in self.db_service.iter_project_assets(project_id)
            if asset.backup_locations
        )
        jobs = self.db_service.get_backup_jobs(project_id)

        # 目标目录 -> 备份作业源根 映射，用于推导目标文件相对路径
        loc_to_source = {}
        for j in jobs:
            for t in j["targets"]:
                loc_to_source.setdefault(t["path"], j["source_path"])

        stats = {
            "checked": 0, "matched": 0, "missing": 0,
            "mismatch": 0, "unhashable": 0, "errors": [],
        }
        total = sum(1 for asset in self.db_service.iter_project_assets(project_id) if asset.backup_locations)
        for i, asset in enumerate(assets):
            if cancel_check and cancel_check():
                break
            if progress_callback:
                progress_callback(i, total, f"校验: {asset.file_name}")

            for loc in asset.backup_locations:
                dest = self._backup_dest_for_asset(asset, loc, loc_to_source)
                stats["checked"] += 1
                if dest is None:
                    stats["missing"] += 1
                    continue

                dest_path = Path(dest)
                if not dest_path.exists():
                    stats["missing"] += 1
                    continue
                if not asset.checksum_value:
                    stats["unhashable"] += 1
                    continue

                try:
                    algo = ChecksumAlgorithm(asset.checksum_algorithm)
                except ValueError:
                    algo = ChecksumAlgorithm.XXHASH64
                try:
                    cached = self.checksum_service.compute_file_checksum(
                        str(dest_path), algo, cancel_check=cancel_check
                    )
                except InterruptedError:
                    break
                except Exception as e:
                    stats["errors"].append(f"{asset.file_name}: {e}")
                    continue

                if cached.hash_value == asset.checksum_value:
                    stats["matched"] += 1
                else:
                    stats["mismatch"] += 1

        if progress_callback:
            progress_callback(total, total, "完成")
        self.db_service.record_operation(
            "备份校验",
            f"检查 {stats['checked']}，一致 {stats['matched']}，"
            f"缺失 {stats['missing']}，不一致 {stats['mismatch']}",
            project_id=project_id,
            status="success" if not (stats["missing"] or stats["mismatch"] or stats["errors"]) else "error",
            object_type="project",
            object_id=project_id,
        )
        return stats

    def is_cancelled(self) -> bool:
        """线程安全查询取消状态（供独立校验等任务复用取消机制）。"""
        return self._is_cancelled()

    def _backup_dest_for_asset(
        self,
        asset,
        loc: str,
        loc_to_source: Dict
    ) -> Optional[str]:
        """推导素材在某个备份目标目录下的目标文件路径。

        优先通过备份作业的源根计算相对路径；源根不可用或素材不在其下时，
        在目标目录内按文件名递归查找兜底。
        """
        src_root = loc_to_source.get(loc)
        if src_root:
            try:
                # 统一解析两侧路径（macOS /var → /private/var），避免表示形式
                # 不一致导致 relative_to 失败
                try:
                    root = Path(src_root).resolve()
                except OSError:
                    root = Path(src_root)
                rel = Path(asset.file_path).relative_to(root)
                return str(Path(loc) / rel)
            except ValueError:
                pass
        try:
            found = next(Path(loc).rglob(Path(asset.file_name).name), None)
            if found:
                return str(found)
        except OSError:
            pass
        return None

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
        job.completed_at = None
        source = Path(job.source_path)

        files = getattr(job, '_files_cache', None)
        if files is None:
            logger.warning("文件缓存未找到，重新扫描源目录")
            files = self.scan_source(job.source_path)

        target_count = len(job.targets)
        if target_count == 0:
            job.status = BackupStatus.FAILED
            self._persist_snapshot(job, project_id)
            return job

        for target in job.targets:
            target.pending_files = [
                f.get("relative", Path(f["path"]).name) for f in files
            ]
            target.failed_files = []
            target.status = CopyStatus.PENDING
            target.error_message = ""
        self._persist_snapshot(job, project_id)

        with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ThreadPoolExecutor(max_workers=target_count)

        futures = {}
        for target in job.targets:
            future = self._executor.submit(
                self._copy_to_target,
                source, files, target, job.algorithm,
                progress_callback, file_completed_callback, verify,
                lambda: self._persist_snapshot(job, project_id)
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

        return self._finalize_and_persist(job, files, project_id)

    def load_job(self, job_id: str) -> Optional[BackupJob]:
        """从数据库恢复备份作业（含目标状态与失败文件列表），供断点续传/重试。

        Returns:
            恢复的 BackupJob；作业不存在或未注入 db_service 时返回 None
        """
        if self.db_service is None:
            return None
        raw = self.db_service.get_backup_job(job_id)
        if raw is None:
            return None
        try:
            job = BackupJob(
                job_id=raw["job_id"],
                source_path=raw["source_path"],
                algorithm=ChecksumAlgorithm(raw["algorithm"]),
                status=BackupStatus(raw["status"]),
                total_files=raw["total_files"],
                total_bytes=raw["total_bytes"],
                created_at=raw["created_at"],
                completed_at=raw["completed_at"],
            )
        except ValueError:
            job = BackupJob(job_id=raw["job_id"], source_path=raw["source_path"])

        failed_by_target = raw.get("failed_files_by_target", {})
        job.targets = []
        for t in raw["targets"]:
            try:
                status = CopyStatus(t["status"])
            except ValueError:
                status = CopyStatus.FAILED
            job.targets.append(BackupTarget(
                path=t["path"],
                name=t.get("name", ""),
                status=status,
                total_files=t.get("total_files", raw["total_files"]),
                completed_files=t.get("completed_files", 0),
                total_bytes=t.get("total_bytes", raw["total_bytes"]),
                copied_bytes=t.get("copied_bytes", 0),
                verified=t.get("verified", False),
                error_message=t.get("error_message", ""),
                failed_files=list(
                    failed_by_target.get(t["path"], t.get("failed_files", []))
                ),
                pending_files=list(t.get("pending_files", [])),
            ))
        job.__dict__["_files_cache"] = list(raw.get("file_snapshots", []))
        return job

    def retry_failed_files(
        self,
        job_id: str,
        project_id: Optional[str] = None,
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
        file_completed_callback: Optional[Callable[[str, CopyTask], None]] = None,
        verify: Optional[bool] = None
    ) -> Optional[BackupJob]:
        """重试备份作业中失败的文件（断点续传语义）。

        从数据库恢复作业，仅对每个目标重新拷贝上次失败（failed_files）的文件；
        目标中已存在且校验一致的文件自动跳过。重试结果写回原作业记录
        （INSERT OR REPLACE 保留同一 job_id）。

        Args:
            job_id: 备份作业 ID
            project_id: 关联项目 ID（持久化与回写 backup_locations 使用）
            progress_callback: (target_path, progress, status_msg)
            file_completed_callback: 单文件完成回调
            verify: 拷贝后是否验证目标校验和；None 时取 config.verify_after_copy

        Returns:
            更新后的作业；作业不存在返回 None
        """
        job = self.load_job(job_id)
        if job is None:
            logger.warning(f"重试失败文件：备份作业不存在 {job_id}")
            return None

        retry_targets = [t for t in job.targets if t.failed_files]
        if not retry_targets:
            logger.info(f"重试失败文件：作业 {job_id} 没有失败文件记录，无需重试")
            return job

        self._set_cancelled(False)
        job.status = BackupStatus.RUNNING
        job.completed_at = None
        # 重试应以启动时持久化的快照为准，避免因介质慢/已拔出而重扫整个源目录。
        files = list(getattr(job, "_files_cache", []) or [])
        if not files:
            logger.warning("重试作业缺少源文件快照，回退为全量扫描")
            files = self.scan_source(job.source_path)
            job.__dict__["_files_cache"] = files
        file_by_rel = {f.get("relative"): f for f in files if f.get("relative")}

        with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ThreadPoolExecutor(max_workers=len(retry_targets))

        futures = {}
        for target in retry_targets:
            # 重置目标状态；total_files/total_bytes 保留历史值用于统计
            target.status = CopyStatus.COPYING
            failed_files, missing = [], []
            for relative in target.failed_files:
                item = file_by_rel.get(relative)
                if item is None:
                    missing.append(relative)
                    continue
                try:
                    stat = Path(item["path"]).stat()
                    if stat.st_size != item.get("size") or (
                        item.get("mtime_ns") is not None and stat.st_mtime_ns != item["mtime_ns"]
                    ):
                        missing.append(relative)
                        continue
                except OSError:
                    missing.append(relative)
                    continue
                failed_files.append(item)
            target.failed_files = list(missing)
            target.pending_files = [
                f.get("relative", Path(f["path"]).name) for f in failed_files
            ]
            target.error_message = ""
            if missing:
                target.error_message = (
                    f"源文件不可用或已变化 {len(missing)} 个: " + "、".join(missing[:10])
                )
            if not failed_files:
                # 失败文件在源目录中全部缺失，无法重试
                if missing:
                    target.status = CopyStatus.FAILED
                continue
            future = self._executor.submit(
                self._copy_to_target,
                Path(job.source_path), failed_files, target, job.algorithm,
                progress_callback, file_completed_callback, verify,
                lambda: self._persist_snapshot(job, project_id)
            )
            futures[future] = target

        for future in as_completed(futures):
            target = futures[future]
            try:
                future.result()
            except Exception as e:
                target.status = CopyStatus.FAILED
                target.error_message = str(e)
                logger.error(f"重试目标失败 {target.path}: {e}")

        with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=False)
                self._executor = None

        return self._finalize_and_persist(job, files, project_id, operation_event="备份重试")

    def _persist_snapshot(self, job: BackupJob, project_id: Optional[str]):
        """持久化运行中任务快照；失败不阻断文件拷贝。"""
        if self.db_service is None:
            return
        try:
            with self._persist_lock:
                self.db_service.save_backup_job(job, project_id=project_id)
        except Exception as exc:
            logger.error(f"备份运行快照持久化失败（不影响文件备份）: {exc}")

    def _finalize_and_persist(
        self,
        job: BackupJob,
        files: List[Dict],
        project_id: Optional[str],
        operation_event: str = "数据备份"
    ) -> BackupJob:
        """汇总作业状态、持久化并回写 asset.backup_locations（execute/retry 共用）。"""
        # 取消/进程中断留下的 pending 文件必须进入重试列表，不能被静默丢弃。
        for target in job.targets:
            if target.pending_files:
                target.failed_files = list(dict.fromkeys(
                    list(target.failed_files) + list(target.pending_files)
                ))
                target.pending_files = []
                if target.status == CopyStatus.CANCELLED:
                    target.status = CopyStatus.FAILED
                if not target.error_message:
                    target.error_message = "任务中断，未完成文件已转入重试队列"
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
                # 操作审计：记录备份结果（失败不影响备份本身）
                self.db_service.record_operation(
                    operation_event,
                    f"{job.status.value}：{len(files)} 个文件，"
                    f"{len(job.targets)} 个目标",
                    project_id=project_id,
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
        verify: Optional[bool] = None,
        persist_callback: Optional[Callable[[], None]] = None,
    ):
        """拷贝文件到单个目标。

        单个文件失败不中断整个目标：记录错误后继续拷贝其余文件，
        结束时目标状态为 FAILED（部分文件成功），由 execute_backup 汇总
        为 BackupStatus.PARTIAL / FAILED。
        """
        if verify is None:
            verify = config.verify_after_copy
        target_path = Path(target.path)
        try:
            target_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            target.status = CopyStatus.FAILED
            target.error_message = f"无法创建目标目录: {e}"
            return
        target.status = CopyStatus.COPYING

        file_count = len(files)
        failed_files = []     # 失败文件的相对路径（完整列表，供重试）
        failed_errors = {}    # relative -> error message
        for i, file_info in enumerate(files):
            if self._is_cancelled():
                target.status = CopyStatus.CANCELLED
                return

            src_file = Path(file_info["path"])
            relative = file_info.get("relative", src_file.name)
            dest_file = target_path / relative

            try:
                dest_file.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                failed_files.append(relative)
                failed_errors[relative] = f"无法创建目录: {e}"
                if relative in target.pending_files:
                    target.pending_files.remove(relative)
                target.failed_files = list(failed_files)
                if persist_callback:
                    persist_callback()
                continue

            try:
                # 断点续传：目标已存在且大小一致时，校验一致则跳过拷贝。
                # 源校验和命中导入/首次备份缓存，几乎免费；目标只需读一次哈希。
                try:
                    dest_size = dest_file.stat().st_size
                except OSError:
                    dest_size = None
                if dest_size == file_info["size"]:
                    # 增量作业已用上一份快照确认源文件未变化；这里只确认目标仍
                    # 存在且大小一致，避免每次重复备份都重新读取源/目标计算哈希。
                    if file_info.get("unchanged"):
                        target.completed_files += 1
                        target.copied_bytes += file_info["size"]
                        if relative in target.pending_files:
                            target.pending_files.remove(relative)
                        if persist_callback:
                            persist_callback()
                        if file_completed_callback:
                            file_completed_callback(target.path, CopyTask(
                                source_path=str(src_file), dest_path=str(dest_file),
                                file_size=file_info["size"], status=CopyStatus.COMPLETED,
                                progress=1.0, source_checksum=file_info.get("checksum", ""),
                                speed_mbps=0.0,
                            ))
                        continue
                    try:
                        src_hash = self.checksum_service.compute_file_checksum(
                            str(src_file), algorithm,
                            cancel_check=self._is_cancelled,
                        )
                        if self.checksum_service.verify_file(
                            str(dest_file), src_hash.hash_value, algorithm,
                            cancel_check=self._is_cancelled,
                        ):
                            target.completed_files += 1
                            target.copied_bytes += file_info["size"]
                            if relative in target.pending_files:
                                target.pending_files.remove(relative)
                            if persist_callback:
                                persist_callback()
                            if file_completed_callback:
                                task = CopyTask(
                                    source_path=str(src_file),
                                    dest_path=str(dest_file),
                                    file_size=file_info["size"],
                                    status=CopyStatus.COMPLETED,
                                    progress=1.0,
                                    source_checksum=src_hash.hash_value,
                                    speed_mbps=0.0,
                                )
                                file_completed_callback(target.path, task)
                            continue
                    except InterruptedError:
                        target.status = CopyStatus.CANCELLED
                        return
                    except Exception as e:
                        logger.warning(f"断点校验失败，重新拷贝 {src_file.name}: {e}")

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
                # 该值随任务快照持久化，供未来的增量判定及交付报告使用。
                file_info["checksum"] = source_checksum.hash_value
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
                if relative in target.pending_files:
                    target.pending_files.remove(relative)
                if persist_callback:
                    persist_callback()

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

            except InterruptedError:
                # 用户取消：不记为失败文件
                target.status = CopyStatus.CANCELLED
                return
            except Exception as e:
                failed_files.append(relative)
                failed_errors[relative] = str(e)
                if relative in target.pending_files:
                    target.pending_files.remove(relative)
                target.failed_files = list(failed_files)
                if persist_callback:
                    persist_callback()
                logger.error(f"文件拷贝失败 {relative}: {e}")
                # 清理残缺/损坏的目标文件，避免残留半成品影响后续复检
                try:
                    dest_file.unlink(missing_ok=True)
                except OSError:
                    pass

        if self._is_cancelled():
            target.status = CopyStatus.CANCELLED
            return
        target.failed_files = failed_files
        if persist_callback:
            persist_callback()
        if failed_files:
            target.status = CopyStatus.FAILED
            shown = "；".join(
                f"{Path(r).name}: {failed_errors.get(r, '')}" for r in failed_files[:20]
            )
            if len(failed_files) > 20:
                shown += f"…（共 {len(failed_files)} 个文件失败）"
            target.error_message = shown
            logger.error(
                f"备份目标部分失败 {target.path}: {len(failed_files)} 个文件"
            )
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
