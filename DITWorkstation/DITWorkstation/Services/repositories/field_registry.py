"""Declarative allowlists for database update operations."""
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Callable

from DITWorkstation.Utils import logger
from DITWorkstation.Models import ChecksumAlgorithm


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
    set_parts: list[str] = []
    params: list[Any] = []
    for key, value in kwargs.items():
        spec = registry.get(key)
        if spec is None:
            logger.warning(f"update {table}: 未注册字段 '{key}' 已忽略")
            continue
        if spec.serializer is not None:
            value = spec.serializer(value)
        set_parts.append(f"{spec.column or spec.name} = ?")
        params.append(value)
    if not set_parts:
        return "", []

    if touch_updated_at:
        set_parts.append("updated_at = ?")
        params.append(datetime.now().isoformat())
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
