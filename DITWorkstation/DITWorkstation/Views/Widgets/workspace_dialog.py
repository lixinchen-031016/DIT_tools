"""工作区新建/编辑对话框（共享组件）

消除 media_import_view / shooting_log_view / project_dashboard_view 三处重复的对话框代码。
"""
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QPushButton,
    QDialogButtonBox, QMessageBox, QHBoxLayout, QWidget
)

from DITWorkstation.Models import Workspace
from DITWorkstation.Utils import pick_directory


class WorkspaceDialog(QDialog):
    """工作区新建/编辑对话框

    字段：名称（必填）、目录（可选，浏览选择）、描述（可选）。
    编辑模式下预填现有值；保存后通过 `get_workspace()` 返回最新对象。
    """

    def __init__(self, parent=None, workspace: Optional[Workspace] = None,
                 db_service=None):
        """
        Args:
            workspace: 为 None 时新建；为 Workspace 实例时编辑现有
            db_service: 数据库服务（用于新建/更新；为 None 时调用方负责持久化）
        """
        super().__init__(parent)
        self._workspace = workspace
        self._db_service = db_service
        self._saved_workspace: Optional[Workspace] = None

        is_edit = workspace is not None
        self.setWindowTitle("编辑工作区" if is_edit else "新建工作区")
        self.setMinimumWidth(420)

        form = QFormLayout(self)

        self.name_edit = QLineEdit(workspace.name if is_edit else "")
        self.name_edit.setPlaceholderText("如「2026 春季广告片」")

        self.path_edit = QLineEdit(workspace.path if is_edit else "")
        self.path_edit.setPlaceholderText(
            "工作区物理目录（可选，建议填写以便导入时复制素材）"
        )
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        browse_btn = QPushButton("浏览…")

        def _browse():
            d = pick_directory(self, "选择工作区目录", self.path_edit.text(), category="workspace")
            if d:
                self.path_edit.setText(d)

        browse_btn.clicked.connect(_browse)
        path_row.addWidget(browse_btn)
        path_container = QWidget()
        path_container.setLayout(path_row)
        path_container.layout().setContentsMargins(0, 0, 0, 0)

        self.desc_edit = QLineEdit(
            workspace.description if is_edit else ""
        )
        self.desc_edit.setPlaceholderText("描述（可选）")

        form.addRow("名称 *:", self.name_edit)
        form.addRow("目录:", path_container)
        form.addRow("描述:", self.desc_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("保存")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _on_save(self):
        """保存工作区（新建或更新）"""
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "工作区名称不能为空")
            return

        path = self.path_edit.text().strip()
        description = self.desc_edit.text().strip()

        try:
            if self._workspace is not None:
                # 编辑模式
                if self._db_service is not None:
                    ok = self._db_service.update_workspace(
                        self._workspace.workspace_id,
                        name=name, path=path, description=description
                    )
                    if not ok:
                        QMessageBox.warning(self, "提示", "更新失败")
                        return
                    self._saved_workspace = self._db_service.get_workspace(
                        self._workspace.workspace_id
                    )
                else:
                    # 无 db_service 时仅更新内存对象
                    self._workspace.name = name
                    self._workspace.path = path
                    self._workspace.description = description
                    self._saved_workspace = self._workspace
            else:
                # 新建模式
                if self._db_service is not None:
                    self._saved_workspace = self._db_service.create_workspace(
                        name=name, path=path, description=description
                    )
                else:
                    # 无 db_service 时返回未持久化的临时对象
                    from uuid import uuid4
                    self._saved_workspace = Workspace(
                        workspace_id=str(uuid4())[:8],
                        name=name, path=path, description=description
                    )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：{e}")

    def get_saved_workspace(self) -> Optional[Workspace]:
        """返回保存后的 Workspace 对象（未保存返回 None）"""
        return self._saved_workspace

    @staticmethod
    def create(parent=None, db_service=None) -> Optional[Workspace]:
        """便捷方法：弹出新建对话框，返回新建的 Workspace（取消返回 None）"""
        dlg = WorkspaceDialog(parent=parent, db_service=db_service)
        if dlg.exec() == QDialog.Accepted:
            return dlg.get_saved_workspace()
        return None

    @staticmethod
    def edit(workspace: Workspace, parent=None, db_service=None) -> Optional[Workspace]:
        """便捷方法：弹出编辑对话框，返回更新后的 Workspace（取消返回 None）"""
        dlg = WorkspaceDialog(parent=parent, workspace=workspace, db_service=db_service)
        if dlg.exec() == QDialog.Accepted:
            return dlg.get_saved_workspace()
        return None
