"""存储卡介质身份与多卡批处理队列。

目标：
1. 介质身份：对存储卡内容计算稳定指纹（顶层媒体文件名 + 关键目录结构），
   用于「自动处理过的卡不再重复处理」和跨插拔识别同一张卡。
2. 多卡批处理：把多张卡排入队列串行处理，避免并发拷贝互相抢 IO，
   单卡失败不影响队列中后续卡片。

指纹策略说明：
- 采用「顶层媒体文件名 + 关键目录名」而不是卷序列号（跨平台读取困难），
  对同一张卡的内容唯一性足够；采样最多 FINGERPRINT_MAX_FILES 个文件名，
  避免超大型卡枚举过慢。
"""

import hashlib
from pathlib import Path

from DITWorkstation.App import config

# 指纹采样上限：同一卡只取前 N 个顶层媒体文件做哈希
FINGERPRINT_MAX_FILES = 200


def media_files_in_top_level(
    root: Path, limit: int = FINGERPRINT_MAX_FILES
) -> list[str]:
    """返回根目录下（非递归）媒体文件的文件名列表，按名称排序。"""
    if not root.is_dir():
        return []
    names: list[str] = []
    try:
        for entry in root.iterdir():
            if entry.is_file() and config.is_media_file(entry.name):
                names.append(entry.name)
                if len(names) >= limit:
                    break
    except (OSError, PermissionError):
        pass
    return sorted(names)


def key_directories(root: Path) -> list[str]:
    """返回根目录下与相机相关的顶层目录名（DCIM/AVCHD/PRIVATE 等）。"""
    if not root.is_dir():
        return []
    try:
        return sorted(
            e.name
            for e in root.iterdir()
            if e.is_dir()
            and e.name.upper() in ("DCIM", "AVCHD", "PRIVATE", "M4ROOT", "MISC")
        )
    except (OSError, PermissionError):
        return []


def compute_card_fingerprint(path: str) -> str:
    """计算存储卡内容指纹。

    哈希内容 = 关键目录名 + 且顶层媒体文件名（采样排序后）。
    返回固定长度十六进制字符串；空卡/不可读返回 ""。
    """
    root = Path(path)
    dirs = key_directories(root)
    names = media_files_in_top_level(root)
    payload = "|".join(dirs + names)
    if not payload:
        return ""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def is_fingerprint_processed(fingerprint: str) -> bool:
    """指纹是否已在已处理列表中（防重复自动处理）。"""
    if not fingerprint:
        return False
    return fingerprint in getattr(config, "processed_card_fingerprints", [])


def mark_fingerprint_processed(fingerprint: str) -> None:
    """记录指纹到已处理列表（最多保留 50 条，防止无限增长）。"""
    from DITWorkstation.Utils import save_app_settings

    if not fingerprint:
        return
    current = list(getattr(config, "processed_card_fingerprints", []))
    if fingerprint in current:
        return
    current.append(fingerprint)
    config.processed_card_fingerprints = current[-50:]
    save_app_settings(processed_card_fingerprints=config.processed_card_fingerprints)


# ===== 擦除保护状态 =====
# 未验证备份的卡不允许用户放心格式化：verified 才视为可安全擦除。

CARD_VERIFIED = "verified"  # 备份完成且校验通过
CARD_PENDING = "pending"  # 已导入但未做校验备份
CARD_FAILED = "failed"  # 备份失败/未完成
_MAX_CARD_STATES = 50


def get_card_state(fingerprint: str) -> str:
    """返回卡的保护状态；未知指纹返回 "unknown"。"""
    if not fingerprint:
        return "unknown"
    states = getattr(config, "card_verification_states", {})
    return states.get(fingerprint, "unknown")


def mark_card_state(fingerprint: str, state: str) -> None:
    """记录卡的擦除保护状态（最多保留 50 条，防止无限增长）。"""
    from DITWorkstation.Utils import save_app_settings

    if not fingerprint or state not in (CARD_VERIFIED, CARD_PENDING, CARD_FAILED):
        return
    states = dict(getattr(config, "card_verification_states", {}))
    states[fingerprint] = state
    # 只保留最近 50 张卡的状态
    if len(states) > _MAX_CARD_STATES:
        recent_keys = list(states)[-_MAX_CARD_STATES:]
        states = {k: states[k] for k in recent_keys}
    config.card_verification_states = states
    save_app_settings(card_verification_states=states)


def card_wipe_warning(path: str) -> str | None:
    """计算卡的擦除风险提示；已验证备份返回 None，否则返回警告文案。"""
    state = get_card_state(compute_card_fingerprint(path))
    if state == CARD_VERIFIED:
        return None
    if state == CARD_PENDING:
        return "此卡素材已导入但未做校验备份，请勿格式化/擦除"
    if state == CARD_FAILED:
        return "此卡上次备份未完成或未通过校验，请勿格式化/擦除"
    return "此卡尚未在本工具中完成校验备份，请勿格式化/擦除"


def card_state_note_from_automation_result(result, fingerprint: str) -> str:
    """根据 CardAutomationService.execute 的结果更新卡擦除保护状态。

    - 备份完成且校验通过（且源卡复读无异常）→ verified（可安全格式化）；
    - 备份部分完成/失败或复读发现内容变化 → failed（警告勿擦除）；
    - 未配置备份步骤（仅导入/整理）→ pending（警告勿擦除）。
    取消的任务不更新状态。

    Returns:
        需要展示给用户的提示文案；无需提示时返回空字符串。
    """
    if not isinstance(result, dict) or result.get("cancelled"):
        return ""
    if result.get("backup") is not None:
        backup = result["backup"]
        reread_bad = list(getattr(backup, "source_reread_mismatches", []) or [])
        if backup.status.value == "completed" and not reread_bad:
            mark_card_state(fingerprint, CARD_VERIFIED)
            return "✅ 此卡已完成校验备份，可安全格式化"
        if reread_bad:
            mark_card_state(fingerprint, CARD_FAILED)
            return (
                f"⚠️ 源卡复读发现 {len(reread_bad)} 个文件内容变化，"
                f"此卡可能劣化，请勿擦除并尽快重新备份"
            )
        mark_card_state(fingerprint, CARD_FAILED)
        return "⚠️ 备份未完成或未通过校验，请勿格式化/擦除此卡"
    mark_card_state(fingerprint, CARD_PENDING)
    return "⚠️ 素材已导入但未做校验备份，请勿格式化/擦除此卡"


class CardBatchQueue:
    """多卡串行处理队列。

    使用方式（主窗口持有单例）：
        queue = CardBatchQueue()
        queue.on_start(path)  # 插入队列，队列空闲时立即回调 start_cb(path)
        queue.on_finished()   # 处理完成，派发下一张
        queue.on_failed()     # 处理失败，同样派发下一张
    """

    def __init__(self, start_cb=None, dedupe: bool = True):
        self._queue: list[str] = []
        self._in_flight: str | None = None
        self._start_cb = start_cb
        self._dedupe = dedupe

    def set_start_callback(self, cb) -> None:
        self._start_cb = cb

    @property
    def in_flight(self) -> str | None:
        return self._in_flight

    @property
    def queued(self) -> list[str]:
        return list(self._queue)

    @property
    def busy(self) -> bool:
        return self._in_flight is not None

    def enqueue(self, path: str) -> bool:
        """入队；若内容指纹已处理过且开启去重，则不入队。返回是否真正入队。"""
        if not path or path in self._queue:
            return False
        if self._in_flight == path:
            return False
        if self._dedupe and is_fingerprint_processed(compute_card_fingerprint(path)):
            return False
        self._queue.append(path)
        if self._in_flight is None:
            self._pump()
        return True

    def _pump(self) -> None:
        if self._in_flight is not None:
            return
        if not self._queue:
            return
        self._in_flight = self._queue.pop(0)
        if self._start_cb:
            try:
                self._start_cb(self._in_flight)
            except Exception as exc:
                from DITWorkstation.Utils import logger

                logger.error(f"存储卡队列启动回调失败: {exc}")
                self.on_failed()

    def on_finished(self, fingerprint: str = "") -> None:
        """当前卡处理完成：记录指纹并派发下一张。"""
        if fingerprint:
            mark_fingerprint_processed(fingerprint)
        self._in_flight = None
        self._pump()

    def on_failed(self) -> None:
        """当前卡处理失败：不记录指纹（下次仍可重试），直接派发下一张。"""
        self._in_flight = None
        self._pump()

    def clear(self) -> None:
        self._queue.clear()
