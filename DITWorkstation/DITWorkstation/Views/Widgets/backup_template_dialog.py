"""备份方案模板对话框。"""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
)

from DITWorkstation.Models import BackupTemplate, ChecksumAlgorithm


class BackupTemplateDialog(QDialog):
    """创建备份方案模板；每行一个目标目录，可使用 {source_name}。"""

    def __init__(self, parent=None, template: BackupTemplate | None = None):
        super().__init__(parent)
        self.template = template
        self.saved_values = None
        self.setWindowTitle("保存备份方案模板")
        self.setMinimumWidth(560)
        self._setup_ui()

    def _setup_ui(self):
        form = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：现场双盘备份")
        if self.template and self.template.name:
            self.name_edit.setText(self.template.name)
        form.addRow("方案名称 *:", self.name_edit)

        self.targets_edit = QPlainTextEdit()
        self.targets_edit.setPlaceholderText(
            "每行一个目标目录，例如：\n/Volumes/BACKUP_A/{source_name}\n/Volumes/BACKUP_B/{source_name}"
        )
        self.targets_edit.setMinimumHeight(120)
        if self.template:
            self.targets_edit.setPlainText("\n".join(self.template.target_paths))
        form.addRow("备份目标 *:", self.targets_edit)

        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItem("XXHash64（推荐）", ChecksumAlgorithm.XXHASH64)
        self.algorithm_combo.addItem("MD5（兼容）", ChecksumAlgorithm.MD5)
        if self.template:
            self.algorithm_combo.setCurrentIndex(
                0 if self.template.algorithm == ChecksumAlgorithm.XXHASH64 else 1
            )
        form.addRow("校验和:", self.algorithm_combo)

        self.verify_check = QCheckBox("拷贝后验证完整性")
        self.verify_check.setChecked(
            self.template.verify_after_copy if self.template else True
        )
        form.addRow("验证:", self.verify_check)

        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("用途说明（可选）")
        if self.template:
            self.description_edit.setText(self.template.description)
        form.addRow("说明:", self.description_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _save(self):
        name = self.name_edit.text().strip()
        targets = [
            line.strip()
            for line in self.targets_edit.toPlainText().splitlines()
            if line.strip()
        ]
        if not name:
            QMessageBox.warning(self, "提示", "方案名称不能为空")
            return
        if not targets:
            QMessageBox.warning(self, "提示", "至少填写一个备份目标目录")
            return
        self.saved_values = {
            "name": name,
            "target_paths": targets,
            "algorithm": self.algorithm_combo.currentData(),
            "verify_after_copy": self.verify_check.isChecked(),
            "description": self.description_edit.text().strip(),
        }
        self.accept()


def edit_backup_template(parent=None, template: BackupTemplate | None = None):
    dialog = BackupTemplateDialog(parent=parent, template=template)
    return dialog.saved_values if dialog.exec() == QDialog.Accepted else None
