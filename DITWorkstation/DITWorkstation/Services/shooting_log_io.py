"""拍摄日志 CSV / 标准镜头清单导入导出。

- 导出：把项目拍摄日志写成 CSV（utf-8-sig，Excel 可直接打开），
  同时提供 Resolve 风格镜头清单列（scene/shot/take/name），便于交接。
- 导入：从 CSV / XLSX 场记单调入日志；按「场景+镜头+镜次」作为业务键，
  已存在则更新而非重复创建。

CSV 列（英文列头，兼容中文字段别名）：
    scene, shot, take, description, camera, lens, iso, aperture,
    shutter_speed, notes
"""
import csv
from pathlib import Path

from DITWorkstation.Models import ShootingLog
from DITWorkstation.Utils import logger, now_local

# 业务键：场景+镜头+镜次 唯一
KEY_FIELDS = ("scene", "shot", "take")

CSV_FIELDS = ("scene", "shot", "take", "description", "camera", "lens",
              "iso", "aperture", "shutter_speed", "notes")

# 中文别名 -> 标准字段
_ALIASES = {
    "场景": "scene", "镜头": "shot", "镜次": "take", "描述": "description",
    "摄影机": "camera", "相机": "camera", "镜头型号": "lens", "ISO": "iso",
    "光圈": "aperture", "快门": "shutter_speed", "备注": "notes",
}


def _normalize_row(row: dict) -> dict:
    """把行 dict 的列名统一到标准字段（含中文别名）。"""
    out = {}
    for key, value in row.items():
        if value is None:
            continue
        k = str(key).strip()
        field = _ALIASES.get(k, k)
        if field in CSV_FIELDS:
            out[field] = str(value).strip()
    return out


def log_business_key(log: ShootingLog) -> tuple[str, str, str]:
    return (log.scene or "").strip(), (log.shot or "").strip(), (log.take or "").strip()


def export_logs_csv(logs: list, target_path: str) -> bool:
    """把拍摄日志导出为 CSV。返回是否成功。"""
    if not logs:
        logger.info("拍摄日志导出：无数据，跳过生成文件")
        return False
    try:
        with open(target_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for log in logs:
                row = {f: getattr(log, f, "") for f in CSV_FIELDS}
                row["iso"] = row["iso"] if row["iso"] else ""
                writer.writerow(row)
        logger.info(f"拍摄日志导出完成: {target_path} ({len(logs)} 条)")
        return True
    except (OSError, PermissionError) as exc:
        logger.warning(f"拍摄日志导出失败 {target_path}: {exc}")
        return False


def _read_csv_rows(path: Path) -> tuple[list[dict], str | None]:
    """读取 CSV（自动识别 utf-8-sig / utf-8 / gbk）。"""
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, encoding=encoding, newline="") as fh:
                return list(csv.DictReader(fh)), None
        except (UnicodeDecodeError, OSError, KeyError) as exc:
            last_error = str(exc)
    return [], (last_error or "无法解析 CSV")


def import_logs_csv(
    source_path: str,
    project_id: str,
    db_service,
    *,
    update_existing: bool = True,
) -> dict:
    """从 CSV 导入拍摄日志。

    按 business key（scene, shot, take）匹配；已存在时默认更新字段，
    update_existing=False 时跳过已存在的日志。

    Returns:
        {"created": int, "updated": int, "skipped": int, "rows": int, "errors": [str]}
    """
    path = Path(source_path)
    if not path.is_file():
        return {"created": 0, "updated": 0, "skipped": 0, "rows": 0,
                "errors": [f"文件不存在: {source_path}"]}

    rows, err = _read_csv_rows(path)
    if err is not None:
        return {"created": 0, "updated": 0, "skipped": 0, "rows": 0, "errors": [err]}
    if not rows:
        return {"created": 0, "updated": 0, "skipped": 0, "rows": 0, "errors": []}

    existing = db_service.get_shooting_logs(project_id)
    existing_by_key = {log_business_key(log): log for log in existing}

    stats = {"created": 0, "updated": 0, "skipped": 0, "rows": len(rows), "errors": []}
    for idx, raw in enumerate(rows, start=2):
        row = _normalize_row(raw)
        scene, shot, take = (row.get("scene") or "").strip(), (row.get("shot") or "").strip(), (row.get("take") or "").strip()
        if not (scene and shot and take):
            stats["skipped"] += 1
            stats["errors"].append(f"第 {idx} 行缺少 场景/镜头/镜次，已跳过")
            continue
        try:
            iso = int(row.get("iso") or 0)
        except ValueError:
            iso = 0
        data = dict(
            description=row.get("description", ""),
            camera=row.get("camera", ""),
            lens=row.get("lens", ""),
            iso=iso,
            aperture=row.get("aperture", ""),
            shutter_speed=row.get("shutter_speed", ""),
            notes=row.get("notes", ""),
        )
        match = existing_by_key.get((scene, shot, take))
        if match is None:
            db_service.create_shooting_log(ShootingLog(
                log_id=_new_id(),
                project_id=project_id,
                scene=scene, shot=shot, take=take,
                description=data["description"], camera=data["camera"],
                lens=data["lens"], iso=data["iso"], aperture=data["aperture"],
                shutter_speed=data["shutter_speed"], notes=data["notes"],
                created_at=now_local(),
            ))
            stats["created"] += 1
        elif update_existing:
            match.description = data["description"]
            match.camera = data["camera"]
            match.lens = data["lens"]
            match.iso = data["iso"]
            match.aperture = data["aperture"]
            match.shutter_speed = data["shutter_speed"]
            match.notes = data["notes"]
            db_service.update_shooting_log(match)
            stats["updated"] += 1
        else:
            stats["skipped"] += 1

    logger.info(f"拍摄日志导入完成: 新建 {stats['created']}，更新 {stats['updated']}，跳过 {stats['skipped']}")
    return stats


def _new_id() -> str:
    import uuid
    return str(uuid.uuid4())[:8]
