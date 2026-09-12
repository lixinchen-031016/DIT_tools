"""功能模式开关单元测试（对应设计文档 9.1 节）

覆盖：
- 缺少 usage_mode 时回退团队模式
- 非法 usage_mode 值回退团队模式
- set_usage_mode 持久化到 app_config
- apply_saved_config 后模式恢复
- 团队模式 9 项导航 / 个人模式 7 项导航 / 极简模式 1 项导航（顺序正确）
- 个人模式关闭团队特性，团队模式全部开启，极简模式仅开启白名单特性
- 极简模式媒体保存目录的读写与持久化
"""

import pytest
from DITWorkstation.App import config
from DITWorkstation.App.feature_flags import (
    MINIMAL_NAV_KEYS,
    PERSONAL_NAV_KEYS,
    UsageMode,
    get_active_nav_index,
    get_active_nav_items,
    get_minimal_import_target_dir,
    get_usage_mode,
    is_enabled,
    is_minimal_mode,
    is_nav_enabled,
    is_personal_mode,
    is_team_mode,
    resolve_minimal_import_target_dir,
    set_minimal_import_target_dir,
    set_usage_mode,
)
from DITWorkstation.App.navigation import NAV_ITEMS, get_nav_index
from DITWorkstation.Utils import common


@pytest.fixture(autouse=True)
def _isolate_usage_mode(monkeypatch, tmp_path):
    """每个测试使用独立 settings.json，并在结束后恢复 config 相关字段。"""
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_path / "settings.json"
    )
    monkeypatch.setattr(config, "usage_mode", "team")
    monkeypatch.setattr(config, "minimal_import_target_dir", "")
    yield


# ===== 模式读取与回退 =====


def test_missing_usage_mode_defaults_to_team():
    assert get_usage_mode() == UsageMode.TEAM
    assert is_team_mode()
    assert not is_personal_mode()


def test_invalid_usage_mode_falls_back_to_team(monkeypatch):
    monkeypatch.setattr(config, "usage_mode", "foo")
    assert get_usage_mode() == UsageMode.TEAM
    assert is_team_mode()


def test_empty_usage_mode_falls_back_to_team(monkeypatch):
    monkeypatch.setattr(config, "usage_mode", "")
    assert get_usage_mode() == UsageMode.TEAM


def test_set_usage_mode_rejects_invalid_value():
    with pytest.raises(ValueError):
        set_usage_mode("foo")


# ===== 持久化与恢复 =====


def test_set_usage_mode_persists_to_app_config():
    set_usage_mode(UsageMode.PERSONAL)
    cfg = common.load_app_settings()
    assert cfg["usage_mode"] == "personal"
    assert config.usage_mode == "personal"


def test_set_usage_mode_accepts_string():
    set_usage_mode("personal")
    assert common.load_app_settings()["usage_mode"] == "personal"


def test_apply_saved_config_restores_personal_mode(monkeypatch):
    """set_usage_mode 写入后，模拟重启（config 复位 + apply_saved_config）恢复个人模式。"""
    set_usage_mode(UsageMode.PERSONAL)
    # 模拟重启：内存配置回到默认值
    monkeypatch.setattr(config, "usage_mode", "team")
    common.apply_saved_config()
    assert config.usage_mode == "personal"
    assert is_personal_mode()


def test_old_settings_without_usage_mode_stays_team():
    """无 usage_mode 的旧设置文件：apply_saved_config 后仍为团队模式。"""
    common.save_app_settings(verify_after_copy=False)
    common.apply_saved_config()
    assert config.usage_mode == "team"
    assert is_team_mode()


# ===== 导航过滤 =====


def test_team_mode_activates_all_nav_items():
    active = get_active_nav_items()
    assert [k for k, _, _ in active] == [k for k, _, _ in NAV_ITEMS]
    assert len(active) == 9


def test_personal_mode_activates_seven_nav_items(monkeypatch):
    monkeypatch.setattr(config, "usage_mode", "personal")
    active = get_active_nav_items()
    assert [k for k, _, _ in active] == list(PERSONAL_NAV_KEYS)
    assert len(active) == 7
    # 顺序必须与 NAV_ITEMS 中的相对顺序一致
    full_order = [k for k, _, _ in NAV_ITEMS]
    assert [k for k in full_order if k in PERSONAL_NAV_KEYS] == list(PERSONAL_NAV_KEYS)


def test_is_nav_enabled(monkeypatch):
    assert is_nav_enabled("log")
    assert is_nav_enabled("report")
    monkeypatch.setattr(config, "usage_mode", "personal")
    assert not is_nav_enabled("log")
    assert not is_nav_enabled("report")
    assert is_nav_enabled("dashboard")
    assert is_nav_enabled("backup")


def test_get_nav_index_uses_active_list(monkeypatch):
    """get_nav_index 基于激活列表：个人模式下 log/report 返回 None，raw 索引前移。"""
    # 团队模式：log=3, raw=4
    assert get_nav_index("log") == 3
    assert get_nav_index("raw") == 4
    assert get_nav_index("nonexistent") is None

    monkeypatch.setattr(config, "usage_mode", "personal")
    assert get_nav_index("log") is None
    assert get_nav_index("report") is None
    # 个人模式激活列表：dashboard=0 import=1 backup=2 raw=3 rename=4 search=5 asset_info=6
    assert get_nav_index("raw") == 3
    assert get_nav_index("asset_info") == 6
    assert get_active_nav_index("backup") == 2


# ===== 组件级特性开关 =====


def test_team_mode_enables_all_features():
    for feature in (
        "workspace_selector",
        "shooting_log",
        "ratings",
        "report",
        "multi_target_backup",
        "backup_templates",
        "mhl_export",
        "project_templates",
        "archive_restore",
        "audit_panel",
        "sop_guide",
        "card_automation",
    ):
        assert is_enabled(feature), f"团队模式下 {feature} 应为开启"


def test_personal_mode_disables_team_only_features(monkeypatch):
    monkeypatch.setattr(config, "usage_mode", "personal")
    for feature in (
        "ratings",
        "archive_restore",
        "card_automation",
        "shooting_log",
        "report",
        "multi_target_backup",
        "backup_templates",
        "mhl_export",
        "project_templates",
        "audit_panel",
        "sop_guide",
        "workspace_selector",
    ):
        assert not is_enabled(feature), f"个人模式下 {feature} 应为关闭"


def test_unknown_feature_defaults_to_enabled(monkeypatch):
    """未知特性在两种模式下均按启用处理（避免新特性被意外禁用）。"""
    assert is_enabled("some_future_feature")
    monkeypatch.setattr(config, "usage_mode", "personal")
    assert is_enabled("some_future_feature")


# ===== 极简模式 =====


def test_minimal_mode_flag(monkeypatch):
    monkeypatch.setattr(config, "usage_mode", "minimal")
    assert is_minimal_mode()
    assert not is_team_mode()
    assert not is_personal_mode()
    assert get_usage_mode() == UsageMode.MINIMAL


def test_set_minimal_mode_persists(monkeypatch):
    set_usage_mode(UsageMode.MINIMAL)
    assert common.load_app_settings()["usage_mode"] == "minimal"
    assert config.usage_mode == "minimal"
    # 模拟重启后恢复
    monkeypatch.setattr(config, "usage_mode", "team")
    common.apply_saved_config()
    assert is_minimal_mode()


def test_minimal_mode_activates_only_import_nav(monkeypatch):
    monkeypatch.setattr(config, "usage_mode", "minimal")
    active = get_active_nav_items()
    assert [k for k, _, _ in active] == list(MINIMAL_NAV_KEYS)
    assert len(active) == 1
    assert is_nav_enabled("import")
    for key in (
        "dashboard",
        "backup",
        "raw",
        "rename",
        "search",
        "asset_info",
        "log",
        "report",
    ):
        assert not is_nav_enabled(key), f"极简模式下 {key} 不应可见"


def test_minimal_mode_nav_index(monkeypatch):
    """极简模式下 import 索引为 0，其余页面返回 None（调用方需容错）。"""
    monkeypatch.setattr(config, "usage_mode", "minimal")
    assert get_nav_index("import") == 0
    assert get_nav_index("backup") is None
    assert get_nav_index("dashboard") is None
    assert get_active_nav_index("import") == 0
    assert get_active_nav_index("report") is None


def test_minimal_mode_disables_all_features(monkeypatch):
    """极简模式采用白名单：任何已知或未知特性均为关闭。"""
    monkeypatch.setattr(config, "usage_mode", "minimal")
    for feature in (
        "workspace_selector",
        "shooting_log",
        "ratings",
        "report",
        "multi_target_backup",
        "backup_templates",
        "mhl_export",
        "project_templates",
        "archive_restore",
        "audit_panel",
        "sop_guide",
        "card_automation",
        "task_history",
        "some_future_feature",
    ):
        assert not is_enabled(feature), f"极简模式下 {feature} 应为关闭"


def test_minimal_nav_order_matches_nav_items(monkeypatch):
    """激活项顺序必须与 NAV_ITEMS 的相对顺序一致。"""
    monkeypatch.setattr(config, "usage_mode", "minimal")
    full_order = [k for k, _, _ in NAV_ITEMS]
    active_order = [k for k, _, _ in get_active_nav_items()]
    assert [k for k in full_order if k in MINIMAL_NAV_KEYS] == active_order


# ===== 极简模式媒体保存目录 =====


def test_minimal_import_target_dir_defaults_empty():
    assert get_minimal_import_target_dir() == ""
    assert resolve_minimal_import_target_dir("项目A") == ""


def test_set_minimal_import_target_dir_persists(tmp_path):
    target = tmp_path / "media_out"
    saved = set_minimal_import_target_dir(target)
    assert saved == str(target)
    assert target.is_dir(), "设置保存目录时应按需创建目录"
    assert get_minimal_import_target_dir() == str(target)
    # 写入 settings.json 的 app_config
    assert common.load_app_settings()["minimal_import_target_dir"] == str(target)


def test_set_minimal_import_target_dir_accepts_string(tmp_path):
    target = tmp_path / "out2"
    set_minimal_import_target_dir(str(target))
    assert get_minimal_import_target_dir() == str(target)


def test_set_minimal_import_target_dir_rejects_unwritable(tmp_path):
    """父路径是普通文件时无法创建目录，应抛 ValueError 且不写入。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        set_minimal_import_target_dir(blocker / "child")
    assert get_minimal_import_target_dir() == ""


def test_clear_minimal_import_target_dir(tmp_path):
    set_minimal_import_target_dir(tmp_path / "media")
    assert set_minimal_import_target_dir("") == ""
    assert get_minimal_import_target_dir() == ""
    assert common.load_app_settings()["minimal_import_target_dir"] == ""


def test_resolve_minimal_import_target_dir_joins_project(tmp_path):
    base = tmp_path / "media"
    set_minimal_import_target_dir(base)
    assert resolve_minimal_import_target_dir("项目A") == str(base / "项目A")
    # 未传项目名时仅返回根目录
    assert resolve_minimal_import_target_dir() == str(base)


def test_minimal_import_target_dir_restored_after_reload(tmp_path, monkeypatch):
    """保存目录经 apply_saved_config 可在重启后恢复。"""
    target = tmp_path / "media_reload"
    set_minimal_import_target_dir(target)
    monkeypatch.setattr(config, "minimal_import_target_dir", "")
    common.apply_saved_config()
    assert get_minimal_import_target_dir() == str(target)
