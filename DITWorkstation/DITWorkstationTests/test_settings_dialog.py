"""设置对话框测试：布局、模式切换、配置持久化（复用 conftest 的 offscreen QApplication）"""

from DITWorkstation.App.feature_flags import UsageMode, get_usage_mode, set_usage_mode
from DITWorkstation.Views.Widgets.settings_dialog import SettingsDialog
from PySide6.QtWidgets import QComboBox, QGroupBox, QScrollArea

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


# ===== 极简模式：下拉项与保存目录设置项 =====


def test_settings_dialog_has_minimal_mode_option():
    """验证「界面模式」下拉包含极简模式选项。"""
    dialog = SettingsDialog()
    combo = dialog.findChild(QComboBox)
    texts = [combo.itemText(i) for i in range(combo.count())]
    datas = [combo.itemData(i) for i in range(combo.count())]
    assert any("极简模式" in t for t in texts)
    assert UsageMode.MINIMAL.value in datas
    dialog.close()


def test_settings_dialog_minimal_dir_item_visibility(monkeypatch):
    """保存目录设置项仅在「界面模式」选中极简模式时可见。"""
    from DITWorkstation.App import config

    monkeypatch.setattr(config, "usage_mode", "team")
    dialog = SettingsDialog()
    combo = dialog.findChild(QComboBox)

    combo.blockSignals(True)
    combo.setCurrentIndex(combo.findData(UsageMode.TEAM.value))
    combo.blockSignals(False)
    dialog._sync_minimal_dir_visibility()
    assert dialog.minimal_dir_widget.isVisibleTo(dialog) is False

    combo.blockSignals(True)
    combo.setCurrentIndex(combo.findData(UsageMode.MINIMAL.value))
    combo.blockSignals(False)
    dialog._sync_minimal_dir_visibility()
    assert dialog.minimal_dir_widget.isVisibleTo(dialog) is True
    dialog.close()


def test_settings_dialog_minimal_dir_shows_and_clears_path(monkeypatch, tmp_path):
    """保存目录设置项显示已保存路径，清除后回到未设置提示。"""
    from DITWorkstation.App import config
    from DITWorkstation.App.feature_flags import set_minimal_import_target_dir
    from DITWorkstation.Utils import common

    monkeypatch.setattr(
        common, "_get_settings_path", lambda: tmp_path / "settings.json"
    )
    monkeypatch.setattr(config, "usage_mode", "minimal")
    monkeypatch.setattr(config, "minimal_import_target_dir", "")

    dialog = SettingsDialog()
    try:
        assert "未设置" in dialog.minimal_dir_label.text()

        target = tmp_path / "minimal_out"
        set_minimal_import_target_dir(target)
        dialog._refresh_minimal_dir_label()
        assert str(target) in dialog.minimal_dir_label.text()

        set_minimal_import_target_dir("")
        dialog._refresh_minimal_dir_label()
        assert "未设置" in dialog.minimal_dir_label.text()
    finally:
        dialog.close()


def test_settings_dialog_hides_backup_groups_in_minimal_mode(monkeypatch):
    """极简模式隐藏备份/完整性等被禁用模块的设置分组。"""
    from DITWorkstation.App import config

    monkeypatch.setattr(config, "usage_mode", "minimal")
    dialog = SettingsDialog()
    try:
        groups = {g.title(): g for g in dialog.findChildren(QGroupBox)}
        assert groups["📦 备份默认选项"].isVisibleTo(dialog) is False
        assert groups["🛡 完整性校验"].isVisibleTo(dialog) is False
        # 使用场景分组必须保留：极简模式唯一的切换出口
        assert groups["🧭 使用场景"].isVisibleTo(dialog) is True
    finally:
        dialog.close()
