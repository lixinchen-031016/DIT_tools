"""元数据读取服务

从 rename_service.py 拆离的 MetadataService，负责读取图片/RAW/视频元数据。
与 RenameService 零耦合，独立演进。
"""
import os
import sys
from datetime import datetime
from pathlib import Path

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
        try:
            tags = self._load_exif_tags(path)
            if tags:
                self._apply_exif_tags(tags, metadata)
        except Exception as e:
            logger.debug(f"EXIF 读取失败 ({path}): {e}")

        if not metadata.width or not metadata.height:
            self._read_image_dimensions(path, metadata)

    @staticmethod
    def _load_exif_tags(path: Path):
        """读取原始 EXIF 标签；依赖或文件问题由调用方统一降级。"""
        import exifread

        with open(path, "rb") as fh:
            return exifread.process_file(fh, details=False)

    @staticmethod
    def _exif_value(tags: dict, *tag_names: str) -> str:
        for tag_name in tag_names:
            value = tags.get(tag_name)
            if value:
                return str(value)
        return ""

    def _apply_exif_tags(self, tags: dict, metadata: MediaMetadata):
        """把 exifread 标签映射到领域模型。"""
        metadata.camera_make = self._exif_value(tags, "Image Make", "EXIF Make").strip()
        metadata.camera_model = self._exif_value(tags, "Image Model", "EXIF Model").strip()
        metadata.lens_model = self._exif_value(tags, "EXIF LensModel", "Image LensModel").strip()

        iso_value = self._exif_value(tags, "EXIF ISOSpeedRatings")
        if iso_value:
            try:
                metadata.iso = int(iso_value)
            except ValueError:
                pass

        aperture = self._exif_value(tags, "EXIF FNumber")
        if aperture:
            try:
                metadata.aperture = f"f/{float(aperture.replace('f/', '').strip()):.1f}"
            except (ValueError, TypeError):
                metadata.aperture = aperture

        metadata.shutter_speed = self._exif_value(tags, "EXIF ExposureTime").strip()
        metadata.focal_length = self._exif_value(tags, "EXIF FocalLength").strip()
        date_value = self._exif_value(tags, "EXIF DateTimeOriginal", "Image DateTimeOriginal")
        if date_value:
            try:
                metadata.date_taken = datetime.strptime(date_value.strip(), "%Y:%m:%d %H:%M:%S")
            except (ValueError, TypeError):
                pass
        self._apply_exif_dimensions(tags, metadata)

    @classmethod
    def _apply_exif_dimensions(cls, tags: dict, metadata: MediaMetadata):
        for width_key, height_key in (
            ("EXIF ExifImageWidth", "EXIF ExifImageLength"),
            ("Image ImageWidth", "Image ImageLength"),
        ):
            width = cls._exif_value(tags, width_key)
            height = cls._exif_value(tags, height_key)
            if not width or not height:
                continue
            try:
                metadata.width = int(width.split()[0])
                metadata.height = int(height.split()[0])
                return
            except (ValueError, TypeError):
                continue

    @staticmethod
    def _read_image_dimensions(path: Path, metadata: MediaMetadata):
        """用 Pillow 补充 EXIF 未提供的图像尺寸。"""
        try:
            from PIL import Image

            with Image.open(path) as img:
                metadata.width = img.width
                metadata.height = img.height
        except Exception as e:
            logger.debug(f"Pillow 读取尺寸失败 ({path}): {e}")

    def batch_read_metadata(self, file_paths: list[str]) -> list[MediaMetadata]:
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
            media_info = self._parse_mediainfo(file_path)
            if media_info is not None:
                self._apply_video_tracks(media_info.tracks, vm)
        except Exception as e:
            logger.debug(f"视频元数据读取失败 ({file_path}): {e}")
        return vm

    def _parse_mediainfo(self, file_path: str):
        """加载 MediaInfo，主路径失败时按平台候选动态库重试。"""
        from pymediainfo import MediaInfo

        try:
            return MediaInfo.parse(file_path)
        except OSError:
            for library_path in self._get_mediainfo_lib_paths():
                try:
                    return MediaInfo.parse(file_path, library_file=library_path)
                except OSError:
                    continue
        return None

    @staticmethod
    def _apply_video_tracks(tracks, metadata: VideoMetadata):
        """映射 General/Video/Audio 轨道字段到视频元数据。"""
        for track in tracks:
            if track.track_type == "General":
                if track.duration:
                    metadata.duration_seconds = float(track.duration) / 1000.0
                if track.overall_bit_rate:
                    metadata.bit_rate = int(track.overall_bit_rate)
            elif track.track_type == "Video":
                if track.width:
                    metadata.width = int(track.width)
                if track.height:
                    metadata.height = int(track.height)
                if track.codec_id or track.format:
                    metadata.codec = track.codec_id or track.format or ""
                if track.frame_rate:
                    try:
                        metadata.frame_rate = float(track.frame_rate)
                    except (ValueError, TypeError):
                        pass
            elif track.track_type == "Audio":
                if track.codec_id or track.format:
                    metadata.audio_codec = track.codec_id or track.format or ""
                if track.sampling_rate:
                    metadata.audio_sample_rate = int(track.sampling_rate)

    @staticmethod
    def _get_mediainfo_lib_paths() -> list[str]:
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
        paths: list[str] = []
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
