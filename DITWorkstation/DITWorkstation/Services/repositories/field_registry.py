"""Declarative allowlists for database update operations."""
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from DITWorkstation.Models import ChecksumAlgorithm
from DITWorkstation.Utils import logger, now_local


@dataclass(frozen=True)
class FieldSpec:
    """A field that may be written by a database update operation."""

    name: str
    column: str | None = None
    serializer: Callable[[Any], Any] | None = None


def field_registry(*specs: FieldSpec | str) -> dict[str, FieldSpec]:
    """Build a field-name to specification mapping."""
    return {
        spec if isinstance(spec, str) else spec.name:
        FieldSpec(spec) if isinstance(spec, str) else spec
        for spec in specs
    }


def build_update_clause(
    registry: dict[str, FieldSpec],
    table: str,
    id_column: str,
    id_value: Any,
    touch_updated_at: bool = True,
    **kwargs: Any,
) -> tuple[str, list[Any]]:
    """Build a parameterised UPDATE statement from registered fields only."""
    allowed_columns = TABLE_FIELD_WHITELIST.get(table)
    if allowed_columns is None:
        raise ValueError(f"不允许更新的表名: {table}")
    expected_id_column = TABLE_ID_WHITELIST[table]
    if id_column != expected_id_column:
        raise ValueError(
            f"表 {table} 的主键列必须是 {expected_id_column}，收到 {id_column}"
        )

    set_parts: list[str] = []
    params: list[Any] = []
    for key, value in kwargs.items():
        spec = registry.get(key)
        if spec is None:
            logger.warning(f"update {table}: 未注册字段 '{key}' 已忽略")
            continue
        column = spec.column or spec.name
        if column not in allowed_columns:
            raise ValueError(f"表 {table} 不允许更新列: {column}")
        if spec.serializer is not None:
            value = spec.serializer(value)
        set_parts.append(f"{column} = ?")
        params.append(value)
    if not set_parts:
        return "", []

    if touch_updated_at:
        set_parts.append("updated_at = ?")
        params.append(now_local().isoformat())
    params.append(id_value)
    return f"UPDATE {table} SET {', '.join(set_parts)} WHERE {id_column} = ?", params


def _pipe_join(value: Any) -> Any:
    return "|".join(value) if isinstance(value, list) else value


def _bool_as_int(value: Any) -> Any:
    return int(value) if isinstance(value, bool) else value


def _truthy_as_int(value: Any) -> int:
    return int(bool(value))


def _isoformat(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json_list(value: Any) -> str:
    return json.dumps(list(value), ensure_ascii=False)


def _algorithm_value(value: Any) -> str:
    return value.value if isinstance(value, ChecksumAlgorithm) else str(value)


WORKSPACE_FIELDS = field_registry("name", "path", "description")
PROJECT_FIELDS = field_registry("name", "description", "base_path", "workspace_id")
PROJECT_TEMPLATE_FIELDS = field_registry("name", "description", "base_path", "notes")
BACKUP_TEMPLATE_FIELDS = field_registry(
    "name",
    FieldSpec("target_paths", serializer=_json_list),
    FieldSpec("algorithm", serializer=_algorithm_value),
    FieldSpec("verify_after_copy", serializer=_truthy_as_int),
    "description",
)
MEDIA_ASSET_FIELDS = field_registry(
    "file_path", "file_name", "file_size", "file_type", "asset_type",
    "checksum_algorithm", "checksum_value", "scene", "shot", "take",
    FieldSpec("date_taken", serializer=_isoformat),
    "camera_make", "camera_model",
    FieldSpec("backup_locations", serializer=_pipe_join),
    "log_id", FieldSpec("is_working_copy", serializer=_bool_as_int),
    "original_path", "width", "height", "duration_seconds", "lens_model",
    "focal_length", "video_metadata", "rating", "tags", "notes",
)

# Dynamic identifiers must come from these allowlists. Values remain SQL parameters.
TABLE_ID_WHITELIST = {
    "workspaces": "workspace_id",
    "projects": "project_id",
    "project_templates": "template_id",
    "backup_templates": "template_id",
    "media_assets": "asset_id",
}
TABLE_FIELD_WHITELIST = {
    "workspaces": set(WORKSPACE_FIELDS) | {"updated_at"},
    "projects": set(PROJECT_FIELDS) | {"updated_at"},
    "project_templates": set(PROJECT_TEMPLATE_FIELDS) | {"updated_at"},
    "backup_templates": set(BACKUP_TEMPLATE_FIELDS) | {"updated_at"},
    "media_assets": set(MEDIA_ASSET_FIELDS),
}
