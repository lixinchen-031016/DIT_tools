"""视图基类：提供 showEvent 节流刷新的公共逻辑。

用法：
    class MyView(RefreshOnShowView):
        def _on_show_refresh(self):
            # 在此实现实际刷新逻辑（仅当视图可见时才会被调用）
            self.selector.refresh()

设计要点：
- 200ms 节流：快速切导航时只执行最后一次刷新，避免反复打 DB
- 不可见时跳过：视图被隐藏时 showEvent 不会触发，无需额外判断
- 子类只需实现 _on_show_refresh，无需关心 timer 创建与 showEvent 样板
"""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QTimer

# 节流间隔（毫秒）：200ms 内多次 showEvent 只触发一次刷新
_SHOW_REFRESH_INTERVAL_MS = 200


class RefreshOnShowView(QWidget):
    """带 showEvent 节流刷新的视图基类。

    子类应实现 `_on_show_refresh` 方法，在其中执行实际的刷新逻辑
    （如重载项目列表、刷新表格等）。该方法只在视图被显示且 200ms
    节流窗口结束后调用一次。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # showEvent 节流：快速切导航时只执行最后一次刷新，避免反复打 DB
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(_SHOW_REFRESH_INTERVAL_MS)
        self._show_timer.timeout.connect(self._on_show_refresh)

    def showEvent(self, event):
        """视图被显示时启动节流定时器，触发一次刷新。"""
        super().showEvent(event)
        # 节流：200ms 内多次 showEvent 只触发一次刷新
        self._show_timer.start()

    def _on_show_refresh(self):
        """showEvent 节流后的实际刷新逻辑。

        子类应重写此方法。默认实现为空操作，确保未重写时也不会报错。
        """
        pass

    def _trigger_refresh_now(self):
        """立即触发一次刷新（绕过节流），用于需要立刻刷新的场景。"""
        self._show_timer.stop()
        self._on_show_refresh()
