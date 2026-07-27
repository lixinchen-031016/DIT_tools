"""首启向导对话框

新用户首次启动时（数据库中无任何项目）弹出，引导完成：
  0) 创建第一个工作区（对应物理目录）
  1) 在工作区内创建第一个项目
  2) SOP 操作链一次性提示（媒体导入 → 数据备份 → 拍摄日志补录）

向导为非阻塞对话框，用户可随时取消；取消后仍可正常使用主程序。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QWizard, QWizardPage,
    QPushButton, QFileDialog
)

from DITWorkstation.Utils import get_db_service, logger


# ===== 常量 =====
_STATUS_SUCCESS_COLOR = "#34c759"
_STATUS_ERROR_COLOR = "#ff3b30"
_STATUS_STYLE = "font-size: 12px;"

_WIZARD_QSS = """
    QWizard { background-color: #ffffff; }
    QWizard QLabel { color: #1d1d1f; font-size: 14px; }
    QWizard QLineEdit {
        padding: 6px 8px;
        border: 1px solid #d1d1d6;
        border-radius: 6px;
        font-size: 14px;
    }
    QWizard QLineEdit:focus { border-color: #0a84ff; }
    QWizard QPushButton {
        background-color: #0a84ff;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 6px 16px;
        font-size: 13px;
        font-weight: 600;
        min-width: 80px;
    }
    QWizard QPushButton:hover { background-color: #0070e0; }
    QWizard QPushButton:disabled { background-color: #c7c7cc; }
"""

_WELCOME_TEXT = (
    "本向导将引导你完成项目初始化，约 2 分钟。\n\n"
    "标准 SOP 操作链：\n"
    "  ⓪ 创建工作区 → ① 在工作区内创建项目 → ② 媒体导入 → ③ 数据备份 → ④ 拍摄日志补录\n\n"
    "工作区对应物理目录，用于组织一组相关项目的素材；\n"
    "项目是单次拍摄任务的素材集合（如「2026 春季广告片 - 镜头组A」）。\n\n"
    "你随时可以跳过本向导，后续可在「项目概览」看板查看下一步建议。"
)

_WORKSPACE_HINT = (
    "工作区是项目的父级容器，对应一个物理目录。\n"
    "示例：工作区「2026 春季广告片」对应目录 /Volumes/Work/2026SpringAd。\n"
    "该工作区下的所有项目素材可默认复制到此目录下。"
)

_PROJECT_HINT = (
    "为本次拍摄任务创建一个项目，例如「镜头组A」。\n"
    "该项目将归属于上一步创建的工作区。"
)

# 合并后的 SOP 提示（原 3 页内容整合为 1 页，避免向导页数过多）
_SOP_GUIDE_TEXT = (
    "点击「下一步」后将自动跳转到「媒体导入」视图。\n\n"
    "完整 SOP 操作链提示：\n\n"
    "② 媒体导入\n"
    "  • 选择存储卡所在目录，扫描后将素材导入到刚创建的项目\n"
    "  • 默认会计算校验和（xxhash64）并读取 EXIF，便于后续校验\n"
    "  • 可选「复制到工作区目录」，将素材复制到工作区对应的物理目录下\n\n"
    "③ 数据备份\n"
    "  • 导入完成后尽快去「数据备份」做存储卡的多目标安全备份\n"
    "  • 备份时记得在「关联项目」下拉选中刚创建的项目，\n"
    "    完成后会自动回写素材的备份位置（backup_locations）\n"
    "  • 建议至少 2 个备份目标（主盘 + 异地盘），过程会校验源/目标 xxhash64 一致性\n\n"
    "④ 拍摄日志补录\n"
    "  • 去「拍摄日志」补录场景/镜头/镜次，并关联已导入的素材\n"
    "  • 可用「从代表素材填充 EXIF」自动带出相机/镜头/ISO/光圈/快门\n"
    "  • 日志关联后，素材的 scene/shot 字段会自动同步"
)

_FINISH_TEXT = (
    "✓ 项目初始化完成。\n\n"
    "后续工作流建议：\n"
    "  • 随时回到「项目概览」看板查看进度与下一步引导\n"
    "  • 一个工作区下可创建多个项目（如不同镜头组、不同日期的拍摄）\n"
    "  • 用「素材检索」按场景/镜头/日期快速定位素材\n"
    "  • 用「报告生成」导出数据管理与 QC 报告\n\n"
    "点击「完成」进入主界面。"
)


class FirstRunWizard(QWizard):
    """首启向导 - QWizard 实现，兼容暗色主题"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 DIT 工作站")
        self.setMinimumSize(640, 480)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.IndependentPages, False)

        # 向导创建产物：供 main.py 在向导结束后做后续跳转
        self._created_workspace_id = None
        self._created_workspace_path = ""  # 工作区物理目录（用于后续默认复制路径）
        self._created_project_id = None

        # 页面顺序：欢迎 → 创建工作区 → 在工作区内创建项目 → SOP 提示 → 完成
        self.addPage(_WelcomePage())
        self.addPage(_CreateWorkspacePage(self))
        self.addPage(_CreateProjectPage(self))
        self.addPage(_SopGuidePage())
        self.addPage(_FinishPage())

        self.setStyleSheet(_WIZARD_QSS)


class _WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("欢迎使用 DIT 工作站")
        layout = QVBoxLayout(self)
        intro = QLabel(_WELCOME_TEXT)
        intro.setWordWrap(True)
        layout.addWidget(intro)


class _CreateWorkspacePage(QWizardPage):
    """第 0 步：创建第一个工作区（对应物理目录）"""

    def __init__(self, wizard: "FirstRunWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("创建第一个工作区")
        layout = QVBoxLayout(self)

        hint = QLabel(_WORKSPACE_HINT)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("工作区名称，如「2026 春季广告片」")
        self.registerField("workspace_name*", self.name_edit)
        layout.addWidget(self.name_edit)

        # 工作区目录选择器
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("工作区物理目录（可选，留空则不绑定目录）")
        path_row.addWidget(self.path_edit, 1)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_dir)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("工作区描述（可选）")
        layout.addWidget(self.desc_edit)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(_STATUS_STYLE)
        layout.addWidget(self._status_label)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if d:
            self.path_edit.setText(d)

    def _set_status(self, text: str, color: str):
        self._status_label.setStyleSheet(f"color: {color}; {_STATUS_STYLE}")
        self._status_label.setText(text)

    def validatePage(self) -> bool:
        name = self.name_edit.text().strip()
        if not name:
            return False
        try:
            db = get_db_service()
            ws = db.create_workspace(
                name=name,
                path=self.path_edit.text().strip(),
                description=self.desc_edit.text().strip()
            )
            self._wizard._created_workspace_id = ws.workspace_id
            self._wizard._created_workspace_path = ws.path
            self._set_status(f"✓ 已创建工作区：{name}", _STATUS_SUCCESS_COLOR)
            logger.info(f"首启向导创建工作区: {ws.workspace_id} - {name} (path={ws.path})")
            return True
        except Exception as e:
            self._set_status(f"✗ 创建失败：{e}", _STATUS_ERROR_COLOR)
            logger.error(f"首启向导创建工作区失败: {e}", exc_info=True)
            return False


class _CreateProjectPage(QWizardPage):
    """第 1 步：在刚创建的工作区内创建第一个项目"""

    def __init__(self, wizard: "FirstRunWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("在工作区内创建第一个项目")
        layout = QVBoxLayout(self)

        hint = QLabel(_PROJECT_HINT)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("项目名称")
        self.registerField("project_name*", self.name_edit)
        layout.addWidget(self.name_edit)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("项目描述（可选）")
        layout.addWidget(self.desc_edit)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(_STATUS_STYLE)
        layout.addWidget(self._status_label)

    def _set_status(self, text: str, color: str):
        self._status_label.setStyleSheet(f"color: {color}; {_STATUS_STYLE}")
        self._status_label.setText(text)

    def validatePage(self) -> bool:
        name = self.name_edit.text().strip()
        if not name:
            return False
        try:
            db = get_db_service()
            # 在向导创建的工作区内创建项目；若用户跳过了工作区步骤则归入默认工作区
            ws_id = self._wizard._created_workspace_id
            project = db.create_project(
                name=name,
                description=self.desc_edit.text().strip(),
                workspace_id=ws_id
            )
            self._wizard._created_project_id = project.project_id
            self._set_status(f"✓ 已创建项目：{name}", _STATUS_SUCCESS_COLOR)
            logger.info(f"首启向导创建项目: {project.project_id} - {name} (workspace={ws_id})")
            return True
        except Exception as e:
            self._set_status(f"✗ 创建失败：{e}", _STATUS_ERROR_COLOR)
            logger.error(f"首启向导创建项目失败: {e}", exc_info=True)
            return False


class _SopGuidePage(QWizardPage):
    """合并后的 SOP 操作链一次性提示页（原导入/备份/日志 3 页合一）"""

    def __init__(self):
        super().__init__()
        self.setTitle("SOP 操作链提示")
        layout = QVBoxLayout(self)
        text = QLabel(_SOP_GUIDE_TEXT)
        text.setWordWrap(True)
        layout.addWidget(text)


class _FinishPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("完成")
        layout = QVBoxLayout(self)
        text = QLabel(_FINISH_TEXT)
        text.setWordWrap(True)
        layout.addWidget(text)


def should_show_wizard() -> bool:
    """是否应该弹出首启向导：仅在数据库中没有任何项目时返回 True"""
    try:
        db = get_db_service()
        projects = db.get_projects()
        return len(projects) == 0
    except Exception as e:
        logger.warning(f"检查首启向导条件失败：{e}")
        return False


def maybe_show_wizard(parent=None) -> Optional[FirstRunWizard]:
    """如果满足首启条件，弹出向导并返回向导对象；否则返回 None。

    返回的向导对象已通过 exec() 阻塞执行完毕，调用方可读取
    `_created_workspace_id` / `_created_project_id` 拿到创建的工作区与项目 ID，
    用于自动设置全局工作区/项目并跳转到媒体导入。
    """
    if not should_show_wizard():
        return None
    wizard = FirstRunWizard(parent)
    wizard.exec()
    return wizard
