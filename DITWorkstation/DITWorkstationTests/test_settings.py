"""应用设置持久化测试"""
from pathlib import Path

from DITWorkstation.Utils import common


def _patch_settings_path(monkeypatch, tmp_path):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(common, "_get_settings_path", lambda: target)
    return target


def test_save_load_app_settings_roundtrip(tmp_path, monkeypatch):
    _patch_settings_path(monkeypatch, tmp_path)
    common.save_app_settings(verify_after_copy=False, auto_detect_volume=False)
    cfg = common.load_app_settings()
    assert cfg["verify_after_copy"] is False
    assert cfg["auto_detect_volume"] is False


def test_save_app_settings_merges_not_overwrites(tmp_path, monkeypatch):
    _patch_settings_path(monkeypatch, tmp_path)
    common.save_app_settings(verify_after_copy=False)
    common.save_app_settings(auto_detect_volume=True)
    cfg = common.load_app_settings()
    assert cfg == {"verify_after_copy": False, "auto_detect_volume": True}


def test_load_app_settings_missing_file_returns_empty(tmp_path, monkeypatch):
    _patch_settings_path(monkeypatch, tmp_path)
    assert common.load_app_settings() == {}


def test_corrupt_settings_are_quarantined_and_defaults_are_used(tmp_path, monkeypatch):
    target = _patch_settings_path(monkeypatch, tmp_path)
    target.write_text('{"app_config": ', encoding="utf-8")

    assert common.load_app_settings() == {}
    quarantined = list(tmp_path.glob("settings.json.corrupt.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == '{"app_config": '
    assert not target.exists()


def test_invalid_settings_shape_is_quarantined(tmp_path, monkeypatch):
    target = _patch_settings_path(monkeypatch, tmp_path)
    target.write_text('["not", "an object"]', encoding="utf-8")

    assert common.load_app_settings() == {}
    assert len(list(tmp_path.glob("settings.json.corrupt.*"))) == 1


def test_invalid_known_config_values_fall_back_without_dropping_other_settings(tmp_path, monkeypatch):
    target = _patch_settings_path(monkeypatch, tmp_path)
    target.write_text(
        '{"app_config": {"verify_after_copy": "yes", "search_page_size": 0, '
        '"usage_mode": "personal", "future_field": {"enabled": true}}}',
        encoding="utf-8",
    )

    assert common.load_app_settings() == {
        "usage_mode": "personal", "future_field": {"enabled": True}
    }


def test_save_settings_uses_atomic_replacement(tmp_path, monkeypatch):
    target = _patch_settings_path(monkeypatch, tmp_path)
    target.write_text('{"old": true}', encoding="utf-8")

    assert common._save_settings({"app_config": {"verify_after_copy": False}}) is True
    import json
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "app_config": {"verify_after_copy": False}
    }
    assert not list(tmp_path.glob(".settings.json.*.tmp"))


def test_save_settings_does_not_clobber_recent_paths(tmp_path, monkeypatch):
    target = _patch_settings_path(monkeypatch, tmp_path)
    common.add_recent_path("/tmp/source", category="import_source")
    common.save_app_settings(verify_after_copy=False)
    import json
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert "recent_directories_import_source" in raw
    assert raw["app_config"]["verify_after_copy"] is False


def test_export_settings_writes_validated_json(tmp_path, monkeypatch):
    _patch_settings_path(monkeypatch, tmp_path)
    common.save_app_settings(verify_after_copy=False)
    target = tmp_path / "export.json"

    assert common.export_settings(target)
    import json
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "app_config": {"verify_after_copy": False}
    }


def test_import_settings_deep_merge_preserves_local_values(tmp_path, monkeypatch):
    target = _patch_settings_path(monkeypatch, tmp_path)
    common._save_settings({
        "app_config": {"verify_after_copy": False, "auto_detect_volume": True},
        "recent_directories_import_source": ["/local/source"],
    })
    source = tmp_path / "import.json"
    source.write_text(
        '{"app_config": {"max_parallel_copies": 8}, "future_section": {"enabled": true}}',
        encoding="utf-8",
    )

    assert common.import_settings(source, merge=True)
    import json
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "app_config": {
            "verify_after_copy": False,
            "auto_detect_volume": True,
            "max_parallel_copies": 8,
        },
        "recent_directories_import_source": ["/local/source"],
        "future_section": {"enabled": True},
    }


def test_invalid_import_does_not_overwrite_current_settings(tmp_path, monkeypatch):
    target = _patch_settings_path(monkeypatch, tmp_path)
    common.save_app_settings(verify_after_copy=False)
    source = tmp_path / "invalid.json"
    source.write_text('{"app_config": ', encoding="utf-8")

    assert not common.import_settings(source)
    assert target.read_text(encoding="utf-8") == '{\n  "app_config": {\n    "verify_after_copy": false\n  }\n}'


def test_apply_saved_config_sets_known_fields(tmp_path, monkeypatch):
    _patch_settings_path(monkeypatch, tmp_path)
    common.save_app_settings(verify_after_copy=False, auto_detect_volume=False, unknown_key=1)
    common.apply_saved_config()
    from DITWorkstation.App import config
    assert config.verify_after_copy is False
    assert config.auto_detect_volume is False
    assert not hasattr(config, "unknown_key")


def test_apply_saved_card_automation_config(tmp_path, monkeypatch):
    _patch_settings_path(monkeypatch, tmp_path)
    common.save_app_settings(
        auto_card_automation_enabled=True,
        auto_card_import=False,
        auto_card_backup=True,
        auto_card_project_id="project-1",
        auto_card_template_id="template-1",
    )
    common.apply_saved_config()
    from DITWorkstation.App import config
    assert config.auto_card_automation_enabled is True
    assert config.auto_card_import is False
    assert config.auto_card_backup is True
    assert config.auto_card_project_id == "project-1"
    assert config.auto_card_template_id == "template-1"


def test_path_settings_roundtrip_as_path_objects(tmp_path, monkeypatch):
    """路径类配置以字符串持久化，启动时恢复为 Path 对象"""
    _patch_settings_path(monkeypatch, tmp_path)
    target = tmp_path / "app_data"
    # 先注册原值以便测试后恢复，避免污染全局 config 影响其他测试
    from DITWorkstation.App import config as _config
    for field in ("db_dir", "report_dir", "log_dir", "thumbnail_cache_dir"):
        monkeypatch.setattr(_config, field, getattr(_config, field))
    common.save_app_settings(
        db_dir=str(target),
        report_dir=str(tmp_path / "reports"),
        log_dir=str(tmp_path / "logs"),
        thumbnail_cache_dir=str(tmp_path / "thumbs"),
    )
    common.apply_saved_config()
    assert _config.db_dir == target
    assert _config.report_dir == tmp_path / "reports"
    assert _config.log_dir == tmp_path / "logs"
    assert _config.thumbnail_cache_dir == tmp_path / "thumbs"
    assert isinstance(_config.thumbnail_cache_dir, Path)


def test_db_dir_change_survives_restart(tmp_path, monkeypatch):
    """更换数据库目录后，重启（重新读取配置）应仍使用新目录。"""
    from DITWorkstation.App import config as _config
    for field in ("db_dir", "settings_dir"):
        monkeypatch.setattr(_config, field, getattr(_config, field))

    default_dir = tmp_path / "default"
    new_db_dir = tmp_path / "new_db"
    _config.db_dir = default_dir
    _config.settings_dir = tmp_path / "settings"

    # 模拟设置对话框：保存新的数据库目录
    common.save_app_settings(db_dir=str(new_db_dir))

    # 模拟重启：config 回到默认数据库目录，再从 settings.json 恢复
    _config.db_dir = default_dir
    common.apply_saved_config()

    assert _config.db_dir == new_db_dir
    # 设置文件必须位于与数据库目录无关的固定位置
    assert common._get_settings_path().parent == _config.effective_settings_dir
    assert not (new_db_dir / "settings.json").exists()


def test_log_files_summary_and_delete(tmp_path, monkeypatch):
    """日志文件统计与删除功能（重设到临时日志目录）"""
    from DITWorkstation.App import config
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(config, "log_dir", log_dir)
    common.logger.set_log_dir(log_dir)
    # 触发一次写入，产生日志文件
    common.logger.info("测试日志写入")
    count, size = common.log_files_summary()
    assert count >= 1
    assert size > 0
    removed = common.delete_log_files()
    assert removed == count
    count_after, size_after = common.log_files_summary()
    # 删除后重新打开句柄会生成 1 个空的新日志文件
    assert count_after <= 1
    assert size_after == 0


def test_log_files_summary_missing_dir(tmp_path, monkeypatch):
    from DITWorkstation.App import config
    monkeypatch.setattr(config, "log_dir", tmp_path / "no_such_dir")
    assert common.log_files_summary() == (0, 0)


# ===== 使用场景（功能模式开关）持久化 =====

def test_apply_saved_config_restores_usage_mode(tmp_path, monkeypatch):
    """usage_mode 保存到 app_config 后，apply_saved_config 能恢复到 AppConfig。"""
    _patch_settings_path(monkeypatch, tmp_path)
    from DITWorkstation.App import config as _config
    monkeypatch.setattr(_config, "usage_mode", "team")
    common.save_app_settings(usage_mode="personal")
    common.apply_saved_config()
    assert _config.usage_mode == "personal"


def test_invalid_usage_mode_falls_back_to_team_on_read(tmp_path, monkeypatch):
    """apply_saved_config 原样恢复存储值；非法值由 feature_flags 读取时回退团队模式。"""
    _patch_settings_path(monkeypatch, tmp_path)
    from DITWorkstation.App import config as _config
    monkeypatch.setattr(_config, "usage_mode", "team")
    common.save_app_settings(usage_mode="foo")
    common.apply_saved_config()
    assert _config.usage_mode == "foo"  # 持久化层不做校验，原样恢复
    from DITWorkstation.App import feature_flags
    assert feature_flags.get_usage_mode() == feature_flags.UsageMode.TEAM
