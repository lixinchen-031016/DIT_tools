"""元数据读取服务

从 rename_service.py 拆离的 MetadataService，负责读取图片/RAW/视频元数据。
与 RenameService 零耦合，独立演进。
"""

import math
import os
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from DITWorkstation.Models import MediaMetadata, VideoMetadata
from DITWorkstation.Utils import logger

# Pillow getexif() 使用的标准 EXIF 标签 ID（TIFF/EXIF 规范）。
# 主路径 exifread 用可读标签名，Pillow 兜底路径需要原始 tag id。
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_DATETIME = 0x0132
_TAG_EXIF_IFD = 0x8769
_TAG_EXPOSURE_TIME = 0x829A
_TAG_FNUMBER = 0x829D
_TAG_SHUTTER_SPEED_VALUE = 0x9201  # APEX 曝光量，需换算成秒
_TAG_APERTURE_VALUE = 0x9202  # APEX 光圈值，需换算成 F 值
_TAG_DATE_ORIGINAL = 0x9003
_TAG_DATE_DIGITIZED = 0x9004
_TAG_ISO = 0x8827
_TAG_RECOMMENDED_EXPOSURE_INDEX = 0x8832
_TAG_FOCAL_LENGTH = 0x920A
_TAG_LENS_MAKE = 0xA433
_TAG_LENS_MODEL = 0xA434

# IFD0 的 ImageWidth/ImageLength 对部分 RAW（如 Nikon NEF）记录的是内嵌缩略图
# 尺寸（160x120），不能当作主图尺寸；低于该阈值的尺寸改由 RAW 解码兜底。
_MIN_PRIMARY_DIMENSION = 1000
# RAW 扩展名白名单：仅这些格式需要 rawpy 尺寸兜底
_RAW_EXTS = {
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".dng",
    ".orf",
    ".rw2",
    ".raf",
    ".pef",
    ".srw",
}


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
            file_type=path.suffix.lower(),
        )

        # 尝试读取EXIF信息（JPG/TIFF/RAW）
        ext = path.suffix.lower()
        if ext in (
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".png",
            ".webp",
            ".cr2",
            ".cr3",
            ".nef",
            ".arw",
            ".dng",
            ".orf",
            ".rw2",
            ".raf",
            ".pef",
            ".srw",
        ):
            self._read_exif(path, metadata)

        return metadata

    def _read_exif(self, path: Path, metadata: MediaMetadata):
        """读取 EXIF（支持 JPG/TIFF/RAW）：exifread 主路径 + Pillow 兜底。

        实测两类素材 exifread 会解析不完整，而 Pillow 能读到完整 EXIF：
        1. 后期软件导出的 JPEG 把 XMP 放在第一个 APP1 段、EXIF 放在第二个 APP1 段，
           exifread 只处理第一个 APP1，返回空标签，导致整组 EXIF 字段丢失；
        2. 部分相机/RAW 变体的 EXIF 子 IFD 布局，exifread 只能取到 IFD0 的部分字段。

        兜底只补空缺字段，不覆盖主路径已解析出的有效值，因此相机直出素材
        （含索尼 JPG/ARW）的解析结果保持不变。
        """
        tags = None
        try:
            tags = self._load_exif_tags(path)
            if tags:
                self._apply_exif_tags(tags, metadata)
        except Exception as e:
            logger.debug(f"EXIF 读取失败 ({path}): {e}")

        if self._needs_pillow_exif(tags, metadata):
            try:
                self._apply_pillow_exif(path, metadata)
            except Exception as e:
                logger.debug(f"Pillow EXIF 兜底失败 ({path}): {e}")

        # 部分机型（如 Canon CR2）把镜头型号只写进 MakerNote，
        # details=False 不解析厂商私有数据，需按需二次读取。
        if not metadata.lens_model:
            self._apply_makernote_lens(path, metadata)

        # Nikon NEF 的 IFD0 尺寸是 160x120 缩略图；Pillow 无法打开 RAW，
        # 改由 rawpy 读取主图尺寸。相机直出的 Sony ARW / Canon CR2 已有
        # 有效 EXIF 尺寸，不会触发这条路径。
        if self._needs_raw_dimensions(path, metadata):
            self._read_raw_dimensions(path, metadata)

        if not metadata.width or not metadata.height:
            self._read_image_dimensions(path, metadata)

    @staticmethod
    def _needs_pillow_exif(tags, metadata: MediaMetadata) -> bool:
        """exifread 无标签，或 EXIF 子 IFD 关键字段缺失时启用 Pillow 兜底。"""
        if not tags:
            return True
        return not metadata.date_taken or not metadata.iso

    @staticmethod
    def _apply_makernote_lens(path: Path, metadata: MediaMetadata):
        """从 MakerNote 补镜头型号（Canon CR2 等机型只有厂商私有标签）。

        仅在标准 LensModel 缺失时按需用 details=True 二次解析，避免对全部素材
        解析厂商私有数据；解析失败或没有有效镜头标签时静默降级。
        """
        try:
            import exifread

            with open(path, "rb") as fh:
                tags = exifread.process_file(fh, details=True)
            for key in (
                "MakerNote LensModel",
                "MakerNote LensType",
                "EXIF LensModel",
                "Image LensModel",
            ):
                value = MetadataService._clean_text(tags.get(key))
                # 过滤纯数字占位值（部分 Nikon MakerNote LensType 为 0）
                if value and not value.isdigit():
                    metadata.lens_model = value
                    return
        except Exception as e:
            logger.debug(f"MakerNote 镜头信息读取失败 ({path}): {e}")

    @staticmethod
    def _needs_raw_dimensions(path: Path, metadata: MediaMetadata) -> bool:
        """RAW 素材缺少可信主图尺寸时，需要 rawpy 兜底。"""
        return path.suffix.lower() in _RAW_EXTS and (
            metadata.width < _MIN_PRIMARY_DIMENSION
            or metadata.height < _MIN_PRIMARY_DIMENSION
        )

    @staticmethod
    def _read_raw_dimensions(path: Path, metadata: MediaMetadata):
        """用 rawpy 读取 RAW 主图尺寸（只读文件头，实测 50MB NEF < 1ms）。

        优先取有效成像尺寸 width/height，缺失时回退 raw_width/raw_height；
        rawpy 缺失或格式不支持时保留已有值，由后续 Pillow 路径继续兜底。
        """
        try:
            import rawpy

            with rawpy.imread(str(path)) as raw:
                sizes = raw.sizes
                width = getattr(sizes, "width", 0) or getattr(sizes, "raw_width", 0)
                height = getattr(sizes, "height", 0) or getattr(sizes, "raw_height", 0)
                if width > 0 and height > 0:
                    metadata.width = int(width)
                    metadata.height = int(height)
        except Exception as e:
            logger.debug(f"rawpy 读取尺寸失败 ({path}): {e}")

    @staticmethod
    def _load_exif_tags(path: Path):
        """读取原始 EXIF 标签；依赖或文件问题由调用方统一降级。"""
        import exifread

        with open(path, "rb") as fh:
            return exifread.process_file(fh, details=False)

    @classmethod
    def _apply_pillow_exif(cls, path: Path, metadata: MediaMetadata):
        """用 Pillow 的 getexif() 补齐 EXIF 字段（只填空，不覆盖主路径结果）。"""
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return
            top = dict(exif)
            try:
                sub = dict(exif.get_ifd(_TAG_EXIF_IFD))
            except (AttributeError, KeyError, OSError, ValueError, TypeError) as e:
                logger.debug(f"Pillow EXIF 子 IFD 读取失败 ({path}): {e}")
                sub = {}
            # 尺寸以图像实际尺寸为准，避免导出图沿用旧 EXIF 尺寸
            if not metadata.width or not metadata.height:
                metadata.width, metadata.height = img.size

        def pick(*tag_ids: int):
            """按优先级从 EXIF 子 IFD / IFD0 取第一个非空值。"""
            for tag_id in tag_ids:
                for source in (sub, top):
                    value = source.get(tag_id)
                    if value not in (None, "", b""):
                        return value
            return None

        if not metadata.camera_make:
            metadata.camera_make = cls._clean_text(pick(_TAG_MAKE))
        if not metadata.camera_model:
            metadata.camera_model = cls._clean_text(pick(_TAG_MODEL))
        if not metadata.lens_model:
            metadata.lens_model = cls._clean_text(pick(_TAG_LENS_MODEL, _TAG_LENS_MAKE))
        if not metadata.iso:
            metadata.iso = cls._parse_int(
                pick(_TAG_ISO, _TAG_RECOMMENDED_EXPOSURE_INDEX)
            )
        if not metadata.aperture:
            fnumber = pick(_TAG_FNUMBER)
            metadata.aperture = cls._format_aperture(
                fnumber
                if fnumber is not None
                else cls._apex_to_fnumber(pick(_TAG_APERTURE_VALUE))
            )
        if not metadata.shutter_speed:
            exposure = pick(_TAG_EXPOSURE_TIME)
            metadata.shutter_speed = cls._format_shutter(
                exposure
                if exposure is not None
                else cls._apex_to_seconds(pick(_TAG_SHUTTER_SPEED_VALUE))
            )
        if not metadata.focal_length:
            metadata.focal_length = cls._format_focal_length(pick(_TAG_FOCAL_LENGTH))
        if not metadata.date_taken:
            metadata.date_taken = cls._parse_datetime(
                pick(_TAG_DATE_ORIGINAL, _TAG_DATE_DIGITIZED, _TAG_DATETIME)
            )

    @staticmethod
    def _exif_value(tags: dict, *tag_names: str) -> str:
        """取第一个非空标签值并清洗；兼容 exifread IfdTag、bytes 与字符串。"""
        for tag_name in tag_names:
            value = tags.get(tag_name)
            if value:
                return MetadataService._clean_text(value)
        return ""

    @staticmethod
    def _clean_text(value) -> str:
        """EXIF 文本清洗：bytes 解码、去 NUL 结尾与首尾空白。"""
        if value is None:
            return ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        return str(value).replace("\x00", "").strip()

    @staticmethod
    def _as_float(value, *, positive_only: bool = True) -> float | None:
        """把 EXIF 数值（有理数 '28/5'、小数、IFDRational、'f/2.8'）转成 float。

        解析失败、无穷大或非有限值返回 None；默认只接受正数，APEX 换算可放宽。
        """
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        text = MetadataService._clean_text(value)
        if not text:
            return None
        if text.lower().startswith("f/"):
            text = text[2:].strip()
        try:
            if "/" in text:
                numerator, denominator = text.split("/", 1)
                number = float(numerator.strip()) / float(denominator.strip())
            else:
                number = float(text)
        except (ValueError, ZeroDivisionError, TypeError):
            return None
        if not math.isfinite(number) or (positive_only and number <= 0):
            return None
        return number

    @staticmethod
    def _parse_int(value) -> int:
        """把 EXIF 整数标签（可能带多值/分数）转成非负整数，失败返回 0。"""
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        text = MetadataService._clean_text(value)
        if not text:
            return 0
        first = text.split()[0].split("/")[0]
        try:
            return max(0, int(float(first)))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _format_number(value: float) -> str:
        """数值转紧凑字符串：整数不带小数，小数最多两位并去尾零。"""
        return f"{value:.2f}".rstrip("0").rstrip(".") or "0"

    @classmethod
    def _format_aperture(cls, value) -> str:
        """FNumber 统一为 'f/x.x'；无法解析时保留清洗后的原始文本。"""
        number = cls._as_float(value)
        if number is None:
            return cls._clean_text(value)
        return f"f/{number:.1f}"

    @classmethod
    def _format_focal_length(cls, value) -> str:
        """FocalLength 有理数转小数（'49/2' -> '24.5'），整数保持原样。"""
        number = cls._as_float(value)
        if number is None:
            return cls._clean_text(value)
        return cls._format_number(number)

    @classmethod
    def _format_shutter(cls, value) -> str:
        """曝光时间统一为最简分数（'0.01' -> '1/100'），长曝光保留秒数。"""
        number = cls._as_float(value)
        if number is None:
            return cls._clean_text(value)
        fraction = Fraction(number).limit_denominator(10000)
        if fraction.denominator == 1:
            return str(fraction.numerator)
        if fraction.numerator == 1:
            return f"1/{fraction.denominator}"
        return f"{fraction.numerator}/{fraction.denominator}"

    @classmethod
    def _apex_to_fnumber(cls, value) -> float | None:
        """APEX ApertureValue -> F 值（2^(AV/2)），无值/非法返回 None。"""
        apex = cls._as_float(value, positive_only=False)
        if apex is None:
            return None
        try:
            return 2 ** (apex / 2)
        except OverflowError:
            return None

    @classmethod
    def _apex_to_seconds(cls, value) -> float | None:
        """APEX ShutterSpeedValue -> 秒（2^-TV），无值/非法返回 None。"""
        tv = cls._as_float(value, positive_only=False)
        if tv is None:
            return None
        try:
            return 2 ** (-tv)
        except OverflowError:
            return None

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        """解析 EXIF 日期：兼容冒号/横杠分隔，容忍子秒与时区后缀。"""
        text = MetadataService._clean_text(value)
        if not text:
            return None
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            for candidate in (text, text[:19]):
                try:
                    return datetime.strptime(candidate, fmt)
                except (ValueError, TypeError):
                    continue
        return None

    def _apply_exif_tags(self, tags: dict, metadata: MediaMetadata):
        """把 exifread 标签映射到领域模型；有理数标签统一归一化。"""
        metadata.camera_make = self._clean_text(
            self._exif_value(tags, "Image Make", "EXIF Make")
        )
        metadata.camera_model = self._clean_text(
            self._exif_value(tags, "Image Model", "EXIF Model")
        )
        metadata.lens_model = self._clean_text(
            self._exif_value(tags, "EXIF LensModel", "Image LensModel")
        )

        iso_value = self._exif_value(
            tags,
            "EXIF ISOSpeedRatings",
            "Image ISOSpeedRatings",
            "EXIF PhotographicSensitivity",
        )
        if iso_value:
            metadata.iso = self._parse_int(iso_value)

        aperture = self._exif_value(tags, "EXIF FNumber", "Image FNumber")
        metadata.aperture = self._format_aperture(
            aperture
            if aperture
            else self._apex_to_fnumber(self._exif_value(tags, "EXIF ApertureValue"))
        )

        exposure = self._exif_value(tags, "EXIF ExposureTime", "Image ExposureTime")
        metadata.shutter_speed = self._format_shutter(
            exposure
            if exposure
            else self._apex_to_seconds(self._exif_value(tags, "EXIF ShutterSpeedValue"))
        )

        focal = self._exif_value(tags, "EXIF FocalLength", "Image FocalLength")
        if focal:
            metadata.focal_length = self._format_focal_length(focal)

        metadata.date_taken = self._parse_datetime(
            self._exif_value(
                tags,
                "EXIF DateTimeOriginal",
                "Image DateTimeOriginal",
                "EXIF DateTimeDigitized",
                "Image DateTimeDigitized",
            )
        )
        self._apply_exif_dimensions(tags, metadata)

    @classmethod
    def _apply_exif_dimensions(cls, tags: dict, metadata: MediaMetadata):
        """取主图尺寸；IFD0 尺寸可能是 RAW 内嵌缩略图，需过滤。"""
        for width_key, height_key, min_size in (
            ("EXIF ExifImageWidth", "EXIF ExifImageLength", 1),
            ("Image ImageWidth", "Image ImageLength", _MIN_PRIMARY_DIMENSION),
        ):
            width = cls._parse_int(cls._exif_value(tags, width_key))
            height = cls._parse_int(cls._exif_value(tags, height_key))
            if width >= min_size and height >= min_size:
                metadata.width = width
                metadata.height = height
                return

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
                paths.extend(
                    [
                        str(bundle / "libmediainfo.0.dylib"),
                        str(bundle / "libmediainfo.dylib"),
                    ]
                )
            else:
                paths.append(str(bundle / "libmediainfo.so.0"))
        if sys.platform == "darwin":
            paths.extend(
                [
                    "/opt/homebrew/lib/libmediainfo.0.dylib",  # Apple Silicon Homebrew
                    "/usr/local/lib/libmediainfo.0.dylib",  # Intel Homebrew
                    "/opt/homebrew/lib/libmediainfo.dylib",
                    "/usr/local/lib/libmediainfo.dylib",
                ]
            )
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
            candidates.extend(
                [
                    Path("C:/Program Files/MediaInfo/MediaInfo.dll"),
                    Path("C:/Program Files (x86)/MediaInfo/MediaInfo.dll"),
                ]
            )
            paths.extend(str(c) for c in candidates)
        else:
            # Linux 及其他类 Unix 系统
            paths.extend(
                [
                    "/usr/lib/libmediainfo.so.0",
                    "/usr/lib/x86_64-linux-gnu/libmediainfo.so.0",
                    "/usr/local/lib/libmediainfo.so.0",
                    "/usr/lib/libmediainfo.so",
                ]
            )
        return paths

    def write_xmp_sidecar(self, file_path, *, rating=None, tags=None, notes=None):
        """为媒体文件写入 XMP sidecar 文件（.xmp 同目录侧挂）。

        Args:
            file_path: 原始媒体文件路径
            rating: 评级 0-5（Adobe XMP 标准；DIT 的 0-3 直接映射）
            tags: 标签列表
            notes: 备注/描述

        Returns:
            True 表示写入成功（含已存在且内容一致=跳过）
        """
        path = Path(file_path)
        if not path.exists():
            return False
        xmp_path = path.parent / (path.name + ".xmp")
        if xmp_path.suffix not in (".xmp",):
            xmp_path = path.parent / (path.name + ".xmp")

        lines = [
            '<?xpacket begin="'
            + _XMP_PACKET_BEGIN
            + '" id="W5M0MpCehiHzreSzNTczkc9d"?>',
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
            '  <rdf:Description rdf:about=""',
            '   xmlns:dc="http://purl.org/dc/elements/1.1/"',
            '   xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
            '   xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">',
        ]

        if tags:
            lines.append("   <dc:subject>")
            lines.append("    <rdf:Bag>")
            for tag in tags:
                lines.append("     <rdf:li>" + _xmp_escape(str(tag)) + "</rdf:li>")
            lines.append("    </rdf:Bag>")
            lines.append("   </dc:subject>")

        if notes:
            lines.extend(
                [
                    "   <dc:description>",
                    "    <rdf:Alt>",
                    '     <rdf:li xml:lang="x-default">'
                    + _xmp_escape(str(notes))
                    + "</rdf:li>",
                    "    </rdf:Alt>",
                    "   </dc:description>",
                ]
            )

        if rating is not None and rating >= 0:
            lines.append("   <xmp:Rating>" + str(int(rating)) + "</xmp:Rating>")

        lines.extend(
            [
                "  </rdf:Description>",
                " </rdf:RDF>",
                "</x:xmpmeta>",
                '<?xpacket end="w"?>',
            ]
        )
        xmp_content = "\n".join(lines)

        # 原子写入：避免进程中断产生半成品
        import tempfile

        tmp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".xmp",
                dir=str(xmp_path.parent),
                delete=False,
            ) as tmp:
                tmp.write(xmp_content)
                tmp_name = tmp.name

            if xmp_path.exists():
                existing = xmp_path.read_text(encoding="utf-8", errors="replace")
                if existing == xmp_content:
                    Path(tmp_name).unlink(missing_ok=True)
                    tmp_name = None
                    return True
            Path(tmp_name).replace(xmp_path)
            tmp_name = None
            return True
        except (OSError, PermissionError) as exc:
            logger.warning(f"XMP 写入失败 {xmp_path}: {exc}")
            return False
        finally:
            if tmp_name is not None:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except OSError:
                    pass


_XMP_PACKET_BEGIN = "\ufeff"


def _xmp_escape(text: str) -> str:
    import html

    return html.escape(str(text), quote=True)
