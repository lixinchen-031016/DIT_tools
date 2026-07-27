"""pytest 共享 fixture

提供各测试模块复用的数据库服务、临时项目、素材等 fixture，
消除每个测试文件各自 setUp/tearDown 的重复代码。
"""
import os
import sys
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Models import Project, ShootingLog, MediaAsset, Workspace


@pytest.fixture
def tmp_dir():
    """临时目录，测试结束自动清理"""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db_service(tmp_dir):
    """隔离的 DatabaseService 实例（每个测试独立数据库文件）"""
    db = DatabaseService(db_path=tmp_dir / "test.db")
    yield db


@pytest.fixture
def project(db_service):
    """预创建的项目"""
    return db_service.create_project(name="测试项目")


@pytest.fixture
def workspace(db_service):
    """预创建的工作区"""
    return db_service.create_workspace(name="测试工作区", path="/tmp/test_ws")


@pytest.fixture
def make_asset(db_service):
    """工厂函数：快速创建素材"""
    def _make(project_id, **kwargs):
        defaults = dict(
            asset_id=str(uuid.uuid4())[:8],
            project_id=project_id,
            file_path=f"/path/IMG_{uuid.uuid4().hex[:4]}.cr2",
            file_name=f"IMG_{uuid.uuid4().hex[:4]}.cr2",
            file_size=1024,
            file_type=".cr2",
        )
        defaults.update(kwargs)
        asset = MediaAsset(**defaults)
        return db_service.add_media_asset(asset)
    return _make


@pytest.fixture
def make_log(db_service):
    """工厂函数：快速创建拍摄日志"""
    def _make(project_id, **kwargs):
        defaults = dict(
            log_id=str(uuid.uuid4())[:8],
            project_id=project_id,
            scene="S001",
            shot="001A",
            take="01",
        )
        defaults.update(kwargs)
        log = ShootingLog(**defaults)
        return db_service.create_shooting_log(log)
    return _make
