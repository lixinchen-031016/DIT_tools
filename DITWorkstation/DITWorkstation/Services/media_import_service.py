"""媒体导入服务"""
import json
import os
import shutil
import stat
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Callable, Dict
from datetime import datetime

from DITWorkstation.App import config
from DITWorkstation.Models import MediaAsset, AssetType, ChecksumAlgorithm
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Services.metadata_service import MetadataService
from DITWorkstation.Utils import logger, get_checksum_service, normalize_path


class MediaImportService:
    """媒体导入服务"""

    def __init__(
        self,
        db_service: Optional[DatabaseService] = None,
        checksum_service: Optional[ChecksumService] = None
    ):
        # 优先使用注入的 checksum_service，否则取全局单例（避免重复计算缓存）
        self.checksum_service = checksum_service or get_checksum_service()
        self.metadata_service = MetadataService()
        self.db_service = db_service or DatabaseService()
        # 复制模式唯一命名互斥锁：并发导入同目录时避免文件名竞态
        self._copy_lock = threading.Lock()

    def classify_media_type(self, file_path: str) -> AssetType:
        """
        判断媒体类型

        Args:
            file_path: 文件路径

        Returns:
            资产类型
        """
        ext = Path(file_path).suffix.lower()
        if ext in config.raw_extensions:
            return AssetType.RAW
        elif ext in config.image_extensions:
            return AssetType.IMAGE
        elif ext in config.video_extensions:
            return AssetType.VIDEO
        elif ext in config.audio_extensions:
            return AssetType.AUDIO
        else:
            return AssetType.OTHER

    def scan_media_folder(
        self,
        folder: str,
        recursive: bool = True,
        include_images: bool = True,
        include_videos: bool = True,
        include_raw: bool = True,
        include_audio: bool = False
    ) -> List[Path]:
        """
        扫描文件夹中的媒体文件

        Args:
            folder: 文件夹路径
            recursive: 是否递归扫描
            include_images: 包含图片
            include_videos: 包含视频
            include_raw: 包含RAW文件
            include_audio: 包含音频

        Returns:
            媒体文件路径列表
        """
        folder_path = Path(folder)
        if not folder_path.exists():
            raise FileNotFoundError(f"文件夹不存在: {folder}")

        extensions = []
        if include_images:
            extensions.extend(config.image_extensions)
        if include_videos:
            extensions.extend(config.video_extensions)
        if include_raw:
            extensions.extend(config.raw_extensions)
        if include_audio:
            extensions.extend(config.audio_extensions)

        if not extensions:
            return []

        media_files = []
        iterator = folder_path.rglob("*") if recursive else folder_path.glob("*")

        for f in iterator:
            if f.is_file() and f.suffix.lower() in extensions:
                media_files.append(f)

        logger.info(f"扫描文件夹完成: {folder}, 发现 {len(media_files)} 个媒体文件")
        return sorted(media_files)

    def scan_multiple_folders(
        self,
        folders: List[str],
        recursive: bool = True,
        **kwargs
    ) -> List[Path]:
        """
        扫描多个文件夹

        Args:
            folders: 文件夹路径列表
            recursive: 是否递归
            **kwargs: 其他扫描参数

        Returns:
            去重后的媒体文件列表
        """
        all_files = set()
        for folder in folders:
            try:
                files = self.scan_media_folder(folder, recursive, **kwargs)
                all_files.update(files)
            except Exception as e:
                logger.warning(f"扫描文件夹失败 {folder}: {e}")
        return sorted(all_files)

    def get_file_info(self, file_path: str) -> Dict:
        """
        获取文件基本信息

        Args:
            file_path: 文件路径

        Returns:
            文件信息字典
        """
        path = Path(file_path)
        stat = path.stat()
        asset_type = self.classify_media_type(file_path)

        return {
            "path": str(path),
            "name": path.name,
            "suffix": path.suffix.lower(),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime),
            "created": datetime.fromtimestamp(stat.st_ctime),
            "asset_type": asset_type.value,
        }

    def import_assets(
        self,
        project_id: str,
        file_paths: List[str],
        compute_checksum: bool = True,
        read_metadata: bool = True,
        copy_to_workspace: bool = False,
        workspace_dir: Optional[str] = None,
        log_id: Optional[str] = None,
        scene: str = "",
        shot: str = "",
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> Dict:
        """
        导入素材到项目

        Args:
            project_id: 项目ID
            file_paths: 文件路径列表
            compute_checksum: 是否计算校验和
            read_metadata: 是否读取元数据
            copy_to_workspace: 是否复制到工作区
            workspace_dir: 工作区目录（复制模式下必填）
            log_id: 关联的拍摄日志ID（可选）
            scene: 场景号（可选，关联日志后自动填充）
            shot: 镜头号（可选，关联日志后自动填充）
            progress_callback: 进度回调 (target, progress, message)
            cancel_check: 取消检查回调，返回 True 时中断导入

        Returns:
            导入结果统计

        性能说明：
        - 批量查重：一次 IN 查询取回全部已存在路径，替代逐文件 SELECT（N+1 优化）
        - 并行处理：元数据/校验和读取使用线程池（config.max_import_workers），
          大卡导入时显著缩短耗时；单文件失败不影响其余文件
        """
        total = len(file_paths)
        if total == 0:
            return {"total": 0, "imported": 0, "skipped": 0, "failed": 0,
                    "cancelled": False, "details": []}

        # 批量查重：一次查询拿回全部已存在路径（N+1 优化）
        existing_paths = self.db_service.existing_asset_paths(project_id, file_paths)

        dedup_lock = threading.Lock()
        assets: List[MediaAsset] = []
        details: List[Dict] = []
        order = {fp: i for i, fp in enumerate(file_paths)}
        imported = skipped = failed = 0
        cancelled = False

        def _import_one(file_path: str):
            """处理单个文件；返回 (status, detail_dict, asset_or_None)"""
            if cancel_check and cancel_check():
                return "cancelled", {"path": file_path, "status": "cancelled"}, None
            try:
                path = Path(file_path)
                if not path.exists():
                    return "skipped", {"path": file_path, "status": "skipped",
                                       "reason": "文件不存在"}, None
                fp_key = normalize_path(str(path))
                with dedup_lock:
                    if fp_key in existing_paths:
                        return "skipped", {"path": file_path, "status": "skipped",
                                           "reason": "已存在"}, None
                    # 先占位，防止同批重复路径并发重复导入；失败时回退
                    existing_paths.add(fp_key)
                try:
                    final_path = str(path)
                    is_copy = False
                    original_path = ""
                    if copy_to_workspace:
                        if not workspace_dir:
                            raise ValueError("复制模式需要指定工作区目录")
                        # workspace_dir 已由调用方构造为完整目标目录（含项目名），
                        # 此处只做复制，不再拼接项目名（避免重复嵌套）。
                        dest_path = self._copy_to_workspace(path, workspace_dir)
                        final_path = str(dest_path)
                        is_copy = True
                        original_path = str(path)

                    asset = self._create_asset(
                        project_id=project_id,
                        file_path=final_path,
                        compute_checksum=compute_checksum,
                        is_working_copy=is_copy,
                        original_path=original_path,
                        read_metadata=read_metadata,
                        log_id=log_id,
                        scene=scene,
                        shot=shot,
                        cancel_check=cancel_check,
                    )
                    return "imported", {"path": file_path, "status": "imported",
                                        "asset_id": asset.asset_id}, asset
                except Exception:
                    with dedup_lock:
                        existing_paths.discard(fp_key)
                    raise
            except Exception as e:
                if isinstance(e, InterruptedError) and cancel_check and cancel_check():
                    return "cancelled", {"path": file_path, "status": "cancelled"}, None
                logger.error(f"导入失败 {file_path}: {e}")
                return "failed", {"path": file_path, "status": "failed", "error": str(e)}, None

        pool = ThreadPoolExecutor(max_workers=max(1, config.max_import_workers))
        try:
            futures = {pool.submit(_import_one, fp): fp for fp in file_paths}
            processed = 0
            for future in as_completed(futures):
                status, detail, asset = future.result()
                processed += 1
                details.append(detail)
                if status == "imported":
                    imported += 1
                    assets.append(asset)
                elif status == "skipped":
                    skipped += 1
                elif status == "failed":
                    failed += 1
                else:  # cancelled
                    cancelled = True

                if progress_callback and total > 0:
                    progress_callback(
                        "import", processed / total,
                        f"处理: {Path(detail['path']).name}"
                    )

                # 用户取消：停止收集剩余结果，已提交未执行的任务由 shutdown 取消
                if cancelled or (cancel_check and cancel_check()):
                    cancelled = True
                    break
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if assets:
            self.db_service.add_media_assets_batch(assets)

        # 按输入顺序稳定输出 details（并发完成顺序与输入顺序无关）
        details.sort(key=lambda d: order.get(d["path"], 0))

        result = {
            "total": total,
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "cancelled": cancelled,
            "details": details
        }

        # 操作审计：记录本次导入结果（失败不影响导入本身）
        try:
            self.db_service.record_operation(
                "导入素材",
                f"共 {total} 个，成功 {imported}，跳过 {skipped}，失败 {failed}"
                + ("，已取消" if cancelled else ""),
                project_id=project_id,
            )
        except Exception as e:
            logger.warning(f"记录导入操作日志失败: {e}")

        logger.info(f"导入完成: 共 {total} 个, 成功 {imported} 个, 跳过 {skipped} 个, 失败 {failed} 个, 取消: {cancelled}")
        return result

    def _create_asset(
        self,
        project_id: str,
        file_path: str,
        compute_checksum: bool = True,
        is_working_copy: bool = False,
        original_path: str = "",
        read_metadata: bool = True,
        log_id: Optional[str] = None,
        scene: str = "",
        shot: str = "",
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> MediaAsset:
        """
        创建素材资产对象

        Args:
            project_id: 项目ID
            file_path: 文件路径
            compute_checksum: 是否计算校验和
            is_working_copy: 是否工作副本
            original_path: 原始路径
            read_metadata: 是否读取元数据
            log_id: 关联的拍摄日志ID
            scene: 场景号
            shot: 镜头号
            cancel_check: 取消检查回调（透传给校验和计算，返回 True 时中断）

        Returns:
            MediaAsset 对象
        """
        path = Path(file_path)
        stat = path.stat()
        asset_type = self.classify_media_type(file_path)

        checksum_value = ""
        if compute_checksum:
            checksum = self.checksum_service.compute_file_checksum(
                file_path, ChecksumAlgorithm.XXHASH64, cancel_check=cancel_check
            )
            checksum_value = checksum.hash_value

        # 元数据默认值
        width = 0
        height = 0
        duration_seconds = 0.0
        lens_model = ""
        focal_length = ""
        date_taken = None
        camera_make = ""
        camera_model = ""
        video_metadata = ""

        # 图片/RAW 读取 EXIF
        if read_metadata and asset_type in (AssetType.IMAGE, AssetType.RAW):
            try:
                meta = self.metadata_service.read_metadata(file_path)
                width = meta.width or 0
                height = meta.height or 0
                lens_model = meta.lens_model or ""
                focal_length = meta.focal_length or ""
                date_taken = meta.date_taken
                camera_make = meta.camera_make or ""
                camera_model = meta.camera_model or ""
            except Exception as e:
                logger.debug(f"读取元数据失败 {file_path}: {e}")

        # 视频读取元数据
        if read_metadata and asset_type == AssetType.VIDEO:
            try:
                vm = self.metadata_service.read_video_metadata(file_path)
                width = vm.width or 0
                height = vm.height or 0
                duration_seconds = vm.duration_seconds or 0.0
                video_metadata = json.dumps({
                    "codec": vm.codec,
                    "frame_rate": vm.frame_rate,
                    "bit_rate": vm.bit_rate,
                    "audio_codec": vm.audio_codec,
                    "audio_sample_rate": vm.audio_sample_rate,
                })
            except Exception as e:
                logger.debug(f"读取视频元数据失败 {file_path}: {e}")

        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project_id,
            file_path=normalize_path(str(path)),
            file_name=path.name,
            file_size=stat.st_size,
            file_type=path.suffix.lower(),
            asset_type=asset_type.value,
            checksum_algorithm=ChecksumAlgorithm.XXHASH64.value,
            checksum_value=checksum_value,
            scene=scene,
            shot=shot,
            log_id=log_id,
            is_working_copy=is_working_copy,
            original_path=original_path,
            date_imported=datetime.now(),
            date_taken=date_taken,
            camera_make=camera_make,
            camera_model=camera_model,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            lens_model=lens_model,
            focal_length=focal_length,
            video_metadata=video_metadata,
        )

        return asset

    def _copy_to_workspace(self, source_path: Path, workspace_dir: str) -> Path:
        """
        复制文件到工作区目标目录。

        注意：workspace_dir 由调用方构造为「完整目标目录」（已含项目名子文件夹，
        如 <ws.path>/<项目名>/）。本方法只负责把源文件复制进去，不再拼接项目名，
        避免调用方与这里重复拼接导致 <ws>/<项目>/<项目>/file 的嵌套问题。

        Args:
            source_path: 源文件路径
            workspace_dir: 完整目标目录（调用方已含项目名）

        Returns:
            目标文件路径
        """
        workspace = Path(workspace_dir)
        with self._copy_lock:
            # 锁内完成选名，避免并发导入同目录时唯一命名竞态
            workspace.mkdir(parents=True, exist_ok=True)
            dest = workspace / source_path.name
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                counter = 1
                while dest.exists():
                    dest = workspace / f"{stem}_{counter}{suffix}"
                    counter += 1
            # 占位：把名字保留到锁内，防止并发线程选到同一目标后互相覆盖
            dest.touch()
        try:
            shutil.copy2(str(source_path), str(dest))
            # Windows：copy2 会连同源文件的只读属性一起复制，存储卡/相机
            # 文件常带只读位，导致工作区副本后续重命名/删除失败；清掉它
            if os.name == "nt":
                os.chmod(dest, stat.S_IREAD | stat.S_IWRITE)
        except Exception:
            # 拷贝失败时清理占位/半成品文件
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        logger.debug(f"复制到工作区: {source_path} -> {dest}")
        return dest

    def copy_asset_to_workspace(
        self,
        asset_id: str,
        workspace_dir: str
    ) -> Optional[MediaAsset]:
        """
        将现有素材复制为工作副本

        Args:
            asset_id: 素材ID
            workspace_dir: 工作区目录

        Returns:
            更新后的素材对象，失败返回None
        """
        asset = self.db_service.get_media_asset(asset_id)
        if not asset:
            logger.warning(f"素材不存在: {asset_id}")
            return None

        if asset.is_working_copy:
            logger.info(f"已经是工作副本: {asset_id}")
            return asset

        try:
            source_path = Path(asset.file_path)
            dest_path = self._copy_to_workspace(source_path, workspace_dir)

            self.db_service.update_media_asset(
                asset_id,
                file_path=str(dest_path),
                file_name=dest_path.name,
                is_working_copy=True,
                original_path=asset.file_path
            )

            return self.db_service.get_media_asset(asset_id)

        except Exception as e:
            logger.error(f"复制到工作区失败 {asset_id}: {e}")
            return None

    def get_supported_extensions(self) -> Dict[str, List[str]]:
        """
        获取支持的文件扩展名

        Returns:
            按类型分组的扩展名列表
        """
        return {
            "image": config.image_extensions,
            "video": config.video_extensions,
            "raw": config.raw_extensions,
            "audio": config.audio_extensions,
        }
