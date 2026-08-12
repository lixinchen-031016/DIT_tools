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

# session_context 模块在导入时实例化 EventBus(QObject)，需要 QApplication 先存在。
# 使用 offscreen 平台避免无头/沙箱环境下连接窗口服务器或剪贴板导致崩溃。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

from DITWorkstation.Services.database_service import DatabaseService
from DITWorkstation.Models import Project, ShootingLog, MediaAsset, Workspace
from DITWorkstation.Utils import reset_singletons
from DITWorkstation.App.session_context import reset_session_state


@pytest.fixture(autouse=True)
def _reset_global_state():
    """每个测试前后自动重置全局单例与会话状态，确保跨测试隔离。

    - reset_singletons()：清空 get_db_service / get_checksum_service 的缓存实例
    - reset_session_state()：清空 _current_workspace_id / _current_project_id
    """
    reset_singletons()
    reset_session_state()


@pytest.fixture(scope="session", autouse=True)
def _qt_cleanup_session():
    """会话结束、解释器退出前显式收尾 Qt。

    macOS 真实 GUI 会话下，PySide6 若把残留的 QWidget 包装器留到
    Py_Finalize 阶段由 runCleanupFunctions 统一析构，会偶发 SIGSEGV
    （QPushButtonWrapper 二次析构，KERN_INVALID_ADDRESS；测试已全部
    通过但进程退出码为 139，会中止打包脚本）。在 pytest 完成前显式
    销毁全部顶层控件、冲刷 DeferredDelete 事件并销毁 QApplication，
    确保 BindingManager 中无存活包装器，消除该竞态。
    """
    yield
    try:
        from PySide6.QtCore import QEventLoop, QThreadPool, QTimer
        try:
            QThreadPool.globalInstance().waitForDone(5000)
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            for w in list(app.topLevelWidgets()):
                try:
                    w.hide()
                    w.deleteLater()
                except RuntimeError:
                    pass
            app.processEvents()
            loop = QEventLoop()
            QTimer.singleShot(0, loop.quit)
            loop.exec()
            app.processEvents()
            app.quit()
    except Exception:
        pass
    # 在解释器终结前显式释放并销毁 QApplication（shiboken 会立即删除
    # 底层 C++ 对象）。若留到 Py_Finalize 阶段由 Python 模块级 _app
    # 引用触发析构，macOS 上会偶发 SIGSEGV。
    global _app
    _app = None
    try:
        import gc
        gc.collect()
    except Exception:
        pass


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
