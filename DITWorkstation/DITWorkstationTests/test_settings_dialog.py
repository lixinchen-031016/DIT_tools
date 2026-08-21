"""设置对话框测试：布局、模式切换、配置持久化（复用 conftest 的 offscreen QApplication）"""
import pytest
from PySide6.QtWidgets import QComboBox, QGroupBox, QPushButton, QScrollArea

from DITWorkstation.App import config
from DITWorkstation.App.feature_flags import UsageMode, get_usage_mode, set_usage_mode
from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog


# ===== 对话框基本构造 =====

def test_settings_dialog_instantiation():
    """验证设置对话框可在无头模式下正常构造。"""
    dialog = SettingsDialog()
    assert dialog.windowTitle() == "设置"
    assert dialog.width() == 640
    assert dialog.height() == 640
    # 应包含滚动区域
    assert dialog.findChild(QScrollArea) is not None
    dialog.close()


def test_settings_dialog_has_usage_mode_selector():
    """验证设置对话框包含使用场景切换下拉框。"""
    dialog = SettingsDialog()
    combo = dialog.findChild(QComboBox)
    assert combo is not None, "应存在模式选择下拉框"
    # 应包含两种模式
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert any("团队模式" in t for t in texts)
    assert any("个人模式" in t for t in texts)
    dialog.close()


def test_settings_dialog_has_group_boxes():
    """验证设置对话框包含预期的分组框。"""
    dialog = SettingsDialog()
    groups = dialog.findChildren(QGroupBox)
    group_titles = [g.title() for g in groups]
    # 至少应包含使用场景、数据存储位置、清理临时文件、运行参数
    assert any("使用场景" in t for t in group_titles), "应包含使用场景分组"
    assert any("数据存储" in t for t in group_titles), "应包含数据存储分组"
    assert any("清理" in t for t in group_titles), "应包含清理分组"
    assert any("最近路径" in t for t in group_titles), "应包含最近路径分组"
    assert any("备份默认选项" in t for t in group_titles), "应包含备份默认选项分组"
    assert any("存储卡" in t for t in group_titles), "应包含存储卡分组"
    dialog.close()


# ===== 使用场景模式切换（直接通过 set_usage_mode 测试持久化） =====

def test_usage_mode_persistence_after_switch():
    """验证 set_usage_mode 的配置持久化：切换后应能通过 get_usage_mode 读取到正确值。

    注意：set_usage_mode 内部调用了 save_app_settings，因此模式是持久化的。
    _on_usage_mode_changed 调用 set_usage_mode，所以对话框中的模式切换也能正确持久化。
    """
    # 保存原始模式
    original_mode = get_usage_mode()

    # 切换到个人模式
    set_usage_mode(UsageMode.PERSONAL)
    assert get_usage_mode() == UsageMode.PERSONAL

    # 切换到团队模式
    set_usage_mode(UsageMode.TEAM)
    assert get_usage_mode() == UsageMode.TEAM

    # 恢复原始模式
    set_usage_mode(original_mode)
    assert get_usage_mode() == original_mode


def test_usage_mode_switch_to_personal():
    """验证 set_usage_mode(PERSONAL) 正确持久化（不通过对话框，避免 QMessageBox 阻塞）。"""
    # 先确保当前为团队模式
    if get_usage_mode() != UsageMode.TEAM:
        set_usage_mode(UsageMode.TEAM)
    assert get_usage_mode() == UsageMode.TEAM

    set_usage_mode(UsageMode.PERSONAL)
    # set_usage_mode 内部调用 save_app_settings，验证持久化
    assert get_usage_mode() == UsageMode.PERSONAL

    # 清理：恢复团队模式
    set_usage_mode(UsageMode.TEAM)


def test_usage_mode_switch_to_team():
    """验证 set_usage_mode(TEAM) 正确持久化（不通过对话框，避免 QMessageBox 阻塞）。"""
    # 先确保当前为个人模式
    if get_usage_mode() != UsageMode.PERSONAL:
        set_usage_mode(UsageMode.PERSONAL)
    assert get_usage_mode() == UsageMode.PERSONAL

    set_usage_mode(UsageMode.TEAM)
    assert get_usage_mode() == UsageMode.TEAM

    # 清理
    set_usage_mode(UsageMode.TEAM)


def test_dialog_usage_mode_combo_display():
    """验证对话框打开时下拉框显示当前模式（不切换模式，避免 QMessageBox 阻塞）。"""
    # 确保为团队模式
    set_usage_mode(UsageMode.TEAM)
    dialog = SettingsDialog()
    combo = dialog.findChild(QComboBox)
    current_data = combo.itemData(combo.currentIndex())
    assert current_data == UsageMode.TEAM.value, "团队模式下下拉框应默认选中团队模式"
    dialog.close()


def test_set_usage_mode_inside_dialog():
    """验证对话框中 _on_usage_mode_changed 调用的 set_usage_mode 能正确持久化。

    此测试仅验证 set_usage_mode 的持久化行为，不触发对话框的槽函数
    （避免 QMessageBox 在 offscreen 模式下阻塞）。
    """
    # 保存原始值
    original = get_usage_mode()

    # 直接调用 set_usage_mode（即 _on_usage_mode_changed 内部调用的方法）
    set_usage_mode(UsageMode.PERSONAL)
    assert get_usage_mode() == UsageMode.PERSONAL

    set_usage_mode(UsageMode.TEAM)
    assert get_usage_mode() == UsageMode.TEAM

    # 恢复原始模式
    if original != UsageMode.TEAM:
        set_usage_mode(original)
