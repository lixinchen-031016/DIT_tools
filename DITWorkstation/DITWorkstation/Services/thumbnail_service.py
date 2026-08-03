"""素材缩略图生成与缓存服务。

设计要点：
- 缓存目录：config.effective_db_dir / "thumbnails"，命名 {cache_key}_{size}.png
- 命中缓存同步返回 QPixmap；未命中投递到 QThreadPool 异步生成，完成后发 thumbnail_ready
- 图片：Pillow Image.thumbnail 解码缩放
- RAW：尝试 Pillow 读取内嵌 JPEG 预览（多数相机内嵌），失败返回 None 显示占位
- 视频：shell out 到 ffmpeg 抽第 1 秒帧；ffmpeg 不在 PATH 时返回 None
- QThreadPool 限 4 并发，避免大批量时抢占主线程
"""
import io
import subprocess
import shutil
from pathlib import Path
from typing import Optional

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
        self._cache_dir = config.effective_db_dir / "thumbnails"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # 复用全局 QThreadPool，限制并发避免大批量导入时抢占资源
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(4)
        # 视频抽帧依赖系统 ffmpeg（缺失时视频显示占位，不报错）
        self._ffmpeg_available = shutil.which("ffmpeg") is not None
        if not self._ffmpeg_available:
            logger.info("未检测到 ffmpeg，视频缩略图将显示占位图（不影响其他功能）")

    def get_thumbnail(self, cache_key: str, file_path: str,
                      asset_type: str, size: int = SIZE_SMALL) -> Optional[QPixmap]:
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

    def _generate(self, file_path: str, asset_type: str, size: int) -> Optional[bytes]:
        """实际生成 PNG bytes。失败返回 None（调用方显示占位图）。"""
        p = Path(file_path)
        if not p.exists():
            return None
        ext = p.suffix.lower()
        try:
            if ext in config.image_extensions or ext in config.raw_extensions:
                # 图片用 Pillow 直接解码；RAW 多数内嵌 JPEG 预览，Pillow 也能读取
                return self._gen_image(file_path, size)
            elif ext in config.video_extensions:
                return self._gen_video(file_path, size)
        except Exception as e:
            logger.warning(f"缩略图生成失败 {file_path}: {e}")
        return None

    def _gen_image(self, file_path: str, size: int) -> Optional[bytes]:
        """用 Pillow 生成图片缩略图（PNG bytes）。"""
        from PIL import Image
        with Image.open(file_path) as img:
            img.thumbnail((size, size))
            buf = io.BytesIO()
            # 转换模式保证 Qt 可加载
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.save(buf, format="PNG")
            return buf.getvalue()

    def _gen_video(self, file_path: str, size: int) -> Optional[bytes]:
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
