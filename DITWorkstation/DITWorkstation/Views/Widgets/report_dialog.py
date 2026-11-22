"""报告生成对话框：把报告生成从导航页改为工作台动作。"""

from PySide6.QtWidgets import QDialog, QMessageBox, QVBoxLayout

from DITWorkstation.Views.report_view import ReportView


class ReportDialog(QDialog):
    """承载 ReportView 的轻量对话框。

    报告生成是任务终点而非常驻工作区，因此不再占用一级导航；
    由「项目概览 → ④ 生成报告」或命令面板打开。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成报告")
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.report_view = ReportView()
        layout.addWidget(self.report_view)

    def reject(self):
        """生成中关闭对话框时先确认并请求取消，避免任务被静默中断。"""
        if self.report_view.task_vm.is_running():
            reply = QMessageBox.question(
                self,
                "报告正在生成",
                "报告仍在生成中。取消并关闭对话框？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            self.report_view.task_vm.cancel()
        super().reject()
