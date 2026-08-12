"""项目模板 CRUD 与应用测试"""
from DITWorkstation.Services.database_service import DatabaseService


def test_default_template_seeded(tmp_dir):
    db = DatabaseService(db_path=tmp_dir / "test.db")
    templates = db.get_project_templates()
    assert any(t.template_id == "default" for t in templates)
    default = db.get_project_template("default")
    assert default.name == "标准影视项目"
    assert default.base_path == "DIT_Workspace"


def test_create_and_get_template(tmp_dir):
    db = DatabaseService(db_path=tmp_dir / "test.db")
    t = db.create_project_template(
        name="广告片模板",
        description="3 天周期广告拍摄",
        base_path="AD_Files",
        notes="适用于 TVC",
    )
    got = db.get_project_template(t.template_id)
    assert got.name == "广告片模板"
    assert got.description == "3 天周期广告拍摄"
    assert got.base_path == "AD_Files"
    assert got.notes == "适用于 TVC"


def test_update_template(tmp_dir):
    db = DatabaseService(db_path=tmp_dir / "test.db")
    t = db.create_project_template(name="旧名称")
    assert db.update_project_template(t.template_id, name="新名称", base_path="NEW")
    got = db.get_project_template(t.template_id)
    assert got.name == "新名称"
    assert got.base_path == "NEW"


def test_delete_template(tmp_dir):
    db = DatabaseService(db_path=tmp_dir / "test.db")
    t = db.create_project_template(name="待删除")
    assert db.delete_project_template(t.template_id)
    assert db.get_project_template(t.template_id) is None


def test_apply_template_creates_project_with_base_path(db_service):
    template = db_service.get_project_template("default")
    project = db_service.create_project(
        name="新项目",
        description=template.description,
        base_path=template.base_path,
        workspace_id="default",
    )
    assert project.name == "新项目"
    assert project.base_path == "DIT_Workspace"
    assert project.workspace_id == "default"


def test_template_table_survives_migration_idempotency(tmp_dir):
    """重复触发迁移不应重复预置模板或报错"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    db._migrate_db()
    templates = db.get_project_templates()
    default_count = sum(1 for t in templates if t.template_id == "default")
    assert default_count == 1
