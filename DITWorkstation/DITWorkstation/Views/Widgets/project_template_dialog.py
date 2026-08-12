"""项目模板对话框：从模板新建项目 / 把当前项目保存为模板"""
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QPushButton, QComboBox,
    QDialogButtonBox, QMessageBox,
)

from DITWorkstation.App.session_context import (
    get_data_bus, set_current_project, get_current_workspace_id,
)
from DITWorkstation.Models import Project, ProjectTemplate


class ProjectFromTemplateDialog(QDialog):
    """从项目模板创建新项目。

    选择模板 → 填写新项目名称 → 选择目标工作区，创建项目后广播
    projects_changed 并选中新项目。
    """

    def __init__(self, parent=None, db_service=None, templates=None):
        super().__init__(parent)
        self._db_service = db_service
        self._templates = list(templates or (db_service.get_project_templates() if db_service else []))
        self._created_project: Optional[Project] = None
        self.setWindowTitle("从模板新建项目")
        self.setMinimumWidth(440)
        self._setup_ui()

    def _setup_ui(self):
        form = QFormLayout(self)

        self.template_combo = QComboBox()
        for t in self._templates:
            self.template_combo.addItem(t.name, t.template_id)
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        form.addRow("项目模板:", self.template_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("新项目名称（必填）")
        form.addRow("项目名称 *:", self.name_edit)

        self.workspace_combo = QComboBox()
        self._load_workspaces()
        form.addRow("所属工作区:", self.workspace_combo)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("项目描述（可选）")
        form.addRow("描述:", self.desc_edit)

        self.base_path_label = QLineEdit()
        self.base_path_label.setReadOnly(True)
        form.addRow("默认工作目录:", self.base_path_label)

        if self._templates:
            self._on_template_changed(0)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("创建项目")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_create)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _load_workspaces(self):
        try:
            workspaces = self._db_service.get_workspaces() if self._db_service else []
        except Exception:
            workspaces = []
        current_ws = get_current_workspace_id()
        for ws in workspaces:
            self.workspace_combo.addItem(ws.name, ws.workspace_id)
            if current_ws and ws.workspace_id == current_ws:
                self.workspace_combo.setCurrentIndex(self.workspace_combo.count() - 1)
        if not workspaces:
            self.workspace_combo.addItem("（默认工作区）", "default")

    def _on_template_changed(self, _index: int):
        template = self._current_template()
        if not template:
            return
        self.desc_edit.setText(template.description)
        self.base_path_label.setText(template.base_path or "")
        if not self.name_edit.text().strip():
            self.name_edit.setText(template.name)

    def _current_template(self) -> Optional[ProjectTemplate]:
        template_id = self.template_combo.currentData()
        for t in self._templates:
            if t.template_id == template_id:
                return t
        return None

    def _on_create(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "项目名称不能为空")
            return
        template = self._current_template()
        workspace_id = self.workspace_combo.currentData() or "default"
        description = self.desc_edit.text().strip()
        base_path = template.base_path if template else ""
        try:
            self._created_project = self._db_service.create_project(
                name=name,
                description=description,
                base_path=base_path,
                workspace_id=workspace_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"创建项目失败：{e}")
            return
        set_current_project(self._created_project.project_id)
        get_data_bus().emit_data_changed("projects_changed")
        self.accept()

    def get_created_project(self) -> Optional[Project]:
        return self._created_project


class SaveProjectAsTemplateDialog(QDialog):
    """把当前项目保存为项目模板，供后续快速建项复用。"""

    def __init__(self, parent=None, db_service=None, project: Optional[Project] = None):
        super().__init__(parent)
        self._db_service = db_service
        self._project = project
        self._saved_template: Optional[ProjectTemplate] = None
        self.setWindowTitle("保存为项目模板")
        self.setMinimumWidth(440)
        self._setup_ui()

    def _setup_ui(self):
        form = QFormLayout(self)

        self.name_edit = QLineEdit()
        if self._project:
            self.name_edit.setText(f"「{self._project.name}」模板")
        self.name_edit.setPlaceholderText("模板名称（必填）")
        form.addRow("模板名称 *:", self.name_edit)

        self.desc_edit = QLineEdit()
        if self._project:
            self.desc_edit.setText(self._project.description)
        self.desc_edit.setPlaceholderText("模板描述（可选）")
        form.addRow("描述:", self.desc_edit)

        self.base_path_edit = QLineEdit()
        if self._project:
            self.base_path_edit.setText(self._project.base_path)
        self.base_path_edit.setPlaceholderText("默认工作目录（可选，如 DIT_Workspace）")
        form.addRow("默认工作目录:", self.base_path_edit)

        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("使用说明（可选）")
        form.addRow("备注:", self.notes_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("保存模板")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "模板名称不能为空")
            return
        try:
            self._saved_template = self._db_service.create_project_template(
                name=name,
                description=self.desc_edit.text().strip(),
                base_path=self.base_path_edit.text().strip(),
                notes=self.notes_edit.text().strip(),
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存模板失败：{e}")
            return
        get_data_bus().emit_data_changed("templates_changed")
        self.accept()

    def get_saved_template(self) -> Optional[ProjectTemplate]:
        return self._saved_template


def create_project_from_template(parent=None, db_service=None) -> Optional[Project]:
    """便捷方法：弹出「从模板新建项目」对话框，返回新项目（取消返回 None）。"""
    dlg = ProjectFromTemplateDialog(parent=parent, db_service=db_service)
    if dlg.exec() == QDialog.Accepted:
        return dlg.get_created_project()
    return None


def save_project_as_template(parent=None, db_service=None, project: Optional[Project] = None) -> Optional[ProjectTemplate]:
    """便捷方法：弹出「保存为模板」对话框，返回新模板（取消返回 None）。"""
    dlg = SaveProjectAsTemplateDialog(parent=parent, db_service=db_service, project=project)
    if dlg.exec() == QDialog.Accepted:
        return dlg.get_saved_template()
    return None
