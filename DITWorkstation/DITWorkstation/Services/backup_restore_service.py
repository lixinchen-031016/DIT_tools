"""从备份卷回拷素材服务。

DIT 片场常见收尾动作：把已备份到存储盘上的素材按校验和/相对路径
复制回工作盘（或交付盘）。本服务是「数据备份」的逆向操作：

- 复用备份作业推导「每个素材在备份目标下的相对路径」；
- 可选校验和比对（目标副本与记录一致才算成功）；
- 冲突策略：目标已存在同名文件且校验一致 → 跳过；不一致 → 记入 conflicts；
- 全程迭代处理，不把全部素材加载进内存。
"""

import shutil
from pathlib import Path

from DITWorkstation.Models import ChecksumAlgorithm
from DITWorkstation.Utils import get_checksum_service, logger


def _safe_relative(rel: str) -> str | None:
    """规范化快照相对路径，防止 .. 逃逸目标目录。

    返回 None 表示路径包含越界段（拒绝写入）。
    """
    raw = str(rel).replace(chr(92), "/")
    parts = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            return None
        parts.append(seg)
    return "/".join(parts)


class BackupRestoreService:
    """从备份目标恢复素材到指定目录。"""

    def __init__(self, db_service, backup_service=None, checksum_service=None):
        self.db_service = db_service
        # 懒注入：避免 import 循环
        if backup_service is None:
            from DITWorkstation.Services.backup_service import BackupService

            backup_service = BackupService(db_service=db_service)
        self.backup_service = backup_service
        self.checksum_service = checksum_service or get_checksum_service()

    def restore_project(
        self,
        project_id: str,
        destination: str,
        *,
        source_locations: list[str] | None = None,
        verify: bool = True,
        progress_callback=None,
        cancel_check=None,
    ) -> dict:
        """把项目内所有有备份记录的素材从指定/全部备份位置恢复到 destination。

        Args:
            project_id: 目标项目
            destination: 恢复目标目录（相对路径结构会保留）
            source_locations: 只从这些备份位置恢复（空 = 全部 backup_locations）
            verify: 是否校验和比对（推荐开启）
            progress_callback: (current, total, message)
            cancel_check: 返回 True 时中止

        Returns:
            {"restored", "skipped", "verification_failed", "missing", "conflicts",
             "errors": [str], "cancelled": bool}
        """
        dest_root = Path(destination)
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {
                "restored": 0,
                "skipped": 0,
                "verification_failed": 0,
                "missing": 0,
                "conflicts": [],
                "errors": [str(exc)],
                "cancelled": False,
            }

        assets = list(self.db_service.iter_project_assets(project_id))
        # 待恢复素材：至少有一个有效备份位置
        pending = [a for a in assets if a.backup_locations]
        total = len(pending)

        # 备份位置 -> (source_root, snapshot_index)，用于推导相对路径
        jobs = self.db_service.get_backup_jobs(project_id)
        loc_meta = self._build_location_meta(jobs)

        stats = {
            "restored": 0,
            "skipped": 0,
            "verification_failed": 0,
            "missing": 0,
            "conflicts": [],
            "errors": [],
            "cancelled": False,
        }

        for i, asset in enumerate(pending):
            if cancel_check and cancel_check():
                stats["cancelled"] = True
                break
            if progress_callback:
                progress_callback(i, total, f"恢复: {asset.file_name}")

            locations = [
                loc
                for loc in asset.backup_locations
                if not source_locations or loc in source_locations
            ]
            if not locations:
                continue

            restored_any = False
            for loc in locations:
                src = self._locate_backup_file(asset, loc, loc_meta)
                if src is None or not Path(src).is_file():
                    stats["missing"] += 1
                    continue
                # 目标路径：保留备份卷内的相对结构（相对于备份源根）
                rel = self._backup_relative(asset, loc, loc_meta)
                if rel is None:
                    rel = asset.file_name
                safe_rel = _safe_relative(rel)
                if safe_rel is None:
                    stats["errors"].append(
                        f"{asset.file_name}: 快照相对路径包含越界段，已拒绝写入"
                    )
                    continue
                dest_file = dest_root / safe_rel

                try:
                    ok, reason = self._restore_one(
                        asset, Path(src), dest_file, verify=verify
                    )
                except (OSError, ValueError) as exc:
                    stats["errors"].append(f"{asset.file_name}: {exc}")
                    continue

                if ok and reason == "restored":
                    stats["restored"] += 1
                    restored_any = True
                    self._record_restore(project_id, asset.file_name, str(dest_file))
                elif ok and reason == "skipped":
                    stats["skipped"] += 1
                    restored_any = True
                elif reason == "conflict":
                    stats["conflicts"].append(str(dest_file))
                elif reason == "verify_failed":
                    stats["verification_failed"] += 1
                if restored_any:
                    break

        return stats

    def _build_location_meta(self, jobs) -> dict:
        """备份位置 -> {source_path, snapshots:{source_path:relative}}"""
        meta = {}
        for job in jobs:
            for target in job.get("targets", []):
                loc = target.get("path")
                if not loc:
                    continue
                entry = meta.setdefault(
                    loc, {"source_path": job.get("source_path"), "snapshots": {}}
                )
                for item in job.get("file_snapshots", []):
                    sp = item.get("path")
                    rel = item.get("relative")
                    if sp and rel:
                        entry["snapshots"][sp] = rel
        return meta

    def _locate_backup_file(self, asset, loc: str, loc_meta: dict) -> str | None:
        """找到素材在备份位置的文件路径（优先快照/源根推导，其次有限搜索）。"""
        entry = loc_meta.get(loc, {})
        rel = entry.get("snapshots", {}).get(asset.file_path)
        if rel:
            safe = _safe_relative(rel)
            if safe is None:
                return None
            return str(Path(loc) / safe)
        src_root = entry.get("source_path")
        if src_root:
            try:
                root = Path(src_root).resolve()
                rel = Path(asset.file_path).relative_to(root)
                return str(Path(loc) / rel)
            except (ValueError, OSError):
                pass
        try:
            candidates = list(Path(loc).glob(Path(asset.file_name).name))
            if not candidates:
                candidates = list(Path(loc).glob(f"*/{Path(asset.file_name).name}"))
            if candidates:
                return str(candidates[0])
        except OSError:
            pass
        return None

    def _backup_relative(self, asset, loc: str, loc_meta: dict) -> str | None:
        entry = loc_meta.get(loc, {})
        rel = entry.get("snapshots", {}).get(asset.file_path)
        if rel:
            return rel
        src_root = entry.get("source_path")
        if src_root:
            try:
                return str(Path(asset.file_path).relative_to(Path(src_root).resolve()))
            except (ValueError, OSError):
                return None
        return None

    def _restore_one(
        self, asset, src: Path, dest: Path, verify: bool
    ) -> tuple[bool, str]:
        """恢复单个文件。返回 (成功, 原因)：
        restored / skipped（已存在且一致）/ conflict / verify_failed
        """
        if dest.exists():
            # 目标已存在：校验一致则跳过，不一致记冲突
            if verify and asset.checksum_value:
                try:
                    algo = ChecksumAlgorithm(asset.checksum_algorithm)
                except ValueError:
                    algo = ChecksumAlgorithm.XXHASH64
                existing_hash = self.checksum_service.compute_file_checksum(
                    str(dest), algo
                ).hash_value
                if existing_hash == asset.checksum_value:
                    return True, "skipped"
                return False, "conflict"
            return False, "conflict"

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))

        if verify and asset.checksum_value:
            try:
                algo = ChecksumAlgorithm(asset.checksum_algorithm)
            except ValueError:
                algo = ChecksumAlgorithm.XXHASH64
            copied_hash = self.checksum_service.compute_file_checksum(
                str(dest), algo
            ).hash_value
            if copied_hash != asset.checksum_value:
                dest.unlink(missing_ok=True)
                return False, "verify_failed"
        return True, "restored"

    def _record_restore(self, project_id: str, file_name: str, dest_path: str) -> None:
        try:
            self.db_service.record_operation(
                "素材回拷",
                f"{file_name} → {dest_path}",
                project_id=project_id,
                status="success",
                object_type="restore",
            )
        except Exception as exc:
            logger.debug(f"回拷审计日志写入失败: {exc}")
