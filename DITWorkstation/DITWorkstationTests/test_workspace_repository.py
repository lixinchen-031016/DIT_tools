"""Workspace repository integration tests."""


def test_workspace_repository_and_database_facade_share_storage(db_service):
    workspace = db_service.workspaces.create("仓储工作区", "/workspace")

    assert db_service.get_workspace(workspace.workspace_id) == workspace
    assert db_service.workspaces.update(workspace.workspace_id, description="已更新")
    assert db_service.get_workspace(workspace.workspace_id).description == "已更新"


def test_workspace_repository_default_workspace_is_idempotent(db_service):
    first = db_service.workspaces.get_or_create_default()
    second = db_service.get_or_create_default_workspace()

    assert first.workspace_id == "default"
    assert second == first


def test_template_repository_and_database_facade_share_storage(db_service):
    template = db_service.templates.create_project("仓储模板", base_path="work")

    assert db_service.get_project_template(template.template_id) == template
    assert db_service.templates.update_project(template.template_id, notes="更新")
    assert db_service.get_project_template(template.template_id).notes == "更新"
