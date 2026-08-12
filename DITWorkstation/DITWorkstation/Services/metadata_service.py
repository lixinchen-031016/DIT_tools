"""元数据读取服务

从 rename_service.py 拆离的 MetadataService，负责读取图片/RAW/视频元数据。
与 RenameService 零耦合，独立演进。
"""
import os
import sys
from pathlib import Path
from typing import List
from datetime import datetime

from DITWorkstation.Models import MediaMetadata, VideoMetadata
from DITWorkstation.Utils import logger


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
        except Exception as e:
            logger.debug(f"EXIF 读取失败 ({path}): {e}")

        # 2. 用 Pillow 读取图像尺寸（仅对 Pillow 能打开的格式，作为补充）
        if not metadata.width or not metadata.height:
            try:
                from PIL import Image

                with Image.open(path) as img:
                    metadata.width = img.width
                    metadata.height = img.height
            except Exception as e:
                logger.debug(f"Pillow 读取尺寸失败 ({path}): {e}")

    def batch_read_metadata(self, file_paths: List[str]) -> List[MediaMetadata]:
        """批量读取元数据"""
        return [self.read_metadata(fp) for fp in file_paths]

    def read_video_metadata(self, file_path: str) -> VideoMetadata:
        """
        读取视频文件元数据（时长、编码、帧率、比特率、音频信息）

        使用 pymediainfo 解析，需要系统安装 MediaInfo 库。
        macOS:   brew install mediainfo
        Windows: 安装 MediaInfo（https://mediaarea.net/en/MediaInfo）
        """
        vm = VideoMetadata()
        try:
            from pymediainfo import MediaInfo

            # 尝试自动加载，失败则按平台指定常见动态库路径
            try:
                media_info = MediaInfo.parse(file_path)
            except OSError:
                lib_paths = self._get_mediainfo_lib_paths()
                media_info = None
                for p in lib_paths:
                    try:
                        media_info = MediaInfo.parse(file_path, library_file=p)
                        break
                    except OSError:
                        continue
                if media_info is None:
                    return vm

            for track in media_info.tracks:
                if track.track_type == "General":
                    if track.duration:
                        vm.duration_seconds = float(track.duration) / 1000.0
                    if track.overall_bit_rate:
                        vm.bit_rate = int(track.overall_bit_rate)
                elif track.track_type == "Video":
                    if track.width:
                        vm.width = int(track.width)
                    if track.height:
                        vm.height = int(track.height)
                    if track.codec_id or track.format:
                        vm.codec = track.codec_id or track.format or ""
                    if track.frame_rate:
                        try:
                            vm.frame_rate = float(track.frame_rate)
                        except (ValueError, TypeError):
                            pass
                elif track.track_type == "Audio":
                    if track.codec_id or track.format:
                        vm.audio_codec = track.codec_id or track.format or ""
                    if track.sampling_rate:
                        vm.audio_sample_rate = int(track.sampling_rate)
        except Exception as e:
            logger.debug(f"视频元数据读取失败 ({file_path}): {e}")
        return vm

    @staticmethod
    def _get_mediainfo_lib_paths() -> List[str]:
        """
        按操作系统返回 MediaInfo 动态库的常见安装路径。

        打包态（PyInstaller）：spec 会把动态库随包分发到 sys._MEIPASS
        根目录，这里优先尝试。Windows 官方 MediaInfo.dll 为自包含单文件，
        目标机无需再装 MediaInfo；macOS 的 Homebrew dylib 依赖 libzen 等
        附属库，若目标机缺少依赖仍需系统安装完整 MediaInfo。
        macOS:   Homebrew 安装的 libmediainfo dylib
        Windows: MediaInfo 官方安装包的 MediaInfo.dll（含 32/64 位 Program Files）
        Linux:   包管理器安装的 libmediainfo.so
        """
        paths: List[str] = []
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            bundle = Path(bundle_dir)
            if sys.platform == "win32":
                paths.append(str(bundle / "MediaInfo.dll"))
            elif sys.platform == "darwin":
                paths.extend([
                    str(bundle / "libmediainfo.0.dylib"),
                    str(bundle / "libmediainfo.dylib"),
                ])
            else:
                paths.append(str(bundle / "libmediainfo.so.0"))
        if sys.platform == "darwin":
            paths.extend([
                "/opt/homebrew/lib/libmediainfo.0.dylib",   # Apple Silicon Homebrew
                "/usr/local/lib/libmediainfo.0.dylib",      # Intel Homebrew
                "/opt/homebrew/lib/libmediainfo.dylib",
                "/usr/local/lib/libmediainfo.dylib",
            ])
        elif sys.platform == "win32":
            # 从环境变量读取 Program Files 路径（覆盖非系统盘安装、自定义目录）
            pf64 = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
            pf32 = os.environ.get("ProgramFiles(x86)")
            candidates = []
            if pf64:
                candidates.append(Path(pf64) / "MediaInfo" / "MediaInfo.dll")
            if pf32:
                candidates.append(Path(pf32) / "MediaInfo" / "MediaInfo.dll")
            # 默认安装路径兜底
            candidates.extend([
                Path("C:/Program Files/MediaInfo/MediaInfo.dll"),
                Path("C:/Program Files (x86)/MediaInfo/MediaInfo.dll"),
            ])
            paths.extend(str(c) for c in candidates)
        else:
            # Linux 及其他类 Unix 系统
            paths.extend([
                "/usr/lib/libmediainfo.so.0",
                "/usr/lib/x86_64-linux-gnu/libmediainfo.so.0",
                "/usr/local/lib/libmediainfo.so.0",
                "/usr/lib/libmediainfo.so",
            ])
        return paths
