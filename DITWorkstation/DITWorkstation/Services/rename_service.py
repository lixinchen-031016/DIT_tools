"""文件重命名与元数据管理服务"""
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime

from DITWorkstation.App import config
from DITWorkstation.Models import RenameRule, MediaMetadata


class RenameService:
    """文件重命名服务"""

    def preview_rename(self, files: List[str], rule: RenameRule) -> List[Tuple[str, str]]:
        """
        预览重命名结果

        Args:
            files: 文件路径列表
            rule: 重命名规则

        Returns:
            [(原路径, 新路径)] 列表
        """
        results = []
        for i, file_path in enumerate(sorted(files)):
            path = Path(file_path)
            new_name = self._generate_name(path, rule, i)
            new_path = str(path.parent / new_name)
            results.append((file_path, new_path))
        return results

    def execute_rename(self, files: List[str], rule: RenameRule) -> List[Tuple[str, str]]:
        """
        执行重命名

        Args:
            files: 文件路径列表
            rule: 重命名规则

        Returns:
            [(原路径, 新路径)] 成功重命名的列表
        """
        rename_pairs = self.preview_rename(files, rule)
        results = []

        for old_path, new_path in rename_pairs:
            old = Path(old_path)
            new = Path(new_path)

            if not old.exists():
                continue

            # 避免覆盖已有文件
            if new.exists() and old != new:
                stem = new.stem
                suffix = new.suffix
                counter = 1
                while new.exists():
                    new = new.parent / f"{stem}_{counter}{suffix}"
                    counter += 1

            if old != new:
                old.rename(new)
                results.append((old_path, str(new)))

        return results

    def _generate_name(self, path: Path, rule: RenameRule, index: int) -> str:
        """根据规则生成新文件名"""
        original_stem = path.stem
        suffix = path.suffix
        number = rule.start_number + index
        padded_number = str(number).zfill(rule.padding)

        # 替换模板变量
        name = rule.pattern
        name = name.replace("{scene}", rule.scene or "S000")
        name = name.replace("{shot}", rule.shot or "000")
        name = name.replace("{take}", rule.take or "00")
        name = name.replace("{original}", original_stem)
        name = name.replace("{number}", padded_number)
        name = name.replace("{prefix}", rule.prefix)
        name = name.replace("{suffix}", rule.suffix)
        name = name.replace("{date}", datetime.now().strftime("%Y%m%d"))

        # 清理多余分隔符
        name = re.sub(r'[_\-\s]+', '_', name).strip('_')

        return f"{name}{suffix}"

    def batch_rename_with_association(
        self,
        file_groups: List[List[str]],
        rule: RenameRule
    ) -> List[Tuple[str, str]]:
        """
        批量重命名并保持文件关联（如JPG+RAW对）

        Args:
            file_groups: 文件分组 [[jpg, raw], [jpg, raw], ...]
            rule: 重命名规则

        Returns:
            所有重命名对
        """
        all_results = []
        for i, group in enumerate(file_groups):
            group_rule = RenameRule(
                pattern=rule.pattern,
                scene=rule.scene,
                shot=rule.shot,
                take=rule.take,
                prefix=rule.prefix,
                suffix=rule.suffix,
                start_number=rule.start_number + i,
                padding=rule.padding
            )
            results = self.execute_rename(group, group_rule)
            all_results.extend(results)
        return all_results


class MetadataService:
    """元数据管理服务"""

    def read_metadata(self, file_path: str) -> MediaMetadata:
        """
        读取文件元数据

        Args:
            file_path: 文件路径

        Returns:
            MediaMetadata 对象
        """
        path = Path(file_path)
        metadata = MediaMetadata(
            file_path=str(path),
            file_name=path.name,
            file_size=path.stat().st_size if path.exists() else 0,
            file_type=path.suffix.lower()
        )

        # 尝试读取EXIF信息（JPG/TIFF/RAW）
        ext = path.suffix.lower()
        if ext in ('.jpg', '.jpeg', '.tiff', '.tif', '.png', '.webp',
                   '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf',
                   '.rw2', '.raf', '.pef', '.srw'):
            self._read_exif(path, metadata)

        return metadata

    def _read_exif(self, path: Path, metadata: MediaMetadata):
        """读取EXIF信息（支持 JPG/TIFF/RAW，使用 exifread）"""
        # 1. 用 exifread 读取 EXIF（支持所有 RAW 和 JPG 格式）
        try:
            import exifread

            with open(path, 'rb') as fh:
                tags = exifread.process_file(fh, details=False)

            if tags:
                def _get(tag_name):
                    """获取 exifread 标签的字符串值"""
                    val = tags.get(tag_name)
                    return str(val) if val else ""

                make = _get("Image Make") or _get("EXIF Make")
                if make:
                    metadata.camera_make = make.strip()

                model = _get("Image Model") or _get("EXIF Model")
                if model:
                    metadata.camera_model = model.strip()

                lens = _get("EXIF LensModel") or _get("Image LensModel")
                if lens:
                    metadata.lens_model = lens.strip()

                iso_str = _get("EXIF ISOSpeedRatings")
                if iso_str:
                    try:
                        metadata.iso = int(iso_str)
                    except ValueError:
                        pass

                fnumber_str = _get("EXIF FNumber")
                if fnumber_str:
                    try:
                        # exifread 返回 "f/2.8" 或 "2.8" 格式
                        val = fnumber_str.replace("f/", "").strip()
                        metadata.aperture = f"f/{float(val):.1f}"
                    except (ValueError, TypeError):
                        metadata.aperture = fnumber_str

                exposure_str = _get("EXIF ExposureTime")
                if exposure_str:
                    metadata.shutter_speed = exposure_str.strip()

                focal_str = _get("EXIF FocalLength")
                if focal_str:
                    # exifread 返回 "50.0 mm" 格式
                    metadata.focal_length = focal_str.strip()

                dto_str = _get("EXIF DateTimeOriginal") or _get("Image DateTimeOriginal")
                if dto_str:
                    try:
                        metadata.date_taken = datetime.strptime(
                            dto_str.strip(), "%Y:%m:%d %H:%M:%S"
                        )
                    except (ValueError, TypeError):
                        pass

                # 从 EXIF 读取图像尺寸（支持 RAW 文件）
                # EXIF 规范中高度标签名为 *Length 而非 *Height
                for w_key, h_key in [("EXIF ExifImageWidth", "EXIF ExifImageLength"),
                                     ("Image ImageWidth", "Image ImageLength")]:
                    w_str = _get(w_key)
                    h_str = _get(h_key)
                    if w_str and h_str:
                        try:
                            metadata.width = int(str(w_str).split()[0])
                            metadata.height = int(str(h_str).split()[0])
                            break
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass

        # 2. 用 Pillow 读取图像尺寸（仅对 Pillow 能打开的格式，作为补充）
        if not metadata.width or not metadata.height:
            try:
                from PIL import Image

                with Image.open(path) as img:
                    metadata.width = img.width
                    metadata.height = img.height
            except Exception:
                pass

    def batch_read_metadata(self, file_paths: List[str]) -> List[MediaMetadata]:
        """批量读取元数据"""
        return [self.read_metadata(fp) for fp in file_paths]
