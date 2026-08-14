"""功能模式开关单元测试（对应设计文档 9.1 节）

覆盖：
- 缺少 usage_mode 时回退团队模式
- 非法 usage_mode 值回退团队模式
- set_usage_mode 持久化到 app_config
- apply_saved_config 后模式恢复
- 团队模式 9 项导航 / 个人模式 7 项导航（顺序正确）
- 个人模式关闭团队特性，团队模式全部开启
"""
import pytest

from DITWorkstation.App import config
from DITWorkstation.App import feature_flags
from DITWorkstation.App.feature_flags import (
    UsageMode,
    get_usage_mode,
    set_usage_mode,
    is_personal_mode,
    is_team_mode,
    is_nav_enabled,
    get_active_nav_items,
    get_active_nav_index,
    is_enabled,
    PERSONAL_NAV_KEYS,
)
from DITWorkstation.App.navigation import NAV_ITEMS, get_nav_index
from DITWorkstation.Utils import common


@pytest.fixture(autouse=True)
def _isolate_usage_mode(monkeypatch, tmp_path):
    """每个测试使用独立 settings.json，并在结束后恢复 config.usage_mode。"""
    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_path / "settings.json"
    )
    monkeypatch.setattr(config, "usage_mode", "team")
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
        "workspace_selector", "shooting_log", "ratings", "report",
        "multi_target_backup", "backup_templates", "mhl_export",
        "project_templates", "archive_restore", "audit_panel",
        "sop_guide", "card_automation",
    ):
        assert is_enabled(feature), f"团队模式下 {feature} 应为开启"


def test_personal_mode_disables_team_only_features(monkeypatch):
    monkeypatch.setattr(config, "usage_mode", "personal")
    for feature in (
        "ratings", "archive_restore", "card_automation",
        "shooting_log", "report", "multi_target_backup",
        "backup_templates", "mhl_export", "project_templates",
        "audit_panel", "sop_guide", "workspace_selector",
    ):
        assert not is_enabled(feature), f"个人模式下 {feature} 应为关闭"


def test_unknown_feature_defaults_to_enabled(monkeypatch):
    """未知特性在两种模式下均按启用处理（避免新特性被意外禁用）。"""
    assert is_enabled("some_future_feature")
    monkeypatch.setattr(config, "usage_mode", "personal")
    assert is_enabled("some_future_feature")
