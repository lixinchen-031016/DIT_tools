"""设置对话框「存储卡」分组的高级 SOP 混合控件测试。

覆盖：SOP 模块化勾选、RAW 输出目录 PathField、重命名模板预设下拉、
报告路径浏览按钮、post-ingest 钩子预设下拉的交互与持久化。
"""

from DITWorkstation.App import config
from DITWorkstation.Services.card_automation_service import SOP_STEPS
from DITWorkstation.Views.Widgets.settings_dialog import (
    _RENAME_PATTERN_PRESETS,
    SettingsDialog,
    _post_ingest_hook_presets,
)


def _make_dialog():
    dialog = SettingsDialog()
    dialog.show()
    return dialog


def test_sop_step_checkboxes_exist_and_modular():
    """SOP 步骤按模块渲染为勾选框，勾选即按 SOP_STEPS 顺序拼接保存"""
    dialog = _make_dialog()
    try:
        assert set(dialog._sop_step_checks) == set(SOP_STEPS)
        # 勾选 backup + report（乱序勾选，保存应按 SOP_STEPS 顺序）
        dialog._sop_step_checks["report"].setChecked(True)
        dialog._sop_step_checks["backup"].setChecked(True)
        assert config.auto_card_steps == ["backup", "report"]
        # 取消全部 → 空列表（运行期回退兼容开关）
        dialog._sop_step_checks["report"].setChecked(False)
        dialog._sop_step_checks["backup"].setChecked(False)
        assert config.auto_card_steps == []
    finally:
        dialog.close()


def test_sop_step_checks_roundtrip_from_config():
    """config 中已有 steps 时加载后勾选状态正确恢复"""
    config.auto_card_steps = ["import", "raw_extract"]
    dialog = _make_dialog()
    try:
        assert dialog._sop_step_checks["import"].isChecked()
        assert dialog._sop_step_checks["raw_extract"].isChecked()
        assert not dialog._sop_step_checks["backup"].isChecked()
    finally:
        dialog.close()
        config.auto_card_steps = []


def test_rename_combo_has_presets_and_saves_custom_text():
    """重命名模板下拉包含 4 个预设，手动输入自定义模板也可保存"""
    dialog = _make_dialog()
    try:
        items = [
            dialog.auto_card_rename_combo.itemText(i)
            for i in range(dialog.auto_card_rename_combo.count())
        ]
        assert items == list(_RENAME_PATTERN_PRESETS)
        assert dialog.auto_card_rename_combo.isEditable()
        # 手动输入
        dialog.auto_card_rename_combo.setEditText("{date}_{original}")
        dialog._save_advanced_card_automation()
        assert config.auto_card_rename_pattern == "{date}_{original}"
        # 选择预设 → 自动填入
        dialog._on_rename_preset_activated(0)
        assert dialog.auto_card_rename_combo.currentText() == _RENAME_PATTERN_PRESETS[0]
        assert config.auto_card_rename_pattern == _RENAME_PATTERN_PRESETS[0]
    finally:
        dialog.close()
        config.auto_card_rename_pattern = ""


def test_hook_combo_presets_and_editable():
    """钩子下拉包含平台预设（标签+命令），选择预设填入命令并可编辑保存"""
    presets = _post_ingest_hook_presets()
    assert presets, "当前平台应至少提供一个钩子预设"
    dialog = _make_dialog()
    try:
        assert dialog.post_ingest_hook_combo.isEditable()
        assert dialog.post_ingest_hook_combo.count() == len(presets)
        # 选择第一个预设 → 编辑框填入命令
        dialog._on_hook_preset_activated(0)
        assert dialog.post_ingest_hook_combo.currentText() == presets[0][1]
        assert config.post_ingest_hook_command == presets[0][1]
        # 手动清空 → 禁用
        dialog.post_ingest_hook_combo.setEditText("")
        dialog._save_advanced_card_automation()
        assert config.post_ingest_hook_command == ""
    finally:
        dialog.close()
        config.post_ingest_hook_command = ""


def test_raw_output_and_report_path_fields():
    """RAW 输出目录（PathField 可编辑）与报告路径（输入框+浏览按钮）混合输入"""
    dialog = _make_dialog()
    try:
        assert dialog.auto_card_raw_output_field.is_editable()
        dialog.auto_card_raw_output_field.set_path("/tmp/raw_out")
        dialog._save_advanced_card_automation()
        assert config.auto_card_raw_output_dir == "/tmp/raw_out"
        assert dialog.auto_card_report_browse_btn.text() == "浏览…"
        dialog.auto_card_report_path_edit.setText("/tmp/rep.pdf")
        dialog._save_advanced_card_automation()
        assert config.auto_card_report_path == "/tmp/rep.pdf"
    finally:
        dialog.close()
        config.auto_card_raw_output_dir = ""
        config.auto_card_report_path = ""
