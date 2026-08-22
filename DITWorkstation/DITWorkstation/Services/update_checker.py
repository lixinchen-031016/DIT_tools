"""自动更新检查服务。

启动/手动触发时拉取一个静态 JSON 清单（URL 在设置中配置，默认为空=禁用）：
    {"version": "alpha.20260801", "download_url": "https://.../DITWorkstation.dmg",
     "notes": "更新说明文本"}

检查规则：
- 网络失败 / 超时 / JSON 解析失败一律降级为「无更新」，绝不阻塞启动；
- 版本比较按 alpha.YYYYMMDD 的日期部分；日期相同视为最新；
- 有更新时返回清单，由 UI 层决定提示与打开下载地址。
"""
import json
import re
import urllib.request
from dataclasses import dataclass

from DITWorkstation.App.version import APP_VERSION


@dataclass
class UpdateInfo:
    version: str
    download_url: str = ""
    notes: str = ""
    is_newer: bool = False
    error: str = ""


def _parse_date(version: str) -> int | None:
    """从 alpha.YYYYMMDD 版本串提取日期整数，提取不到返回 None。"""
    m = re.search(r"(\d{8})", str(version))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None



def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    """候选版本是否比当前版本更新（按 alpha.YYYYMMDD 日期比较）。"""
    cand_date = _parse_date(candidate)
    cur_date = _parse_date(current)
    if cand_date is None or cur_date is None:
        # 无法解析时：字符串不同的视为新（宽松处理），相同视为旧
        return candidate != current
    return cand_date > cur_date


def check_for_update(
    url: str,
    *,
    current_version: str = APP_VERSION,
    timeout: float = 8.0,
) -> UpdateInfo:
    """拉取更新清单并与当前版本比较。

    Args:
        url: 更新清单 URL；空字符串直接返回“禁用”。
        current_version: 本地版本号（默认应用版本）。
        timeout: 请求超时秒数。

    Returns:
        UpdateInfo（error 非空表示检查失败；is_newer=True 表示有更新）。
    """
    if not url:
        return UpdateInfo(version="", error="更新检查被禁用")

    # 仅允许 http/https，避免 file:// 等本地 scheme 被当作远端清单读取
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme not in ("http", "https"):
        return UpdateInfo(version="", error=f"不支持的更新清单地址协议: {scheme or '未知'}")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"DITWorkstation/{current_version}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(64 * 1024).decode("utf-8", errors="replace")
        payload = json.loads(raw)
    except Exception as exc:
        return UpdateInfo(version="", error=f"无法获取更新信息: {exc}")

    version = str(payload.get("version") or "")
    return UpdateInfo(
        version=version,
        download_url=str(payload.get("download_url") or ""),
        notes=str(payload.get("notes") or ""),
        is_newer=is_newer_version(version, current_version),
    )
