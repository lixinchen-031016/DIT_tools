"""校验和计算服务 - 支持MD5和XXHash64"""
import hashlib
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import xxhash

from DITWorkstation.App import config
from DITWorkstation.Models import ChecksumAlgorithm, FileChecksum
from DITWorkstation.Utils import logger


class ChecksumService:
    """校验和计算服务"""

    def __init__(
        self, buffer_size: int | None = None, cache_size: int | None = None,
        db_service=None, persistent_cache_size: int | None = None,
    ):
        self.buffer_size = buffer_size or config.checksum_buffer_size
        self._max_cache_size = (
            config.checksum_cache_size if cache_size is None else cache_size
        )
        if not isinstance(self._max_cache_size, int) or isinstance(self._max_cache_size, bool):
            raise TypeError("cache_size 必须是正整数")
        if self._max_cache_size <= 0:
            raise ValueError("cache_size 必须是正整数")
        self._cache: OrderedDict[str, FileChecksum] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._db_service = db_service
        self._persistent_cache_size = (
            config.checksum_persistent_cache_size
            if persistent_cache_size is None else persistent_cache_size
        )
        if not isinstance(self._persistent_cache_size, int) or self._persistent_cache_size <= 0:
            raise ValueError("persistent_cache_size 必须是正整数")

    @staticmethod
    def _cache_key(file_path: str, file_size: int, mtime_ns: int, algorithm: ChecksumAlgorithm) -> str:
        return f"{file_path}_{file_size}_{mtime_ns}_{algorithm.value}"

    def _load_persistent(self, path: Path, file_size: int, mtime_ns: int,
                         algorithm: ChecksumAlgorithm) -> FileChecksum | None:
        if self._db_service is None:
            return None
        try:
            hash_value = self._db_service.get_checksum_cache(
                str(path), file_size, mtime_ns, algorithm.value,
            )
        except Exception as exc:
            logger.debug(f"读取持久化校验和缓存失败: {exc}")
            return None
        if not hash_value:
            return None
        return FileChecksum(
            file_path=str(path), algorithm=algorithm, hash_value=hash_value,
            file_size=file_size, mtime_ns=mtime_ns,
        )

    def _remember(self, checksum: FileChecksum) -> None:
        key = self._cache_key(
            checksum.file_path, checksum.file_size, checksum.mtime_ns, checksum.algorithm,
        )
        with self._cache_lock:
            self._cache[key] = checksum
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        if self._db_service is not None:
            try:
                self._db_service.put_checksum_cache(
                    checksum.file_path, checksum.file_size, checksum.mtime_ns,
                    checksum.algorithm.value, checksum.hash_value,
                    limit=self._persistent_cache_size,
                )
            except Exception as exc:
                logger.debug(f"写入持久化校验和缓存失败: {exc}")

    def compute_file_checksum(
        self,
        file_path: str,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Callable[[float], None] | None = None,
        cancel_check: Callable[[], bool] | None = None
    ) -> FileChecksum:
        """
        计算文件校验和

        Args:
            file_path: 文件路径
            algorithm: 校验和算法
            progress_callback: 进度回调 (0.0 ~ 1.0)
            cancel_check: 取消检查回调，返回 True 时中断计算（抛 InterruptedError）

        Returns:
            FileChecksum 对象
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        stat = path.stat()
        file_size = stat.st_size
        mtime_ns = stat.st_mtime_ns
        # 缓存键含修改时间：同尺寸但内容变更的文件不会命中过期哈希
        file_key = self._cache_key(str(path), file_size, mtime_ns, algorithm)

        with self._cache_lock:
            cached = self._cache.get(file_key)
            if cached is not None:
                if self._is_cache_valid(cached, path):
                    self._cache.move_to_end(file_key)
                    logger.debug(f"使用缓存的校验和: {file_path}")
                    return cached
                self._cache.pop(file_key, None)

        persistent = self._load_persistent(path, file_size, mtime_ns, algorithm)
        if persistent is not None:
            self._remember(persistent)
            logger.debug(f"使用持久化缓存的校验和: {file_path}")
            return persistent

        hash_value = self._compute_hash(
            path, algorithm, file_size, progress_callback, cancel_check
        )

        checksum = FileChecksum(
            file_path=str(path),
            algorithm=algorithm,
            hash_value=hash_value,
            file_size=file_size,
            mtime_ns=mtime_ns
        )

        self._remember(checksum)

        return checksum

    def _is_cache_valid(self, cached: FileChecksum, path: Path) -> bool:
        """检查缓存是否有效：文件大小与修改时间均一致才视为有效。

        仅比对 size 会在「内容变更但尺寸不变」时返回过期哈希（如视频重编码、
        工作副本被覆盖），故加入 mtime_ns 双重校验。
        """
        try:
            stat = path.stat()
            return (
                cached.file_size == stat.st_size
                and cached.mtime_ns == stat.st_mtime_ns
            )
        except (OSError, ValueError):
            return False

    def _compute_hash(
        self,
        path: Path,
        algorithm: ChecksumAlgorithm,
        file_size: int,
        progress_callback: Callable[[float], None] | None,
        cancel_check: Callable[[], bool] | None = None
    ) -> str:
        """内部哈希计算"""
        if algorithm == ChecksumAlgorithm.XXHASH64:
            hasher = xxhash.xxh64()
        else:
            hasher = hashlib.md5()

        bytes_read = 0
        with open(path, "rb") as f:
            while True:
                if cancel_check and cancel_check():
                    raise InterruptedError("操作已取消")
                chunk = f.read(self.buffer_size)
                if not chunk:
                    break
                hasher.update(chunk)
                bytes_read += len(chunk)
                if progress_callback and file_size > 0:
                    progress_callback(bytes_read / file_size)

        return hasher.hexdigest()

    def copy_file_with_checksum(
        self,
        src_path: str,
        dest_path: str,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Callable[[float], None] | None = None,
        cancel_check: Callable[[], bool] | None = None
    ) -> FileChecksum:
        """边拷贝边计算源文件校验和（单次读盘）。

        相比「先 compute_file_checksum 再逐块拷贝」少一次源文件全量读取，
        对备份 / RAW 提取这类大文件场景可显著降低 IO 开销。

        Args:
            src_path: 源文件路径
            dest_path: 目标文件路径
            algorithm: 校验和算法
            progress_callback: 进度回调 (0.0 ~ 1.0)
            cancel_check: 取消检查回调，返回 True 时中断（抛 InterruptedError）

        Returns:
            源文件的校验和信息（dest 已完整写入）
        """
        src = Path(src_path)
        dest = Path(dest_path)
        if not src.exists():
            raise FileNotFoundError(f"文件不存在: {src_path}")

        stat = src.stat()
        file_size = stat.st_size
        if algorithm == ChecksumAlgorithm.XXHASH64:
            hasher = xxhash.xxh64()
        else:
            hasher = hashlib.md5()

        bytes_read = 0
        with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
            while True:
                if cancel_check and cancel_check():
                    raise InterruptedError("操作已取消")
                chunk = fsrc.read(self.buffer_size)
                if not chunk:
                    break
                hasher.update(chunk)
                fdst.write(chunk)
                bytes_read += len(chunk)
                if progress_callback and file_size > 0:
                    progress_callback(bytes_read / file_size)

        checksum = FileChecksum(
            file_path=str(src),
            algorithm=algorithm,
            hash_value=hasher.hexdigest(),
            file_size=file_size,
            mtime_ns=stat.st_mtime_ns,
        )
        self._remember(checksum)
        return checksum

    def verify_file(
        self,
        file_path: str,
        expected_hash: str,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Callable[[float], None] | None = None,
        cancel_check: Callable[[], bool] | None = None
    ) -> bool:
        """
        验证文件完整性

        Args:
            file_path: 文件路径
            expected_hash: 期望的哈希值
            algorithm: 校验和算法
            progress_callback: 进度回调

        Returns:
            验证是否通过
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        computed = self.compute_file_checksum(
            str(path), algorithm, progress_callback, cancel_check,
        )
        return computed.hash_value == expected_hash

    def verify_files_batch(
        self,
        file_pairs: list[tuple[str, str]],
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Callable[[int, int, str], None] | None = None
    ) -> list[dict]:
        """
        批量验证文件完整性

        Args:
            file_pairs: [(源文件路径, 目标文件路径)] 列表
            algorithm: 校验和算法
            progress_callback: 进度回调 (当前, 总数, 消息)

        Returns:
            验证结果列表 [{"source", "target", "verified", "error"}]
        """
        results = []
        total = len(file_pairs)

        for i, (source_path, target_path) in enumerate(file_pairs):
            try:
                source_checksum = self.compute_file_checksum(source_path, algorithm)
                target_checksum = self.compute_file_checksum(target_path, algorithm)
                verified = source_checksum.hash_value == target_checksum.hash_value

                results.append({
                    "source": source_path,
                    "target": target_path,
                    "verified": verified,
                    "error": None
                })

                if progress_callback:
                    progress_callback(i + 1, total,
                                     f"验证: {Path(source_path).name}")

            except Exception as e:
                results.append({
                    "source": source_path,
                    "target": target_path,
                    "verified": False,
                    "error": str(e)
                })
                logger.error(f"批量验证失败 {source_path} -> {target_path}: {e}")

        return results

    def compute_string_checksum(self, data: str, algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64) -> str:
        """计算字符串校验和"""
        if algorithm == ChecksumAlgorithm.XXHASH64:
            return xxhash.xxh64(data.encode()).hexdigest()
        else:
            return hashlib.md5(data.encode()).hexdigest()

    def compute_checksum_for_stream(
        self,
        stream,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        total_size: int = 0,
        progress_callback: Callable[[float], None] | None = None,
        cancel_check: Callable[[], bool] | None = None
    ) -> str:
        """
        从流中计算校验和

        Args:
            stream: 文件流对象
            algorithm: 校验和算法
            total_size: 总大小（用于进度计算）
            progress_callback: 进度回调

        Returns:
            校验和值
        """
        if algorithm == ChecksumAlgorithm.XXHASH64:
            hasher = xxhash.xxh64()
        else:
            hasher = hashlib.md5()

        bytes_read = 0
        while True:
            if cancel_check and cancel_check():
                raise InterruptedError("操作已取消")
            chunk = stream.read(self.buffer_size)
            if not chunk:
                break
            hasher.update(chunk)
            bytes_read += len(chunk)
            if progress_callback and total_size > 0:
                progress_callback(bytes_read / total_size)

        return hasher.hexdigest()

    def clear_cache(self):
        """清空校验和缓存"""
        with self._cache_lock:
            self._cache.clear()
        if self._db_service is not None:
            self._db_service.clear_checksum_cache()
        logger.info("校验和缓存已清空")

    def cache_size(self) -> int:
        """返回当前内存缓存条数。"""
        with self._cache_lock:
            return len(self._cache)
