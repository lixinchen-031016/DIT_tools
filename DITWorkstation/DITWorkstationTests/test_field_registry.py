import logging

import pytest

from DITWorkstation.Services.repositories.field_registry import (
    FieldSpec,
    PROJECT_FIELDS,
    build_update_clause,
    field_registry,
)


def test_build_update_clause_skips_unregistered_fields(caplog):
    registry = field_registry("name", "path")

    sql, params = build_update_clause(
        registry, "projects", "project_id", "project-1", name="new", unknown="ignored"
    )

    assert sql == "UPDATE projects SET name = ?, updated_at = ? WHERE project_id = ?"
    assert params[0] == "new"
    assert len(params) == 3
    assert params[-1] == "project-1"
    assert "未注册字段 'unknown' 已忽略" in caplog.text


def test_build_update_clause_applies_serializer():
    registry = field_registry(FieldSpec("tags", serializer=lambda value: "|".join(value)))

    sql, params = build_update_clause(
        registry, "media_assets", "asset_id", "asset-1", touch_updated_at=False,
        tags=["a", "b"],
    )

    assert "tags = ?" in sql
    assert params[0] == "a|b"


def test_build_update_clause_returns_empty_for_no_registered_fields():
    sql, params = build_update_clause(
        field_registry("name"), "projects", "project_id", "project-1", unknown="ignored"
    )

    assert sql == ""
    assert params == []


def test_build_update_clause_can_skip_updated_at_for_tables_without_timestamp():
    sql, params = build_update_clause(
        field_registry("rating"), "media_assets", "asset_id", "asset-1",
        touch_updated_at=False, rating=5,
    )

    assert sql == "UPDATE media_assets SET rating = ? WHERE asset_id = ?"
    assert params == [5, "asset-1"]


def test_build_update_clause_rejects_unknown_table():
    with pytest.raises(ValueError, match="不允许更新的表名"):
        build_update_clause(field_registry("name"), "items", "item_id", "item-1", name="x")


def test_build_update_clause_rejects_wrong_id_column():
    with pytest.raises(ValueError, match="主键列"):
        build_update_clause(PROJECT_FIELDS, "projects", "id", "project-1", name="x")


def test_build_update_clause_rejects_unapproved_registered_column():
    registry = field_registry(FieldSpec("name", column="not_a_column"))
    with pytest.raises(ValueError, match="不允许更新列"):
        build_update_clause(registry, "projects", "project_id", "project-1", name="x")
