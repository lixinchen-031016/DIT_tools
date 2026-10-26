"""相机品牌机型识别（基于 EXIF）。

不依赖卡上目录特征（目录结构随厂商固件/拍摄模式变化，不可靠），
直接从卡内媒体文件的 EXIF ``Make``/``Model`` 标签读取品牌与机型。

- 单文件识别：``identify_camera(file_path)``；
- 卡级识别：``identify_camera_for_card(folder)`` 抽样最多 3 个媒体文件，
  取首个成功结果（同一张卡内机型一致）。

识别结果用于：插卡状态提示、备份模板目录命名、post-ingest 钩子上下文
（``DIT_CAMERA_MAKE`` / ``DIT_CAMERA_MODEL``）。
"""

from pathlib import Path

from DITWorkstation.App import config
from DITWorkstation.Utils import logger

# EXIF Make 常见写法 -> 展示名规范化
_MAKE_NORMALIZE = {
    "canon": "Canon",
    "nikon": "Nikon",
    "nikon corporation": "Nikon",
    "sony": "Sony",
    "sony corporation": "Sony",
    "fujifilm": "Fujifilm",
    "fuji film": "Fujifilm",
    "panasonic": "Panasonic",
    "leica": "Leica",
    "dji": "DJI",
    "apple": "Apple",
    "gopro": "GoPro",
    "red": "RED",
    "red digital cinema": "RED",
    "blackmagic": "Blackmagic",
    "blackmagic design": "Blackmagic",
    "arri": "ARRI",
    "arnold & richter": "ARRI",
    "sigma": "Sigma",
    "pentax": "Pentax",
    "ricoh": "Ricoh",
    "olympus": "Olympus",
    "om digital solutions": "OM System",
    "hasselblad": "Hasselblad",
    "phase one": "Phase One",
    "insta360": "Insta360",
}

# 抽样识别的文件数量上限
_SAMPLE_LIMIT = 3


def normalize_make(raw_make: str) -> str:
    """把 EXIF Make 规范化为展示名（如 'NIKON CORPORATION' -> 'Nikon'）。"""
    key = (raw_make or "").strip().lower()
    return _MAKE_NORMALIZE.get(key, (raw_make or "").strip())


def identify_camera(file_path: str | Path) -> dict | None:
    """读取单个媒体文件的 EXIF 品牌机型。

    Returns:
        {"make": "Nikon", "model": "NIKON Z 8", "raw_make": "NIKON CORPORATION"}
        任一关键信息缺失或读取失败返回 None。
    """
    path = Path(file_path)
    if not path.is_file():
        return None
    raw_make = raw_model = ""
    # 1. exifread（覆盖 RAW/TIFF 系：NEF/ARW/CR2/ORF/RAF/DNG 等）
    try:
        import exifread

        with open(path, "rb") as fh:
            tags = exifread.process_file(fh, details=False)
        raw_make = str(tags.get("Image Make", "") or "").strip()
        raw_model = str(tags.get("Image Model", "") or "").strip()
    except Exception as e:
        logger.debug(f"exifread 读取品牌机型失败 {path.name}: {e}")
    # 2. Pillow 兜底（JPEG/HEIC 系，exifread 失败或字段缺失时）
    if not raw_make or not raw_model:
        try:
            from PIL import Image

            with Image.open(path) as img:
                exif = img.getexif()
                raw_make = raw_make or str(exif.get(271, "") or "").strip()
                raw_model = raw_model or str(exif.get(272, "") or "").strip()
        except Exception as e:
            logger.debug(f"Pillow 读取品牌机型失败 {path.name}: {e}")

    if not raw_make and not raw_model:
        return None
    return {
        "make": normalize_make(raw_make),
        "model": raw_model,
        "raw_make": raw_make,
    }


def _candidate_files(root: Path, limit: int = 20) -> list[Path]:
    """收集用于抽样的媒体文件候选：顶层 + 两层子目录，避免全卡遍历。

    大容量卡的完整 rglob 可能扫描数十万文件；相机文件集中在
    顶层或 DCIM/<子目录>/ 两层内，浅层采样足够。
    """
    candidates: list[Path] = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.is_file() and config.is_media_file(entry.name):
                candidates.append(entry)
                if len(candidates) >= limit:
                    return candidates
        # 逐层向下最多两层（如 DCIM/100NIKON/）
        dirs = [p for p in sorted(root.iterdir()) if p.is_dir()]
        for _depth in range(2):
            next_dirs: list[Path] = []
            for sub in dirs:
                try:
                    for entry in sorted(sub.iterdir()):
                        if entry.is_file() and config.is_media_file(entry.name):
                            candidates.append(entry)
                            if len(candidates) >= limit:
                                return candidates
                        elif entry.is_dir():
                            next_dirs.append(entry)
                except (OSError, PermissionError):
                    continue
            if not next_dirs:
                break
            dirs = next_dirs
    except (OSError, PermissionError):
        pass
    return candidates


def identify_camera_for_card(folder: str | Path) -> dict | None:
    """识别整张卡的品牌机型：抽样最多 3 个媒体文件，取首个成功结果。"""
    root = Path(folder)
    if not root.is_dir():
        return None
    for candidate in _candidate_files(root)[:_SAMPLE_LIMIT]:
        info = identify_camera(candidate)
        if info:
            return info
    return None
