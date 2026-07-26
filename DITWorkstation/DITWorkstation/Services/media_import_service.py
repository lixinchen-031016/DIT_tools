"""媒体导入服务"""
import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Callable, Dict, Tuple
from datetime import datetime

from DITWorkstation.App import config
from DITWorkstation.Models import MediaAsset, AssetType, ChecksumAlgorithm
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Services.rename_service import MetadataService
from DITWorkstation.Utils import logger


class MediaImportService:
    """媒体导入服务"""

    def __init__(self, db_service: Optional[DatabaseService] = None):
        self.checksum_service = ChecksumService()
        self.metadata_service = MetadataService()
        self.db_service = db_service or DatabaseService()

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
        """
        total = len(file_paths)
        imported = 0
        skipped = 0
        failed = 0
        cancelled = False
        assets: List[MediaAsset] = []
        details = []

        for i, file_path in enumerate(file_paths):
            if cancel_check and cancel_check():
                cancelled = True
                logger.info("导入已用户取消")
                break

            if progress_callback and total > 0:
                progress = (i + 1) / total
                progress_callback("import", progress, f"处理: {Path(file_path).name}")

            try:
                path = Path(file_path)
                if not path.exists():
                    skipped += 1
                    details.append({"path": file_path, "status": "skipped", "reason": "文件不存在"})
                    continue

                if self.db_service.asset_exists_by_path(project_id, str(path)):
                    skipped += 1
                    details.append({"path": file_path, "status": "skipped", "reason": "已存在"})
                    continue

                final_path = str(path)
                is_copy = False
                original_path = ""

                if copy_to_workspace:
                    if not workspace_dir:
                        raise ValueError("复制模式需要指定工作区目录")
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
                    shot=shot
                )
                assets.append(asset)
                imported += 1
                details.append({"path": file_path, "status": "imported", "asset_id": asset.asset_id})

            except Exception as e:
                failed += 1
                logger.error(f"导入失败 {file_path}: {e}")
                details.append({"path": file_path, "status": "failed", "error": str(e)})

        if assets:
            self.db_service.add_media_assets_batch(assets)

        result = {
            "total": total,
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "cancelled": cancelled,
            "details": details
        }

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
        shot: str = ""
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

        Returns:
            MediaAsset 对象
        """
        path = Path(file_path)
        stat = path.stat()
        asset_type = self.classify_media_type(file_path)

        checksum_value = ""
        if compute_checksum:
            checksum = self.checksum_service.compute_file_checksum(
                file_path, ChecksumAlgorithm.XXHASH64
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

        # 图片/RAW 通过 PIL 读取 EXIF；视频元数据读取需第三方库，暂留默认
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

        asset = MediaAsset(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project_id,
            file_path=str(path.resolve()),
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
        )

        return asset

    def _copy_to_workspace(self, source_path: Path, workspace_dir: str) -> Path:
        """
        复制文件到工作区

        Args:
            source_path: 源文件路径
            workspace_dir: 工作区目录

        Returns:
            目标文件路径
        """
        workspace = Path(workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)

        dest = workspace / source_path.name
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = workspace / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.copy2(str(source_path), str(dest))
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
