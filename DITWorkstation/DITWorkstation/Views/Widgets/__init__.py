"""Widgets模块"""
from .workspace_dialog import WorkspaceDialog
from .workspace_project_selector import WorkspaceProjectSelector
from .base_views import RefreshOnShowView
from .error_dialog import ErrorDialog, show_error
from .backup_template_dialog import BackupTemplateDialog, edit_backup_template

__all__ = [
    'WorkspaceDialog', 'WorkspaceProjectSelector', 'RefreshOnShowView',
    'ErrorDialog', 'show_error', 'BackupTemplateDialog', 'edit_backup_template',
]
