"""ASC MHL（Media Hash List）清单服务。

支持 ASC MHL 规范（https://github.com/ascmitc/mhl-specification）：

- 生成：``generate_asc_mhl`` 产出 ``<ascmhl version="2.0">`` 结构的 XML，
  兼容 Silverstack / OffShoot(Hedge) / YoYotta 等行业标准工具读取；
- 写入：``write_mhl_to_folder`` 按 ASC MHL 目录约定写入
  ``<目标>/.ascmhl/<名称>-<代数>-ascmhl.xml``；
- 解析：``parse_mhl`` 宽容读取 ASC MHL（``<ascmhl>`` 根）与
  原始 MHL（``<hashlist>`` 根）两种格式；
- 校验：``verify_against_mhl`` 依据清单逐文件核对哈希，
  支持取消与进度回调。

哈希格式名映射遵循 ASC MHL 规范命名（``xxh64``/``md5``）。
"""

import getpass
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from DITWorkstation.Models import ChecksumAlgorithm
from DITWorkstation.Utils import logger

TOOL_NAME = "DIT工作站"
TOOL_VERSION = "1.0"

# ASC MHL 规范哈希格式名 <-> 本项目算法
ASC_TO_ALGORITHM = {
    "xxh64": ChecksumAlgorithm.XXHASH64,
    "xxhash64": ChecksumAlgorithm.XXHASH64,  # 历史自产清单的写法
    "md5": ChecksumAlgorithm.MD5,
}
ALGORITHM_TO_ASC = {
    ChecksumAlgorithm.XXHASH64: "xxh64",
    ChecksumAlgorithm.MD5: "md5",
}

MHL_DIR_NAME = ".ascmhl"


@dataclass
class MHLFileEntry:
    """MHL 清单中的单文件哈希记录。"""

    path: str  # 相对路径（POSIX 分隔符）
    size: int | None = None
    last_modification_date: str | None = None
    hash_format: str = ""
    hash_value: str = ""


@dataclass
class MHLVerifyResult:
    """按清单校验的结果汇总。"""

    verified: int = 0
    mismatched: int = 0
    missing: int = 0
    skipped: int = 0
    cancelled: bool = False
    details: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"校验通过 {self.verified} 个"]
        if self.mismatched:
            parts.append(f"哈希不匹配 {self.mismatched} 个")
        if self.missing:
            parts.append(f"文件缺失 {self.missing} 个")
        if self.skipped:
            parts.append(f"跳过 {self.skipped} 个")
        if self.cancelled:
            parts.append("已取消")
        return "，".join(parts)


def _now_iso() -> str:
    """ASC MHL 要求带时区的 ISO8601 时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def generate_asc_mhl(
    files: list[dict],
    algorithm: ChecksumAlgorithm,
    source_root: str = "",
) -> str:
    """生成 ASC MHL v2 XML 字符串。

    Args:
        files: 文件列表，每项需含 ``path``（绝对或相对）、``size``；
            可选 ``checksum``（已有哈希值）与 ``relative``（相对路径）。
            缺 ``checksum`` 的项由调用方保证已计算——本函数不做 IO。
        algorithm: 生成清单时使用的哈希算法
        source_root: 源根目录（仅写入备注用途，可为空）
    """
    asc_format = ALGORITHM_TO_ASC.get(algorithm, algorithm.value)
    root = ET.Element("ascmhl", {"version": "2.0"})

    creator = ET.SubElement(root, "creatorinfo")
    try:
        ET.SubElement(creator, "hostname").text = socket.gethostname()
        ET.SubElement(creator, "username").text = getpass.getuser()
    except Exception as e:  # 主机信息非关键，失败不阻断清单生成
        logger.debug(f"读取主机信息失败（已跳过）: {e}")
    ET.SubElement(creator, "toolname").text = TOOL_NAME
    ET.SubElement(creator, "toolversion").text = TOOL_VERSION
    ET.SubElement(creator, "creationdate").text = _now_iso()

    process = ET.SubElement(root, "processinfo")
    ET.SubElement(process, "process").text = "generation"
    ET.SubElement(process, "hashformat").text = asc_format
    if source_root:
        ET.SubElement(process, "source").text = source_root

    for info in files:
        rel = info.get("relative") or Path(info["path"]).name
        entry = ET.SubElement(root, "hash")
        ET.SubElement(entry, "path").text = Path(rel).as_posix()
        ET.SubElement(entry, "size").text = str(info.get("size", ""))
        mtime = info.get("last_modification_date")
        if mtime:
            ET.SubElement(entry, "lastmodificationdate").text = mtime
        ET.SubElement(entry, "hashformat").text = asc_format
        ET.SubElement(entry, "hashvalue").text = info["checksum"]

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def next_mhl_generation(target_dir: Path, source_name: str) -> int:
    """返回目标目录 .ascmhl 内指定源名称的下一个清单代数（从 1 开始）。"""
    mhl_dir = Path(target_dir) / MHL_DIR_NAME
    if not mhl_dir.is_dir():
        return 1
    max_gen = 0
    for xml in mhl_dir.glob(f"{source_name}-*-ascmhl.xml"):
        stem = xml.name[len(source_name) + 1 : -len("-ascmhl.xml")]
        if stem.isdigit():
            max_gen = max(max_gen, int(stem))
    return max_gen + 1


def write_mhl_to_folder(
    mhl_xml: str, target_dir: Path, source_name: str
) -> Path:
    """按 ASC MHL 约定把清单写入 ``<target>/.ascmhl/<name>-<gen>-ascmhl.xml``。

    Returns:
        写入的清单文件路径。
    """
    mhl_dir = Path(target_dir) / MHL_DIR_NAME
    mhl_dir.mkdir(parents=True, exist_ok=True)
    generation = next_mhl_generation(mhl_dir.parent, source_name)
    mhl_path = mhl_dir / f"{source_name}-{generation:04d}-ascmhl.xml"
    mhl_path.write_text(mhl_xml, encoding="utf-8")
    logger.info(f"ASC MHL 清单已写入: {mhl_path}")
    return mhl_path


def parse_mhl(mhl_path: str | Path) -> tuple[str, list[MHLFileEntry]]:
    """解析 MHL 清单文件，返回 (根目录备注, 条目列表)。

    兼容 ASC MHL（``<ascmhl>``）与原始 MHL（``<hashlist>``）两种格式；
    单条目 ``hashformat`` 缺失时回退到 processinfo 声明的全局算法。
    Raises:
        ValueError: 不是合法 MHL 结构时抛出。
    """
    tree = ET.parse(str(mhl_path))
    root = tree.getroot()
    if root.tag == "ascmhl":
        default_format = ""
        process = root.find("processinfo")
        if process is not None:
            hf = process.find("hashformat")
            if hf is not None and hf.text:
                default_format = hf.text.strip().lower()
    elif root.tag == "hashlist":
        default_format = ""
    else:
        raise ValueError(f"无法识别的 MHL 格式: <{root.tag}>")

    entries: list[MHLFileEntry] = []
    for node in root.findall("hash"):
        path_node = node.find("path")
        if path_node is None or not (path_node.text or "").strip():
            continue
        fmt_node = node.find("hashformat")
        value_node = node.find("hashvalue")
        size_node = node.find("size")
        date_node = node.find("lastmodificationdate") or node.find("hashdate")
        entries.append(
            MHLFileEntry(
                path=path_node.text.strip(),
                size=int(size_node.text) if size_node is not None and (size_node.text or "").isdigit() else None,
                last_modification_date=date_node.text if date_node is not None else None,
                hash_format=(fmt_node.text or "").strip().lower() if fmt_node is not None else default_format,
                hash_value=value_node.text.strip() if value_node is not None else "",
            )
        )
    return "", entries


def verify_against_mhl(
    mhl_path: str | Path,
    root_dir: str | Path,
    checksum_service,
    cancel_check=None,
    progress_callback=None,
) -> MHLVerifyResult:
    """依据 MHL 清单逐文件核对哈希。

    Args:
        mhl_path: MHL XML 文件路径
        root_dir: 清单内相对路径的基准目录（卡/盘/备份目录）
        checksum_service: ChecksumService 实例
        cancel_check: 取消回调
        progress_callback: (completed, total, message) 进度回调

    Returns:
        MHLVerifyResult，含逐文件明细。
    """
    _, entries = parse_mhl(mhl_path)
    root = Path(root_dir)
    result = MHLVerifyResult()
    total = len(entries)

    for idx, entry in enumerate(entries, start=1):
        if cancel_check and cancel_check():
            result.cancelled = True
            break
        if progress_callback:
            progress_callback(
                idx - 1, total, f"校验: {entry.path}"
            )

        algorithm = ASC_TO_ALGORITHM.get(entry.hash_format)
        if algorithm is None:
            result.skipped += 1
            result.details.append(
                {"path": entry.path, "status": "skipped", "reason": f"不支持的算法 {entry.hash_format}"}
            )
            continue
        file_path = root / entry.path
        if not file_path.is_file():
            result.missing += 1
            result.details.append({"path": entry.path, "status": "missing"})
            continue

        actual = checksum_service.compute_file_checksum(
            str(file_path), algorithm, cancel_check=cancel_check
        )
        if actual.hash_value.lower() == entry.hash_value.lower():
            result.verified += 1
            result.details.append({"path": entry.path, "status": "verified"})
        else:
            result.mismatched += 1
            result.details.append(
                {
                    "path": entry.path,
                    "status": "mismatched",
                    "expected": entry.hash_value,
                    "actual": actual.hash_value,
                }
            )

    if progress_callback and not result.cancelled:
        progress_callback(total, total, "MHL 校验完成")
    logger.info(f"MHL 校验完成: {result.summary()} ({mhl_path})")
    return result
