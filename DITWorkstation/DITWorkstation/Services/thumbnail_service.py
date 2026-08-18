"""素材缩略图生成与缓存服务。

设计要点：
- 缓存目录：config.effective_db_dir / "thumbnails"，命名 {cache_key}_{size}.png
- 命中缓存同步返回 QPixmap；未命中投递到 QThreadPool 异步生成，完成后发 thumbnail_ready
- 图片：Pillow Image.thumbnail 解码缩放
- RAW：回退链 Pillow 直接解码 → EXIF 内嵌 JPEG/TIFF 预览（exifread，多数相机内嵌）
  → rawpy 全量解码
- 视频：回退链 ffmpeg 抽第 1 秒帧 → macOS 系统 QuickLook（qlmanage，无需安装）
  → PyAV；全部解码失败时返回 None 显示占位图
- QThreadPool 限 4 并发，避免大批量时抢占主线程
"""
import io
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap

from DITWorkstation.App import config
from DITWorkstation.Utils import logger

# 缩略图尺寸档位
SIZE_SMALL = 64    # 表格列用
SIZE_LARGE = 320   # 详情面板用


class ThumbnailService(QObject):
    """缩略图生成与缓存服务（线程安全单例）。"""

    # 缩略图生成完成信号：(cache_key, pixmap)
    thumbnail_ready = Signal(str, QPixmap)

    def __init__(self):
        super().__init__()
        self._cache_dir = config.effective_thumbnail_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # 复用全局 QThreadPool，限制并发避免大批量导入时抢占资源
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(4)
        # 视频抽帧依赖系统 ffmpeg（缺失时视频显示占位，不报错）
        self._ffmpeg_available = shutil.which("ffmpeg") is not None
        if not self._ffmpeg_available:
            logger.info(
                "未检测到 ffmpeg，视频缩略图将尝试 macOS QuickLook / PyAV 备选方案"
            )

    def get_thumbnail(self, cache_key: str, file_path: str,
                      asset_type: str, size: int = SIZE_SMALL) -> QPixmap | None:
        """同步取缓存；未命中返回 None 并异步生成。

        Args:
            cache_key: 缓存键。已入库素材用 asset_id；未入库文件用路径哈希
            file_path: 素材绝对路径
            asset_type: 素材类型（image/video/raw/audio）
            size: 目标尺寸（边长 px）
        """
        cache_file = self._cache_dir / f"{cache_key}_{size}.png"
        if cache_file.exists():
            pix = QPixmap(str(cache_file))
            if not pix.isNull():
                return pix
        # 未命中：异步生成
        worker = _ThumbnailWorker(self, cache_key, file_path, asset_type, size, cache_file)
        self._pool.start(worker)
        return None

    @property
    def cache_dir(self) -> Path:
        """缩略图缓存目录。"""
        return self._cache_dir

    def set_cache_dir(self, path: Path):
        """重设缩略图缓存目录（运行期生效，旧缓存保留待清理）。"""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._cache_dir = path
        logger.info(f"缩略图缓存目录已更新: {path}")

    def cache_size(self) -> int:
        """返回缩略图缓存占用总字节数。"""
        total = 0
        try:
            for p in self._cache_dir.glob("*.png"):
                total += p.stat().st_size
        except OSError:
            pass
        return total

    def clear_cache(self) -> int:
        """清空缩略图缓存，返回删除的文件数。"""
        removed = 0
        try:
            for p in self._cache_dir.glob("*.png"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        if removed:
            logger.info(f"缩略图缓存已清理: {removed} 个文件")
        return removed

    def _generate(self, file_path: str, asset_type: str, size: int) -> bytes | None:
        """实际生成 PNG bytes。失败返回 None（调用方显示占位图）。"""
        p = Path(file_path)
        if not p.exists():
            return None
        ext = p.suffix.lower()
        try:
            if ext in config.image_extensions or ext in config.raw_extensions:
                return self._gen_image(file_path, size)
            elif ext in config.video_extensions:
                return self._gen_video(file_path, size)
        except Exception as e:
            logger.debug(f"缩略图生成失败 {file_path}: {e}")
        return None

    def _gen_image(self, file_path: str, size: int) -> bytes | None:
        """生成图片/RAW 缩略图（PNG bytes）。

        回退链：
        1. Pillow 直接解码（jpg/png/tiff/webp 等）；
        2. EXIF 内嵌 JPEG/TIFF 预览（exifread，相机 RAW 普遍内嵌）；
        3. rawpy 全量解码（正式发布依赖）。
        """
        from PIL import Image

        # 1. 直接解码
        try:
            with Image.open(file_path) as img:
                return self._to_png(img, size)
        except Exception as e:
            logger.debug(f"Pillow 直接解码失败，尝试内嵌预览 {file_path}: {e}")

        # 2. EXIF 内嵌预览（Sony ARW / Canon CR2 / Nikon NEF 等普遍内嵌）
        preview = self._extract_embedded_preview(file_path)
        if preview:
            try:
                with Image.open(io.BytesIO(preview)) as img:
                    return self._to_png(img, size)
            except Exception as e:
                logger.debug(f"EXIF 内嵌预览解码失败 {file_path}: {e}")

        # 3. rawpy 全量解码
        return self._decode_rawpy(file_path, size)

    @staticmethod
    def _to_png(img, size: int) -> bytes:
        """把 PIL Image 缩放并编码为 PNG bytes（模式统一，保证 Qt 可加载）。"""
        img.thumbnail((size, size))
        buf = io.BytesIO()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _extract_embedded_preview(file_path: str) -> bytes | None:
        """从 EXIF 提取相机内嵌的 JPEG/TIFF 预览字节（无额外依赖）。

        exifread.process_file 在 extract_thumbnail 开启（默认）时会解析
        JPEGInterchangeFormat / MakerNote 中的预览，写入 tags 的
        "JPEGThumbnail"（JPEG）或 "TIFFThumbnail"（未压缩 TIFF）键。
        """
        try:
            import exifread

            with open(file_path, "rb") as fh:
                tags = exifread.process_file(fh, details=False)
            for key in ("JPEGThumbnail", "TIFFThumbnail"):
                val = tags.get(key)
                if val is None:
                    continue
                if hasattr(val, "values"):
                    val = val.values
                if isinstance(val, (bytes, bytearray)) and val:
                    return bytes(val)
        except Exception as e:
            logger.debug(f"EXIF 内嵌预览读取失败 {file_path}: {e}")
        return None

    @staticmethod
    def _decode_rawpy(file_path: str, size: int) -> bytes | None:
        """用 rawpy（libraw）全量解码 RAW 为缩略图。

        rawpy 是正式发布依赖；ImportError 防御仅用于开发环境或损坏安装。
        """
        try:
            import rawpy
            from PIL import Image
        except ImportError:
            return None
        try:
            with rawpy.imread(file_path) as raw:
                rgb = raw.postprocess(half_size=True, use_camera_wb=True)
            return ThumbnailService._to_png(Image.fromarray(rgb), size)
        except Exception as e:
            logger.debug(f"rawpy 解码失败 {file_path}: {e}")
            return None

    def _gen_video(self, file_path: str, size: int) -> bytes | None:
        """生成视频缩略图（PNG bytes）。

        回退链（全部失败返回 None，UI 显示占位图）：
        1. ffmpeg 抽第 1 秒帧（PATH 中可用时）；
        2. macOS 系统 QuickLook（qlmanage，无第三方依赖）；
        3. PyAV（正式依赖 av）。
        """
        frame = self._ffmpeg_frame(file_path, size)
        if frame:
            return frame
        frame = self._qlmanage_frame(file_path, size)
        if frame:
            return frame
        frame = self._av_frame(file_path, size)
        if frame:
            return frame
        return None

    def _ffmpeg_frame(self, file_path: str, size: int) -> bytes | None:
        """用 ffmpeg 抽视频第 1 秒帧并缩放（PNG bytes）。"""
        if not self._ffmpeg_available:
            return None
        cmd = [
            "ffmpeg", "-y", "-ss", "1", "-i", file_path,
            "-frames:v", "1", "-vf", f"scale={size}:-1",
            "-f", "image2pipe", "-vcodec", "png", "pipe:1",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"ffmpeg 抽帧失败 {file_path}: {e}")
        return None

    @staticmethod
    def _qlmanage_frame(file_path: str, size: int) -> bytes | None:
        """用 macOS 系统 QuickLook 生成视频缩略图（qlmanage，无需安装 ffmpeg）。

        qlmanage 输出与源文件同名（<basename>.png）到指定目录，
        再用 Pillow 缩放到目标尺寸并统一编码为 PNG。
        """
        if platform.system() != "Darwin":
            return None
        out_dir = tempfile.mkdtemp(prefix="dit_thumb_")
        try:
            result = subprocess.run(
                ["qlmanage", "-t", "-s", str(size), "-o", out_dir, file_path],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                return None
            png = Path(out_dir) / (Path(file_path).name + ".png")
            if not png.exists():
                return None
            from PIL import Image
            with Image.open(png) as img:
                return ThumbnailService._to_png(img, size)
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.debug(f"QuickLook 抽帧失败 {file_path}: {e}")
            return None
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    @staticmethod
    def _av_frame(file_path: str, size: int) -> bytes | None:
        """用 PyAV（正式依赖 av）解码视频首帧为缩略图。"""
        try:
            import av
        except ImportError:
            return None
        try:
            with av.open(file_path) as container:
                stream = next(
                    s for s in container.streams if s.type == "video"
                )
                for frame in container.decode(stream):
                    img = frame.to_image()
                    break
                else:
                    return None
            return ThumbnailService._to_png(img, size)
        except Exception as e:
            logger.debug(f"PyAV 抽帧失败 {file_path}: {e}")
            return None

class _ThumbnailWorker(QRunnable):
    """缩略图生成任务：在 QThreadPool 中执行，完成后发信号。"""

    def __init__(self, service, cache_key, file_path, asset_type, size, cache_file):
        super().__init__()
        self._service = service
        self._cache_key = cache_key
        self._file_path = file_path
        self._asset_type = asset_type
        self._size = size
        self._cache_file = cache_file

    @Slot()
    def run(self):
        data = self._service._generate(self._file_path, self._asset_type, self._size)
        if data is None:
            return
        # 写缓存（失败仅记日志，不影响功能）
        try:
            self._cache_file.write_bytes(data)
        except OSError as e:
            logger.warning(f"缩略图缓存写入失败 {self._cache_file}: {e}")
        pix = QPixmap()
        pix.loadFromData(data)
        if not pix.isNull():
            self._service.thumbnail_ready.emit(self._cache_key, pix)
