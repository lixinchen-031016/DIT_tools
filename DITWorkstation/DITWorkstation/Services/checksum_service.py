"""校验和计算服务 - 支持MD5和XXHash64"""
import hashlib
import threading
from pathlib import Path
from typing import Optional, Callable, Dict

import xxhash

from DITWorkstation.App import config
from DITWorkstation.Models import ChecksumAlgorithm, FileChecksum
from DITWorkstation.Utils import logger


class ChecksumService:
    """校验和计算服务"""

    def __init__(self, buffer_size: int = None):
        self.buffer_size = buffer_size or config.checksum_buffer_size
        self._cache: Dict[str, FileChecksum] = {}
        self._cache_lock = threading.Lock()

    def compute_file_checksum(
        self,
        file_path: str,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Optional[Callable[[float], None]] = None
    ) -> FileChecksum:
        """
        计算文件校验和

        Args:
            file_path: 文件路径
            algorithm: 校验和算法
            progress_callback: 进度回调 (0.0 ~ 1.0)

        Returns:
            FileChecksum 对象
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        file_size = path.stat().st_size
        file_key = f"{file_path}_{file_size}_{algorithm.value}"

        with self._cache_lock:
            if file_key in self._cache:
                cached = self._cache[file_key]
                if self._is_cache_valid(cached, path):
                    logger.debug(f"使用缓存的校验和: {file_path}")
                    return cached

        hash_value = self._compute_hash(path, algorithm, file_size, progress_callback)

        checksum = FileChecksum(
            file_path=str(path),
            algorithm=algorithm,
            hash_value=hash_value,
            file_size=file_size
        )

        with self._cache_lock:
            self._cache[file_key] = checksum

        return checksum

    def _is_cache_valid(self, cached: FileChecksum, path: Path) -> bool:
        """检查缓存是否有效"""
        try:
            current_size = path.stat().st_size
            return cached.file_size == current_size
        except Exception:
            return False

    def _compute_hash(
        self,
        path: Path,
        algorithm: ChecksumAlgorithm,
        file_size: int,
        progress_callback: Optional[Callable[[float], None]]
    ) -> str:
        """内部哈希计算"""
        if algorithm == ChecksumAlgorithm.XXHASH64:
            hasher = xxhash.xxh64()
        else:
            hasher = hashlib.md5()

        bytes_read = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(self.buffer_size)
                if not chunk:
                    break
                hasher.update(chunk)
                bytes_read += len(chunk)
                if progress_callback and file_size > 0:
                    progress_callback(bytes_read / file_size)

        return hasher.hexdigest()

    def verify_file(
        self,
        file_path: str,
        expected_hash: str,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Optional[Callable[[float], None]] = None
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

        file_size = path.stat().st_size
        computed_hash = self._compute_hash(path, algorithm, file_size, progress_callback)
        return computed_hash == expected_hash

    def verify_files_batch(
        self,
        file_pairs: list[tuple[str, str]],
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
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
        progress_callback: Optional[Callable[[float], None]] = None
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
            logger.info("校验和缓存已清空")
