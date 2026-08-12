"""素材标签关联表与检索测试"""
from DITWorkstation.Services.database_service import DatabaseService


def _make_asset(project_id, asset_id, tags=""):
    from DITWorkstation.Models import MediaAsset
    return MediaAsset(
        asset_id=asset_id,
        project_id=project_id,
        file_path=f"/p/{asset_id}.jpg",
        file_name=f"{asset_id}.jpg",
        tags=tags,
    )


def test_add_asset_syncs_tags(db_service, project):
    asset = _make_asset(project.project_id, "a1", "日戏, 主镜头,日戏")
    db_service.add_media_asset(asset)
    assert set(db_service.get_all_tags()) == {"日戏", "主镜头"}


def test_batch_add_syncs_tags(db_service, project):
    db_service.add_media_assets_batch([
        _make_asset(project.project_id, "a1", "日戏"),
        _make_asset(project.project_id, "a2", "夜景"),
    ])
    assert set(db_service.get_all_tags()) == {"日戏", "夜景"}


def test_update_asset_resyncs_tags(db_service, project):
    db_service.add_media_asset(_make_asset(project.project_id, "a1", "日戏"))
    assert db_service.update_media_asset("a1", tags="夜景,雨戏")
    assert set(db_service.get_all_tags()) == {"夜景", "雨戏"}
    assert db_service.search_assets(tag="日戏") == []


def test_update_asset_other_field_keeps_tags(db_service, project):
    db_service.add_media_asset(_make_asset(project.project_id, "a1", "日戏"))
    assert db_service.update_media_asset("a1", notes="重要镜头")
    assert db_service.get_all_tags() == ["日戏"]


def test_delete_asset_removes_tags(db_service, project):
    db_service.add_media_asset(_make_asset(project.project_id, "a1", "日戏"))
    db_service.delete_media_asset("a1")
    assert db_service.get_all_tags() == []


def test_tag_search_substring_and_case_insensitive(db_service, project):
    db_service.add_media_asset(_make_asset(project.project_id, "a1", "日戏,夜景"))
    db_service.add_media_asset(_make_asset(project.project_id, "a2", "TVC"))
    assert len(db_service.search_assets(tag="日")) == 1
    assert len(db_service.search_assets(tag="tvc")) == 1
    assert len(db_service.search_assets(tag="不存在")) == 0


def test_keyword_search_includes_notes(db_service, project):
    from DITWorkstation.Models import MediaAsset
    asset = MediaAsset(
        asset_id="a1", project_id=project.project_id,
        file_path="/p/a1.jpg", file_name="a1.jpg", notes="关键备用镜头",
    )
    db_service.add_media_asset(asset)
    assert len(db_service.search_assets(keyword="备用")) == 1


def test_count_and_pagination(db_service, project):
    db_service.add_media_assets_batch([
        _make_asset(project.project_id, f"a{i}", "日戏")
        for i in range(25)
    ])
    assert db_service.count_assets(tag="日戏") == 25
    page1 = db_service.search_assets(tag="日戏", limit=10, offset=0)
    page3 = db_service.search_assets(tag="日戏", limit=10, offset=20)
    assert len(page1) == 10 and len(page3) == 5
    # 无 offset 时与 limit 行为不变
    assert len(db_service.search_assets(tag="日戏", limit=10)) == 10


def test_legacy_tags_backfilled_on_migration(tmp_dir):
    """旧库（无 asset_tags 表但 tags 列有数据）迁移后应回填关联表"""
    import sqlite3
    from datetime import datetime
    db = DatabaseService(db_path=tmp_dir / "test.db")
    # 模拟旧数据：直接往 media_assets 写入带 tags 的行（绕过 add_media_asset）
    now = datetime.now().isoformat()
    conn = sqlite3.connect(str(tmp_dir / "test.db"))
    conn.execute(
        "INSERT INTO media_assets "
        "(asset_id, project_id, file_path, file_name, date_imported, tags) "
        "VALUES ('legacy1', 'default', '/p/l.jpg', 'l.jpg', ?, '旧标签, 遗留')",
        (now,),
    )
    conn.commit()
    conn.close()
    # 重置版本到 v1，触发 v2 迁移回填 asset_tags
    conn = sqlite3.connect(str(tmp_dir / "test.db"))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    db._migrate_db()
    assert set(db.get_all_tags()) == {"旧标签", "遗留"}
