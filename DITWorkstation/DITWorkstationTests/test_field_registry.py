import logging

from DITWorkstation.Services.repositories.field_registry import (
    FieldSpec,
    build_update_clause,
    field_registry,
)


def test_build_update_clause_skips_unregistered_fields(caplog):
    registry = field_registry("name", "path")

    sql, params = build_update_clause(
        registry, "items", "item_id", "item-1", name="new", unknown="ignored"
    )

    assert sql == "UPDATE items SET name = ?, updated_at = ? WHERE item_id = ?"
    assert params[0] == "new"
    assert len(params) == 3
    assert params[-1] == "item-1"
    assert "未注册字段 'unknown' 已忽略" in caplog.text


def test_build_update_clause_applies_serializer():
    registry = field_registry(FieldSpec("tags", serializer=lambda value: "|".join(value)))

    sql, params = build_update_clause(registry, "items", "item_id", "item-1", tags=["a", "b"])

    assert "tags = ?" in sql
    assert params[0] == "a|b"


def test_build_update_clause_returns_empty_for_no_registered_fields():
    sql, params = build_update_clause(
        field_registry("name"), "items", "item_id", "item-1", unknown="ignored"
    )

    assert sql == ""
    assert params == []


def test_build_update_clause_can_skip_updated_at_for_tables_without_timestamp():
    sql, params = build_update_clause(
        field_registry("rating"), "assets", "asset_id", "asset-1",
        touch_updated_at=False, rating=5,
    )

    assert sql == "UPDATE assets SET rating = ? WHERE asset_id = ?"
    assert params == [5, "asset-1"]
