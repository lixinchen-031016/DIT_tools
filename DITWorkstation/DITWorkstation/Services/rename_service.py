"""文件重命名服务

MetadataService 已拆离到 metadata_service.py（两者零耦合）。
向后兼容：`from rename_service import MetadataService` 仍可用（re-export）。
"""
import re
from collections.abc import Callable
from pathlib import Path

from DITWorkstation.Models import OperationResult, OperationStatus, RenameRule
from DITWorkstation.Utils import now_local


class RenameService:
    """文件重命名服务"""

    def preview_rename(self, files: list[str], rule: RenameRule) -> list[tuple[str, str]]:
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

    def execute_rename(
        self,
        files: list[str],
        rule: RenameRule,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[tuple[str, str]]:
        """
        执行重命名

        Args:
            files: 文件路径列表
            rule: 重命名规则
            progress_callback: 进度回调 (current, total, filename)，
                current 从1开始，每处理一个文件后调用一次

        Returns:
            [(原路径, 新路径)] 成功重命名的列表
        """
        rename_pairs = self.preview_rename(files, rule)
        results = []
        total = len(rename_pairs)

        for i, (old_path, new_path) in enumerate(rename_pairs):
            old = Path(old_path)
            new = Path(new_path)

            if not old.exists():
                if progress_callback:
                    progress_callback(i + 1, total, new.name)
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

            if progress_callback:
                progress_callback(i + 1, total, new.name)

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
        name = name.replace("{date}", now_local().strftime("%Y%m%d"))

        # 清理多余分隔符
        name = re.sub(r'[_\-\s]+', '_', name).strip('_')

        return f"{name}{suffix}"

    def batch_rename_with_association(
        self,
        file_groups: list[list[str]],
        rule: RenameRule
    ) -> list[tuple[str, str]]:
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

    def rollback_rename(
        self,
        db_service,
        rename_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> OperationResult:
        """回退一次已记录的重命名。

        只有全部新路径仍存在、旧路径仍为空且该记录未回退时才执行，避免覆盖
        用户在此后创建或再次重命名的文件。
        """
        history = db_service.get_rename_history(rename_id)
        if not history:
            return history
        mappings = history.value["mappings"]
        for old_path, new_path in mappings:
            old, new = Path(old_path), Path(new_path)
            if not new.is_file():
                return OperationResult(OperationStatus.CONFLICT, f"无法回退，重命名后的文件已变化: {new}")
            if old.exists():
                return OperationResult(OperationStatus.CONFLICT, f"无法回退，原路径已被占用: {old}")

        reverted = []
        try:
            total = len(mappings)
            for index, (old_path, new_path) in enumerate(mappings, start=1):
                Path(new_path).rename(old_path)
                reverted.append((old_path, new_path))
                if progress_callback:
                    progress_callback(index, total, Path(old_path).name)
        except OSError as exc:
            for old_path, new_path in reversed(reverted):
                try:
                    Path(old_path).rename(new_path)
                except OSError:
                    pass
            return OperationResult(OperationStatus.ERROR, f"文件回退失败: {exc}")

        for old_path, new_path in mappings:
            db_service.update_asset_path_by_old_path(new_path, old_path, Path(old_path).name)
        marked = db_service.mark_rename_history_reverted(rename_id)
        if not marked:
            return marked
        return OperationResult(OperationStatus.SUCCESS, affected_count=len(mappings), recovery_id=rename_id)


# 向后兼容：MetadataService 已拆离到 metadata_service.py
# 现有代码 `from rename_service import MetadataService` 仍可用
from DITWorkstation.Services.metadata_service import MetadataService  # noqa: F401
