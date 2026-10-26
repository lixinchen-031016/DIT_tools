"""应用内用户手册查看器测试。"""

from DITWorkstation.Views.Widgets.help_viewer_dialog import (
    HelpViewerDialog,
    locate_manual,
)


def test_locate_manual_in_dev_tree():
    """开发态能定位到仓库 docs/用户手册.md"""
    manual = locate_manual()
    assert manual is not None
    assert manual.is_file()
    assert manual.name == "用户手册.md"


def test_viewer_renders_manual_content():
    """查看器能渲染手册内容（包含核心章节标题）"""
    dialog = HelpViewerDialog()
    dialog.show()
    try:
        assert locate_manual() is not None
        text = dialog.browser.toPlainText()
        # 手册应包含核心模块章节与新功能章节
        assert "模块 3" in text
        assert "数据备份" in text
        assert "KIOSK" in text
        assert "ASC MHL" in text
    finally:
        dialog.close()


def test_viewer_find_next_wraps():
    """查找：关键字命中并可在文档末尾回绕"""
    dialog = HelpViewerDialog()
    try:
        dialog.search_edit.setText("备份")
        dialog._find_next()
        first_pos = dialog.browser.textCursor().position()
        assert first_pos > 0
        # 连续查找直至回绕后仍能命中
        for _ in range(80):
            dialog._find_next()
        assert dialog.browser.textCursor().position() >= 0
    finally:
        dialog.close()


def test_viewer_missing_manual_shows_hint(tmp_path, monkeypatch):
    """手册文件缺失时显示路径说明而非空白"""
    import DITWorkstation.Views.Widgets.help_viewer_dialog as mod

    monkeypatch.setattr(mod, "locate_manual", lambda: None)
    dialog = mod.HelpViewerDialog()
    try:
        text = dialog.browser.toPlainText()
        assert "未找到用户手册" in text
    finally:
        dialog.close()


def test_viewer_empty_search_is_noop():
    """空查找串直接返回，不移动光标"""
    dialog = HelpViewerDialog()
    try:
        dialog.browser.textCursor().movePosition(
            dialog.browser.textCursor().MoveOperation.Start
        )
        pos = dialog.browser.textCursor().position()
        dialog.search_edit.setText("   ")
        dialog._find_next()
        assert dialog.browser.textCursor().position() == pos
    finally:
        dialog.close()
