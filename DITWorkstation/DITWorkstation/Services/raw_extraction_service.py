"""JPG筛选后RAW文件提取服务"""
import threading
from pathlib import Path
from typing import List, Optional, Callable, Tuple, Dict

from DITWorkstation.App import config
from DITWorkstation.Models import ChecksumAlgorithm
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Utils import logger, get_checksum_service


class RawExtractionService:
    """JPG筛选后RAW文件提取服务"""

    def __init__(self, checksum_service: Optional[ChecksumService] = None):
        # 与 MediaImportService / BackupService 复用同一校验和缓存
        self.checksum_service = checksum_service or get_checksum_service()
        self._cancelled = False
        self._cancelled_lock = threading.Lock()

    def scan_jpg_folder(self, jpg_folder: str) -> List[Path]:
        """
        扫描JPG文件夹，获取筛选后的JPG文件列表

        Args:
            jpg_folder: JPG文件夹路径

        Returns:
            JPG文件路径列表
        """
        folder = Path(jpg_folder)
        if not folder.exists():
            raise FileNotFoundError(f"文件夹不存在: {jpg_folder}")

        jpg_files = []
        extensions = ('.jpg', '.jpeg')
        for ext in extensions:
            jpg_files.extend(folder.rglob(f"*{ext}"))
            jpg_files.extend(folder.rglob(f"*{ext.upper()}"))

        logger.info(f"扫描JPG文件夹完成: {jpg_folder}, 发现 {len(jpg_files)} 个文件")
        return sorted(set(jpg_files))

    def scan_raw_folder(self, raw_folder: str) -> Dict[str, Path]:
        """
        扫描RAW文件夹，建立文件名索引

        Args:
            raw_folder: RAW源文件夹路径

        Returns:
            {文件名主干(小写): 文件路径} 映射
        """
        folder = Path(raw_folder)
        if not folder.exists():
            raise FileNotFoundError(f"文件夹不存在: {raw_folder}")

        raw_index = {}
        for ext in config.raw_extensions:
            for f in folder.rglob(f"*{ext}"):
                raw_index[f.stem.lower()] = f
            for f in folder.rglob(f"*{ext.upper()}"):
                raw_index[f.stem.lower()] = f

        logger.info(f"扫描RAW文件夹完成: {raw_folder}, 建立索引 {len(raw_index)} 个文件")
        return raw_index

    def match_raw_files(
        self,
        jpg_files: List[Path],
        raw_index: Dict[str, Path]
    ) -> List[Tuple[Path, Optional[Path]]]:
        """
        根据JPG文件名匹配对应RAW文件

        Args:
            jpg_files: JPG文件列表
            raw_index: RAW文件索引

        Returns:
            [(jpg_path, raw_path_or_None)] 匹配结果
        """
        matches = []
        for jpg in jpg_files:
            stem = jpg.stem.lower()
            raw_path = raw_index.get(stem)
            matches.append((jpg, raw_path))

        matched_count = sum(1 for _, raw in matches if raw is not None)
        logger.info(f"文件匹配完成: {matched_count}/{len(matches)} 匹配成功")
        return matches

    def extract_raw_files(
        self,
        jpg_folder: str,
        raw_folder: str,
        output_folder: str,
        verify: bool = True,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict:
        """
        提取JPG对应的RAW文件到指定位置

        Args:
            jpg_folder: 筛选后的JPG文件夹
            raw_folder: RAW源文件夹
            output_folder: 输出文件夹
            verify: 是否验证拷贝完整性
            algorithm: 校验和算法
            progress_callback: 进度回调 (current, total, message)

        Returns:
            提取结果统计
        """
        self._set_cancelled(False)
        output = Path(output_folder)
        output.mkdir(parents=True, exist_ok=True)

        jpg_files = self.scan_jpg_folder(jpg_folder)
        raw_index = self.scan_raw_folder(raw_folder)
        matches = self.match_raw_files(jpg_files, raw_index)

        total = len(matches)
        extracted = 0
        not_found = 0
        failed = 0
        results = {
            "total_jpg": total,
            "extracted": 0,
            "not_found": 0,
            "failed": 0,
            "details": []
        }

        for i, (jpg_path, raw_path) in enumerate(matches):
            if self._is_cancelled():
                logger.info("用户取消RAW提取操作")
                break

            if progress_callback:
                progress_callback(i, total, f"处理: {jpg_path.name}")

            if raw_path is None:
                not_found += 1
                results["details"].append({
                    "jpg": str(jpg_path),
                    "raw": None,
                    "status": "not_found"
                })
                continue

            try:
                dest = self._get_unique_dest(output, raw_path)

                # 边拷贝边计算源校验和：单次读盘，替代「先哈希再拷贝」的两次读盘
                src_checksum = self.checksum_service.copy_file_with_checksum(
                    str(raw_path), str(dest), algorithm,
                    cancel_check=self._is_cancelled,
                )

                if verify:
                    verified = self.checksum_service.verify_file(
                        str(dest), src_checksum.hash_value, algorithm,
                        cancel_check=self._is_cancelled,
                    )
                    if not verified:
                        raise IOError(f"校验和验证失败: {raw_path.name}")

                extracted += 1
                results["details"].append({
                    "jpg": str(jpg_path),
                    "raw": str(raw_path),
                    "output": str(dest),
                    "status": "success"
                })

            except Exception as e:
                failed += 1
                logger.error(f"RAW提取失败 {raw_path.name}: {e}")
                # 清理残缺/损坏的目标文件，避免残留半成品
                try:
                    dest.unlink(missing_ok=True)
                except (OSError, UnboundLocalError):
                    pass
                results["details"].append({
                    "jpg": str(jpg_path),
                    "raw": str(raw_path),
                    "status": "failed",
                    "error": str(e)
                })

        results["extracted"] = extracted
        results["not_found"] = not_found
        results["failed"] = failed

        if progress_callback:
            progress_callback(total, total, "完成")

        logger.info(f"RAW提取完成: 成功 {extracted} 个, 未找到 {not_found} 个, 失败 {failed} 个")
        return results

    def extract_raw_files_streaming(
        self,
        matches: List[Tuple[Path, Optional[Path]]],
        output_folder: str,
        verify: bool = True,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict:
        """
        流式提取RAW文件（适合大量文件场景）

        Args:
            matches: 匹配结果列表 [(jpg_path, raw_path)]
            output_folder: 输出文件夹
            verify: 是否验证
            algorithm: 校验和算法
            progress_callback: 进度回调

        Returns:
            提取结果统计
        """
        self._set_cancelled(False)
        output = Path(output_folder)
        output.mkdir(parents=True, exist_ok=True)

        total = len(matches)
        extracted = 0
        not_found = 0
        failed = 0
        results = {
            "total_jpg": total,
            "extracted": 0,
            "not_found": 0,
            "failed": 0,
            "details": []
        }

        for i, (jpg_path, raw_path) in enumerate(matches):
            if self._is_cancelled():
                break

            if progress_callback:
                progress_callback(i, total, f"处理: {jpg_path.name}")

            if raw_path is None:
                not_found += 1
                continue

            try:
                dest = self._get_unique_dest(output, raw_path)

                # 边拷贝边计算源校验和：单次读盘，替代「先哈希再拷贝」的两次读盘
                src_checksum = self.checksum_service.copy_file_with_checksum(
                    str(raw_path), str(dest), algorithm,
                    cancel_check=self._is_cancelled,
                )

                if verify:
                    verified = self.checksum_service.verify_file(
                        str(dest), src_checksum.hash_value, algorithm,
                        cancel_check=self._is_cancelled,
                    )
                    if not verified:
                        raise IOError(f"校验和验证失败: {raw_path.name}")

                extracted += 1
                results["details"].append({
                    "jpg": str(jpg_path),
                    "raw": str(raw_path),
                    "output": str(dest),
                    "status": "success"
                })

            except Exception as e:
                failed += 1
                results["details"].append({
                    "jpg": str(jpg_path),
                    "raw": str(raw_path),
                    "status": "failed",
                    "error": str(e)
                })
                try:
                    dest.unlink(missing_ok=True)
                except (OSError, UnboundLocalError):
                    pass

        results["extracted"] = extracted
        results["not_found"] = not_found
        results["failed"] = failed
        return results

    def _get_unique_dest(self, output: Path, raw_path: Path) -> Path:
        """获取唯一的目标路径"""
        dest = output / raw_path.name
        if not dest.exists():
            return dest

        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = output / f"{stem}_{counter}{suffix}"
            counter += 1
        return dest

    def _is_cancelled(self) -> bool:
        """线程安全检查取消状态"""
        with self._cancelled_lock:
            return self._cancelled

    def _set_cancelled(self, value: bool):
        """线程安全设置取消状态"""
        with self._cancelled_lock:
            self._cancelled = value

    def cancel(self):
        """取消操作"""
        self._set_cancelled(True)
        logger.info("RAW提取操作已取消")
