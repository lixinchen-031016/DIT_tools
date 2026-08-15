"""丢失素材的重新链接服务。

匹配逻辑与 UI 分离：调用方先请求预览，再只提交已确定的匹配，避免扫描结果
直接覆盖用户路径。优先级为相对路径、文件名和大小、可选校验和。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from DITWorkstation.Models import ChecksumAlgorithm, OperationResult, OperationStatus
from DITWorkstation.Utils import normalize_path


@dataclass
class RelinkMatch:
    asset_id: str
    old_path: str
    candidate_paths: list[str] = field(default_factory=list)
    match_method: str = "unmatched"
    message: str = ""

    @property
    def selected_path(self) -> str:
        return self.candidate_paths[0] if len(self.candidate_paths) == 1 else ""

    @property
    def is_conflict(self) -> bool:
        return len(self.candidate_paths) > 1


@dataclass
class RelinkPreview:
    project_id: str
    new_root: str
    matches: list[RelinkMatch] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(bool(item.selected_path) for item in self.matches)

    @property
    def conflict_count(self) -> int:
        return sum(item.is_conflict for item in self.matches)

    @property
    def unmatched_count(self) -> int:
        return sum(not item.candidate_paths for item in self.matches)


class AssetRelinkService:
    """预览并提交移动后素材的路径重定位。"""

    def __init__(self, db_service, checksum_service=None):
        self.db_service = db_service
        self.checksum_service = checksum_service

    def preview(
        self,
        project_id: str,
        new_root: str,
        old_root: Optional[str] = None,
        verify_checksum: bool = False,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> RelinkPreview:
        root = Path(new_root)
        if not root.is_dir():
            raise NotADirectoryError(f"重新链接目录不存在: {new_root}")

        missing_assets = (
            asset for asset in self.db_service.iter_project_assets(project_id)
            if not asset.file_path or not Path(asset.file_path).is_file()
        )
        files = self._scan_files(root, cancel_check)
        by_name_size: dict[tuple[str, int], list[Path]] = {}
        for path in files:
            by_name_size.setdefault((path.name.casefold(), path.stat().st_size), []).append(path)

        preview = RelinkPreview(project_id=project_id, new_root=str(root))
        # 素材路径入库时会经过 normalize_path；Windows 上测试或历史参数中的
        # `/legacy` 是当前盘符根下的相对根，而直接构造 Path 后仍可能是无盘符
        # 路径，无法与已规范化的 `C:\\legacy\\...` 做 relative_to 比较。
        # 对 old_root 使用同一规则，确保两侧处于同一绝对路径语义空间。
        old_root_path = Path(normalize_path(old_root)) if old_root else None
        for asset in missing_assets:
            self._raise_if_cancelled(cancel_check)
            candidates: list[Path] = []
            method = "unmatched"
            if old_root_path and asset.file_path:
                try:
                    relative = Path(asset.file_path).relative_to(old_root_path)
                    relative_candidate = root / relative
                    if relative_candidate.is_file() and relative_candidate.stat().st_size == asset.file_size:
                        candidates = [relative_candidate]
                        method = "relative_path"
                except ValueError:
                    pass
            if not candidates:
                candidates = by_name_size.get((asset.file_name.casefold(), asset.file_size), [])
                method = "name_and_size" if candidates else "unmatched"
            if verify_checksum and len(candidates) > 1 and asset.checksum_value:
                candidates = self._checksum_matches(asset, candidates, cancel_check)
                if candidates:
                    method = "checksum"

            preview.matches.append(RelinkMatch(
                asset_id=asset.asset_id,
                old_path=asset.file_path,
                candidate_paths=[normalize_path(str(path)) for path in candidates],
                match_method=method,
                message="存在多个候选文件" if len(candidates) > 1 else "",
            ))
        return preview

    def apply(
        self,
        preview: RelinkPreview,
        selections: Optional[Iterable[tuple[str, str]]] = None,
    ) -> OperationResult:
        chosen = dict(selections or (
            (match.asset_id, match.selected_path)
            for match in preview.matches if match.selected_path
        ))
        if not chosen:
            return OperationResult(OperationStatus.INVALID, "没有可提交的重新链接结果")

        updates = []
        for asset_id, path in chosen.items():
            candidate = Path(path)
            if not candidate.is_file():
                return OperationResult(OperationStatus.CONFLICT, f"候选文件已不存在: {path}")
            updates.append((asset_id, normalize_path(str(candidate)), candidate.name, candidate.stat().st_size))
        return self.db_service.relink_media_assets(preview.project_id, updates)

    @staticmethod
    def _scan_files(root: Path, cancel_check) -> list[Path]:
        files = []
        for path in root.rglob("*"):
            AssetRelinkService._raise_if_cancelled(cancel_check)
            if path.is_file():
                files.append(path)
        return files

    def _checksum_matches(self, asset, candidates: list[Path], cancel_check) -> list[Path]:
        if self.checksum_service is None:
            return candidates
        try:
            algorithm = ChecksumAlgorithm(asset.checksum_algorithm)
        except ValueError:
            return candidates
        matches = []
        for candidate in candidates:
            self._raise_if_cancelled(cancel_check)
            checksum = self.checksum_service.compute_file_checksum(
                str(candidate), algorithm, cancel_check=cancel_check
            )
            if checksum.hash_value == asset.checksum_value:
                matches.append(candidate)
        return matches

    @staticmethod
    def _raise_if_cancelled(cancel_check) -> None:
        if cancel_check and cancel_check():
            raise InterruptedError("重新链接已取消")
