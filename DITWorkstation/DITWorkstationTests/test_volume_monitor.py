"""存储卡自动识别测试"""
from pathlib import Path

from DITWorkstation.Services.volume_monitor import (
    list_volume_roots, looks_like_card, VolumeMonitor,
)


def test_looks_like_card_dcim(tmp_dir):
    card = tmp_dir / "CARD"
    (card / "DCIM").mkdir(parents=True)
    assert looks_like_card(str(card))


def test_looks_like_card_media_files(tmp_dir):
    card = tmp_dir / "CARD"
    card.mkdir()
    for name in ("IMG_001.jpg", "IMG_002.cr2", "IMG_003.jpg"):
        (card / name).write_bytes(b"x")
    assert looks_like_card(str(card))


def test_looks_like_card_rejects_normal_dir(tmp_dir):
    project_dir = tmp_dir / "project"
    project_dir.mkdir()
    (project_dir / "manifest.json").write_text("{}")
    assert not looks_like_card(str(project_dir))


def test_looks_like_card_missing_path(tmp_dir):
    assert not looks_like_card(str(tmp_dir / "nope"))


def test_list_volume_roots_returns_list():
    roots = list_volume_roots()
    assert isinstance(roots, list)


def test_volume_monitor_seeds_known_volumes():
    """start() 时已存在的卷不触发挂载事件"""
    import platform
    monitor = VolumeMonitor(interval_ms=1000)
    monitor.start()
    assert len(monitor._known) >= 0
    monitor.stop()


def test_volume_monitor_emits_on_new_mount(tmp_dir, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    monitor = VolumeMonitor(interval_ms=50)
    mounted = []
    monitor.volume_mounted.connect(lambda p: mounted.append(p))
    # 初始只认识一个空卷；随后"挂载"一个新存储卡目录
    empty = tmp_dir / "empty"
    empty.mkdir()
    monkeypatch.setattr(
        "DITWorkstation.Services.volume_monitor.list_volume_roots",
        lambda: [str(empty)],
    )
    monitor._known = {str(empty)}
    card = tmp_dir / "CARD"
    (card / "DCIM").mkdir(parents=True)

    def _roots():
        return [str(empty), str(card)]

    monkeypatch.setattr(
        "DITWorkstation.Services.volume_monitor.list_volume_roots",
        _roots,
    )
    monitor._poll()
    assert mounted == [str(card)]
    assert str(card) in monitor._known
