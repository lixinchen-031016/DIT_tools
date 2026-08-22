"""项目归档与恢复服务

归档：把项目（信息 + 日志 + 素材元数据，可选附带素材文件）打包为 zip。
恢复：从归档 zip 重建项目、日志与素材记录，可选还原素材文件。
"""

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from DITWorkstation.Models import MediaAsset, Project, ShootingLog
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Utils import (
    get_checksum_service,
    logger,
    now_local,
    sanitize_filename,
)

_ARCHIVE_VERSION = 1
_MANIFEST = "manifest.json"
_ASSETS_JSON = "assets.json"
_LOGS_JSON = "logs.json"
_CHECKSUMS_TXT = "checksums.txt"
_FILES_DIR = "files"
_MAX_ARCHIVE_FILE_BYTES = 50 * 1024 * 1024 * 1024  # 50 GiB
_MAX_ARCHIVE_TOTAL_BYTES = 200 * 1024 * 1024 * 1024  # 200 GiB


class ArchiveService:
    """项目归档/恢复服务"""

    def __init__(
        self, db_service=None, checksum_service: ChecksumService | None = None
    ):
        self.db_service = db_service
        self.checksum_service = checksum_service or get_checksum_service()

    # ===== 归档 =====

    def archive_project(
        self,
        project_id: str,
        output_path: str,
        include_files: bool = False,
        progress_callback: Callable[[int, int, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        """把项目归档为 zip 包。

        包内含 manifest.json（版本/项目信息/统计）、logs.json（拍摄日志）、
        assets.json（素材元数据）、checksums.txt（校验清单），以及可选
        files/ 目录（素材文件副本，按索引前缀扁平存放避免重名冲突）。

        Args:
            project_id: 要归档的项目
            output_path: 目标 .zip 路径
            include_files: True 时把素材文件一并打包（离线文件跳过并记入缺失）
            progress_callback: (current, total, message)
            cancel_check: 返回 True 时中止

        Returns:
            归档包路径
        """
        if self.db_service is None:
            raise ValueError("archive_project 需要注入 db_service")

        project = self.db_service.get_project(project_id)
        if project is None:
            raise ValueError(f"项目不存在: {project_id}")

        logs = self.db_service.get_shooting_logs(project_id)
        total = self.db_service.count_project_assets(project_id)

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        copied = 0
        missing = 0
        checksum_lines = []
        metadata_path = None
        archive_temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=".assets.json",
                dir=path.parent,
                delete=False,
            ) as asset_stream:
                metadata_path = Path(asset_stream.name)
                asset_stream.write(b"[")
                for index, asset in enumerate(
                    self.db_service.iter_project_assets(project_id)
                ):
                    if cancel_check and cancel_check():
                        raise InterruptedError("归档已取消")
                    data = self._asset_to_dict(asset)
                    if include_files:
                        data["archive_file"] = (
                            f"{_FILES_DIR}/{self._file_rel_path(asset, index)}"
                        )
                    if index:
                        asset_stream.write(b",")
                    asset_stream.write(
                        json.dumps(data, ensure_ascii=False).encode("utf-8")
                    )
                asset_stream.write(b"]")

            with tempfile.NamedTemporaryFile(
                suffix=".zip",
                dir=path.parent,
                delete=False,
            ) as archive_stream:
                archive_temp_path = Path(archive_stream.name)

            with zipfile.ZipFile(archive_temp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                manifest = {
                    "version": _ARCHIVE_VERSION,
                    "exported_at": now_local().isoformat(),
                    "project": {
                        "project_id": project.project_id,
                        "name": project.name,
                        "description": project.description,
                        "base_path": project.base_path,
                        "workspace_id": project.workspace_id,
                        "created_at": project.created_at.isoformat(),
                    },
                    "stats": {"assets": total, "logs": len(logs)},
                }
                zf.writestr(
                    _LOGS_JSON,
                    json.dumps(
                        [self._log_to_dict(log) for log in logs],
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                zf.write(metadata_path, _ASSETS_JSON)
                if include_files:
                    for index, asset in enumerate(
                        self.db_service.iter_project_assets(project_id)
                    ):
                        if cancel_check and cancel_check():
                            raise InterruptedError("归档已取消")
                        if progress_callback:
                            progress_callback(index, total, f"归档: {asset.file_name}")
                        archive_file = (
                            f"{_FILES_DIR}/{self._file_rel_path(asset, index)}"
                        )
                        src = Path(asset.file_path)
                        try:
                            if src.is_file():
                                zf.write(src, archive_file)
                                copied += 1
                                checksum_lines.append(
                                    f"{archive_file}\t{asset.checksum_algorithm}\t{asset.checksum_value}"
                                )
                            else:
                                missing += 1
                                logger.warning(f"归档跳过缺失文件: {asset.file_path}")
                        except OSError as exc:
                            missing += 1
                            logger.warning(f"归档读取失败 {asset.file_path}: {exc}")
                elif progress_callback:
                    progress_callback(total, total, "已写入元数据")
                if checksum_lines:
                    zf.writestr(_CHECKSUMS_TXT, "\n".join(checksum_lines) + "\n")
                manifest["stats"]["files_copied"] = copied
                manifest["stats"]["files_missing"] = missing
                zf.writestr(
                    _MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2)
                )
            # 成功完成后才替换目标，取消或异常不会产生看似完整的归档包。
            archive_temp_path.replace(path)
            archive_temp_path = None
        finally:
            if metadata_path is not None:
                metadata_path.unlink(missing_ok=True)
            if archive_temp_path is not None:
                archive_temp_path.unlink(missing_ok=True)

        if progress_callback:
            progress_callback(total, total, "完成")
        logger.info(
            f"项目归档完成: {path}（{total} 素材 / {len(logs)} 日志，"
            f"文件 {copied} 个，缺失 {missing} 个）"
        )
        return str(path)

    def _file_rel_path(self, asset: MediaAsset, index: int) -> str:
        """生成素材文件在归档包内的扁平路径（索引前缀防重名冲突）。"""
        name = sanitize_filename(asset.file_name) or f"file_{index}"
        return f"{index:05d}_{name}"

    @staticmethod
    def _asset_to_dict(asset: MediaAsset) -> dict:
        return {
            "asset_id": asset.asset_id,
            "project_id": asset.project_id,
            "file_path": asset.file_path,
            "file_name": asset.file_name,
            "file_size": asset.file_size,
            "file_type": asset.file_type,
            "asset_type": asset.asset_type,
            "checksum_algorithm": asset.checksum_algorithm,
            "checksum_value": asset.checksum_value,
            "scene": asset.scene,
            "shot": asset.shot,
            "take": asset.take,
            "date_imported": asset.date_imported.isoformat(),
            "date_taken": asset.date_taken.isoformat() if asset.date_taken else None,
            "camera_make": asset.camera_make,
            "camera_model": asset.camera_model,
            "backup_locations": list(asset.backup_locations),
            "log_id": asset.log_id,
            "is_working_copy": asset.is_working_copy,
            "original_path": asset.original_path,
            "width": asset.width,
            "height": asset.height,
            "duration_seconds": asset.duration_seconds,
            "lens_model": asset.lens_model,
            "focal_length": asset.focal_length,
            "video_metadata": asset.video_metadata,
            "rating": asset.rating,
            "tags": asset.tags,
            "notes": asset.notes,
        }

    @staticmethod
    def _log_to_dict(log: ShootingLog) -> dict:
        return {
            "log_id": log.log_id,
            "project_id": log.project_id,
            "scene": log.scene,
            "shot": log.shot,
            "take": log.take,
            "description": log.description,
            "camera": log.camera,
            "lens": log.lens,
            "iso": log.iso,
            "aperture": log.aperture,
            "shutter_speed": log.shutter_speed,
            "notes": log.notes,
            "file_paths": list(log.file_paths),
            "created_at": log.created_at.isoformat(),
        }

    # ===== 恢复 =====

    def restore_project(
        self,
        archive_path: str,
        workspace_id: str | None = None,
        restore_files: bool = True,
        files_dest: str | None = None,
        verify: bool = True,
        progress_callback: Callable[[int, int, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict:
        """从归档 zip 恢复项目。

        - 重建项目（新 project_id；与目标工作区内重名时自动追加时间戳后缀）
        - 重建拍摄日志与素材记录（新 ID，保留日志-素材关联）
        - restore_files=True 且包内含 files/ 时，把素材文件还原到 files_dest
          并更新素材 file_path；verify=True 时逐文件比对校验和

        Args:
            archive_path: 归档 zip 路径
            workspace_id: 恢复到的目标工作区；None 时归入默认工作区
            restore_files: 是否还原素材文件
            files_dest: 文件还原目录（restore_files=True 且包内有效时必填）
            verify: 还原后是否校验文件校验和
            progress_callback: (current, total, message)
            cancel_check: 返回 True 时中止

        Returns:
            {"project", "restored_assets", "restored_logs",
             "restored_files", "missing_files", "mismatches": [str]}
        """
        if self.db_service is None:
            raise ValueError("restore_project 需要注入 db_service")

        archive = Path(archive_path)
        if not archive.is_file():
            raise FileNotFoundError(f"归档文件不存在: {archive_path}")

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if _MANIFEST not in names:
                raise ValueError("无效的归档包：缺少 manifest.json")
            self._validate_archive_members(zf)
            try:
                manifest = json.loads(zf.read(_MANIFEST))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("无效的归档包：manifest.json 不是有效 JSON") from exc
            if not isinstance(manifest, dict) or not isinstance(
                manifest.get("project"), dict
            ):
                raise ValueError("无效的归档包：manifest.json 结构错误")
            if manifest.get("version") != _ARCHIVE_VERSION:
                raise ValueError(
                    f"不支持的归档版本: {manifest.get('version')}（当前支持 {_ARCHIVE_VERSION}）"
                )
            log_dicts = self._read_json_list(zf, _LOGS_JSON)
            asset_dicts = self._read_json_list(zf, _ASSETS_JSON)

            has_files = any(
                name.startswith(f"{_FILES_DIR}/") and not name.endswith("/")
                for name in zf.namelist()
            )
            file_map = {}
            if has_files and restore_files:
                if not files_dest:
                    raise ValueError("归档包内含素材文件，请提供还原目录 files_dest")
                dest_root = Path(files_dest)
                dest_root.mkdir(parents=True, exist_ok=True)
                # 先写入目标目录内的临时目录，避免校验失败时留下可见半成品。
                with tempfile.TemporaryDirectory(
                    prefix=".dit-restore-", dir=str(dest_root)
                ) as temp_dir:
                    staged = {}
                    for member in zf.infolist():
                        if member.is_dir() or not member.filename.startswith(
                            f"{_FILES_DIR}/"
                        ):
                            continue
                        rel = self._safe_archive_relative_path(member.filename)
                        stage_target = Path(temp_dir) / rel
                        stage_target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            with (
                                zf.open(member) as src,
                                open(stage_target, "xb") as out,
                            ):
                                shutil.copyfileobj(src, out)
                            staged[member.filename] = (rel, stage_target)
                        except OSError as e:
                            raise ValueError(
                                f"归档文件还原失败 {member.filename}: {e}"
                            ) from e

                    # 所有文件写入临时目录后再校验 manifest 中的校验和。
                    checksums = {
                        d.get("archive_file"): d
                        for d in asset_dicts
                        if isinstance(d, dict) and d.get("archive_file")
                    }
                    for member_name, (rel, stage_target) in staged.items():
                        asset_info = checksums.get(member_name)
                        if verify and asset_info and asset_info.get("checksum_value"):
                            cached = self.checksum_service.compute_file_checksum(
                                str(stage_target),
                                self._checksum_algorithm(
                                    asset_info.get("checksum_algorithm")
                                ),
                                cancel_check=cancel_check,
                            )
                            if cached.hash_value != asset_info["checksum_value"]:
                                raise ValueError(f"归档文件校验和不一致: {member_name}")
                    for member_name, (rel, stage_target) in staged.items():
                        target = self._safe_restore_target(dest_root, rel)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(str(stage_target), str(target))
                        file_map[member_name] = str(target)

            old_name = manifest["project"].get("name", "恢复项目")
            project = self._create_restored_project(
                old_name, manifest["project"], workspace_id
            )

            old_log_to_new = {}
            for d in log_dicts:
                log = ShootingLog(
                    log_id=str(uuid.uuid4())[:8],
                    project_id=project.project_id,
                    scene=d.get("scene", ""),
                    shot=d.get("shot", ""),
                    take=d.get("take", ""),
                    description=d.get("description", ""),
                    camera=d.get("camera", ""),
                    lens=d.get("lens", ""),
                    iso=d.get("iso", 0),
                    aperture=d.get("aperture", ""),
                    shutter_speed=d.get("shutter_speed", ""),
                    notes=d.get("notes", ""),
                    file_paths=d.get("file_paths", []),
                )
                old_log_to_new[d["log_id"]] = log.log_id
                self.db_service.create_shooting_log(log)

            restored_assets = 0
            restored_files = 0
            missing_files = 0
            mismatches = []
            total = len(asset_dicts)
            for i, d in enumerate(asset_dicts):
                if cancel_check and cancel_check():
                    raise InterruptedError("恢复已取消")
                if progress_callback:
                    progress_callback(i, total, f"恢复: {d.get('file_name', '')}")

                file_path = d.get("file_path", "")
                archive_file = d.get("archive_file", "")
                if archive_file and archive_file in file_map:
                    file_path = file_map[archive_file]
                    restored_files += 1
                    if verify and d.get("checksum_value"):
                        try:
                            cached = self.checksum_service.compute_file_checksum(
                                file_path, cancel_check=cancel_check
                            )
                            if cached.hash_value != d["checksum_value"]:
                                mismatches.append(
                                    f"{d.get('file_name', file_path)}: 校验和不一致"
                                )
                        except Exception as e:
                            mismatches.append(f"{d.get('file_name', file_path)}: {e}")
                elif archive_file:
                    missing_files += 1

                old_log_id = d.get("log_id")
                asset = MediaAsset(
                    asset_id=str(uuid.uuid4())[:8],
                    project_id=project.project_id,
                    file_path=file_path,
                    file_name=d.get("file_name", Path(file_path).name),
                    file_size=d.get("file_size", 0),
                    file_type=d.get("file_type", ""),
                    asset_type=d.get("asset_type", "other"),
                    checksum_algorithm=d.get("checksum_algorithm", "xxhash64"),
                    checksum_value=d.get("checksum_value", ""),
                    scene=d.get("scene", ""),
                    shot=d.get("shot", ""),
                    take=d.get("take", ""),
                    camera_make=d.get("camera_make", ""),
                    camera_model=d.get("camera_model", ""),
                    backup_locations=d.get("backup_locations", []),
                    log_id=old_log_to_new.get(old_log_id) if old_log_id else None,
                    is_working_copy=bool(d.get("is_working_copy", False)),
                    original_path=d.get("original_path", ""),
                    width=d.get("width", 0),
                    height=d.get("height", 0),
                    duration_seconds=d.get("duration_seconds", 0.0),
                    lens_model=d.get("lens_model", ""),
                    focal_length=d.get("focal_length", ""),
                    video_metadata=d.get("video_metadata", ""),
                    rating=d.get("rating", 0),
                    tags=d.get("tags", ""),
                    notes=d.get("notes", ""),
                )
                if d.get("date_imported"):
                    try:
                        asset.date_imported = datetime.fromisoformat(d["date_imported"])
                    except ValueError:
                        pass
                if d.get("date_taken"):
                    try:
                        asset.date_taken = datetime.fromisoformat(d["date_taken"])
                    except ValueError:
                        pass
                self.db_service.add_media_asset(asset)
                restored_assets += 1

        if progress_callback:
            progress_callback(total, total, "完成")
        logger.info(
            f"项目恢复完成: {project.name}（素材 {restored_assets}，"
            f"文件还原 {restored_files}，缺失 {missing_files}）"
        )
        return {
            "project": project,
            "restored_assets": restored_assets,
            "restored_logs": len(log_dicts),
            "restored_files": restored_files,
            "missing_files": missing_files,
            "mismatches": mismatches,
        }

    @staticmethod
    def _safe_archive_relative_path(member_name: str) -> Path:
        """校验 zip 成员路径，拒绝绝对路径、父目录跳转和 NUL 字符。"""
        prefix = f"{_FILES_DIR}/"
        if not member_name.startswith(prefix) or "\x00" in member_name:
            raise ValueError(f"非法归档成员路径: {member_name!r}")
        rel_name = member_name[len(prefix) :].replace("\\", "/")
        posix_rel = PurePosixPath(rel_name)
        windows_rel = PureWindowsPath(rel_name)
        if (
            not rel_name
            or posix_rel.is_absolute()
            or windows_rel.is_absolute()
            or windows_rel.drive
            or any(part in ("", ".", "..") for part in posix_rel.parts)
        ):
            raise ValueError(f"非法归档成员路径: {member_name!r}")
        return Path(*posix_rel.parts)

    @classmethod
    def _validate_archive_members(cls, zf: zipfile.ZipFile):
        """限制归档解压大小并验证所有素材成员路径。"""
        total = 0
        for member in zf.infolist():
            if member.file_size > _MAX_ARCHIVE_FILE_BYTES:
                raise ValueError(f"归档单文件过大: {member.filename}")
            total += member.file_size
            if total > _MAX_ARCHIVE_TOTAL_BYTES:
                raise ValueError("归档解压总大小超过限制")
            if member.filename.startswith(f"{_FILES_DIR}/") and not member.is_dir():
                cls._safe_archive_relative_path(member.filename)

    @staticmethod
    def _safe_restore_target(dest_root: Path, relative: Path) -> Path:
        """确保最终还原路径不会通过已有符号链接逃出目标目录。"""
        # resolve 只用于安全比较；返回原始路径，保持用户输入的路径形式
        # （macOS 的 /var 与 /private/var 可能是两个不同字符串）。
        root = dest_root.resolve()
        target = dest_root / relative
        resolved_target = target.resolve(strict=False)
        try:
            resolved_target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"还原目标路径越出目标目录: {relative}") from exc
        return target

    @staticmethod
    def _checksum_algorithm(value: str | None):
        try:
            from DITWorkstation.Models import ChecksumAlgorithm

            return ChecksumAlgorithm(value or ChecksumAlgorithm.XXHASH64.value)
        except ValueError:
            from DITWorkstation.Models import ChecksumAlgorithm

            return ChecksumAlgorithm.XXHASH64

    def _create_restored_project(
        self, old_name: str, project_info: dict, workspace_id: str | None
    ) -> Project:
        """创建恢复后的项目；重名时自动追加时间戳后缀避免混淆。"""
        ws_id = workspace_id
        if ws_id is None:
            ws_id = (
                self.db_service.get_workspace("default").workspace_id
                if self.db_service.get_workspace("default")
                else None
            )
        name = old_name
        existing = {p.name for p in self.db_service.get_projects(workspace_id=ws_id)}
        if name in existing:
            name = f"{old_name} (恢复 {now_local().strftime('%Y%m%d%H%M%S')})"
        desc = project_info.get("description", "")
        return self.db_service.create_project(
            name=name,
            description=f"{desc}\n（由归档恢复）" if desc else "由归档恢复",
            base_path=project_info.get("base_path", ""),
            workspace_id=ws_id,
        )

    @staticmethod
    def _read_json_list(zf: zipfile.ZipFile, name: str) -> list[dict]:
        if name not in zf.namelist():
            return []
        try:
            data = json.loads(zf.read(name))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, KeyError):
            return []
