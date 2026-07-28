"""文件重命名服务

MetadataService 已拆离到 metadata_service.py（两者零耦合）。
向后兼容：`from rename_service import MetadataService` 仍可用（re-export）。
"""
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from datetime import datetime

from DITWorkstation.Models import RenameRule


class RenameService:
    """文件重命名服务"""

    def preview_rename(self, files: List[str], rule: RenameRule) -> List[Tuple[str, str]]:
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
        files: List[str],
        rule: RenameRule,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Tuple[str, str]]:
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
        name = name.replace("{date}", datetime.now().strftime("%Y%m%d"))

        # 清理多余分隔符
        name = re.sub(r'[_\-\s]+', '_', name).strip('_')

        return f"{name}{suffix}"

    def batch_rename_with_association(
        self,
        file_groups: List[List[str]],
        rule: RenameRule
    ) -> List[Tuple[str, str]]:
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


# 向后兼容：MetadataService 已拆离到 metadata_service.py
# 现有代码 `from rename_service import MetadataService` 仍可用
from DITWorkstation.Services.metadata_service import MetadataService  # noqa: E402,F401

