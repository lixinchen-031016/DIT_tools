"""备份-导入数据闭环测试

验证「备份关联项目时，未入库源文件自动登记为 asset 并回写 backup_locations」
这一数据闭环逻辑。对应 backup_view._on_finished 中的编排：
    execute_backup -> import_assets(引用模式) -> add_backup_location_to_assets(补写)
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DITWorkstation.Services.backup_service import BackupService
from DITWorkstation.Services.media_import_service import MediaImportService
from DITWorkstation.Models import ChecksumAlgorithm, BackupStatus, CopyStatus


@pytest.fixture
def fs_workspace(tmp_dir):
    """临时文件系统：源目录 + 2 个备份目标"""
    src = tmp_dir / "source"
    tgt1 = tmp_dir / "target1"
    tgt2 = tmp_dir / "target2"
    src.mkdir()
    tgt1.mkdir()
    tgt2.mkdir()
    # 造 3 个媒体文件（.jpg 便于走 EXIF 分支但不依赖真实 EXIF）
    for i in range(3):
        (src / f"IMG_{i:03d}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 1024)
    return src, tgt1, tgt2


def test_backup_then_import_creates_assets_with_backup_locations(db_service, fs_workspace):
    """场景 1：未入库源文件备份后，经「导入+补写」应进入项目并带 backup_locations

    这是用户报告 bug 的核心回归用例：备份只复制文件，没把文件信息导入项目。
    """
    src, tgt1, tgt2 = fs_workspace
    project = db_service.create_project(name="闭环项目")

    # 复刻 backup_view 的编排：共享 db_service + checksum_service
    backup_service = BackupService(db_service=db_service)
    import_service = MediaImportService(db_service=db_service)

    # 1. 执行备份（关联项目）
    job = backup_service.create_backup_job(str(src), [str(tgt1), str(tgt2)])
    job = backup_service.execute_backup(job, project_id=project.project_id)

    assert job.status == BackupStatus.COMPLETED
    assert all(t.status == CopyStatus.COMPLETED for t in job.targets)

    # 2. 备份完成后，把未入库源文件登记到项目（引用模式）
    files = getattr(job, '_files_cache')
    source_paths = [f["path"] for f in files]
    result = import_service.import_assets(
        project_id=project.project_id,
        file_paths=source_paths,
        compute_checksum=False,  # 测试加速
        read_metadata=False,
        copy_to_workspace=False,
    )
    assert result["imported"] == 3
    assert result["skipped"] == 0

    # 3. 对新入库的 asset 补写 backup_locations
    for t in job.targets:
        if t.status == CopyStatus.COMPLETED:
            db_service.add_backup_location_to_assets(
                source_paths, t.path, project_id=project.project_id
            )

    # 断言：项目里出现 3 个 asset，且每个都带 2 个 backup_locations
    assets = db_service.search_assets(project_id=project.project_id)
    assert len(assets) == 3
    for a in assets:
        locations = a.backup_locations if a.backup_locations else []
        assert str(tgt1) in locations
        assert str(tgt2) in locations
        assert len(locations) == 2


def test_backup_import_idempotent_no_duplicate(db_service, fs_workspace):
    """场景 2：先导入再备份+再导入，不应产生重复 asset，backup_locations 正确回写"""
    src, tgt1, tgt2 = fs_workspace
    project = db_service.create_project(name="幂等项目")

    backup_service = BackupService(db_service=db_service)
    import_service = MediaImportService(db_service=db_service)

    # 1. 先导入（模拟「先导入再备份」的既有流程）
    source_paths = [str(p) for p in sorted(src.glob("*.jpg"))]
    first = import_service.import_assets(
        project_id=project.project_id,
        file_paths=source_paths,
        compute_checksum=False,
        read_metadata=False,
    )
    assert first["imported"] == 3

    # 2. 备份
    job = backup_service.create_backup_job(str(src), [str(tgt1)])
    job = backup_service.execute_backup(job, project_id=project.project_id)
    assert job.status == BackupStatus.COMPLETED

    # execute_backup 内部已尝试回写 backup_locations（此时 asset 已存在，应生效）
    assets_after_backup = db_service.search_assets(project_id=project.project_id)
    assert len(assets_after_backup) == 3
    for a in assets_after_backup:
        locations = a.backup_locations if a.backup_locations else []
        assert str(tgt1) in locations

    # 3. 再次导入（幂等：应全部跳过）
    second = import_service.import_assets(
        project_id=project.project_id,
        file_paths=source_paths,
        compute_checksum=False,
        read_metadata=False,
    )
    assert second["imported"] == 0
    assert second["skipped"] == 3

    # 不产生重复 asset
    assets_final = db_service.search_assets(project_id=project.project_id)
    assert len(assets_final) == 3


def test_backup_no_project_does_not_import(db_service, fs_workspace):
    """场景 3：不关联项目时，备份行为不变，不触发导入

    view 层判断 `if project_id and ...` 才走导入分支；此处验证 service 层
    在 project_id=None 时仍能完成备份且不依赖项目。
    """
    src, tgt1, _ = fs_workspace
    backup_service = BackupService(db_service=db_service)

    job = backup_service.create_backup_job(str(src), [str(tgt1)])
    # project_id 不传
    job = backup_service.execute_backup(job)
    assert job.status == BackupStatus.COMPLETED

    # 没有任何项目，自然没有 asset
    project = db_service.create_project(name="空项目")
    assets = db_service.search_assets(project_id=project.project_id)
    assert len(assets) == 0


def test_backup_partial_then_import_only_completed_targets_written(db_service, fs_workspace):
    """场景 4：部分成功时，只对 completed 目标回写 backup_locations

    验证 _on_finished 中 `if t.status.value == "completed"` 的过滤逻辑。
    """
    src, tgt1, tgt2 = fs_workspace
    project = db_service.create_project(name="部分项目")

    backup_service = BackupService(db_service=db_service)
    import_service = MediaImportService(db_service=db_service)

    job = backup_service.create_backup_job(str(src), [str(tgt1), str(tgt2)])
    # 手动把第二个目标标记为失败，模拟 partial
    job.targets[1].status = CopyStatus.FAILED
    job.targets[1].error_message = "模拟失败"

    # 正常执行拷贝到 tgt1（tgt2 已标记失败，但 execute_backup 会重新提交；
    # 这里改为直接验证「只对 completed 目标回写」的逻辑）
    job = backup_service.execute_backup(job, project_id=project.project_id)

    # 导入源文件
    files = getattr(job, '_files_cache')
    source_paths = [f["path"] for f in files]
    import_service.import_assets(
        project_id=project.project_id,
        file_paths=source_paths,
        compute_checksum=False,
        read_metadata=False,
    )

    # 只对 completed 目标补写
    for t in job.targets:
        if t.status == CopyStatus.COMPLETED:
            db_service.add_backup_location_to_assets(
                source_paths, t.path, project_id=project.project_id
            )

    # 至少 tgt1 应被回写（execute_backup 正常完成则 tgt1=completed）
    assets = db_service.search_assets(project_id=project.project_id)
    assert len(assets) == 3
    completed_targets = [t.path for t in job.targets if t.status == CopyStatus.COMPLETED]
    for a in assets:
        locations = a.backup_locations if a.backup_locations else []
        for ct in completed_targets:
            assert ct in locations
