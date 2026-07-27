"""数据模型定义"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class ChecksumAlgorithm(Enum):
    """校验和算法"""
    XXHASH64 = "xxhash64"
    MD5 = "md5"


class AssetType(Enum):
    """资产类型"""
    IMAGE = "image"
    VIDEO = "video"
    RAW = "raw"
    AUDIO = "audio"
    OTHER = "other"


class CopyStatus(Enum):
    """拷贝状态"""
    PENDING = "pending"
    COPYING = "copying"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BackupStatus(Enum):
    """备份任务状态"""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # 部分成功


class AssetRating(Enum):
    """镜次评级 - MediaAsset.rating 字段的枚举化引用

    数据库存储为 INTEGER（0/1/2/3），MediaAsset.rating 仍为 int 类型以兼容
    旧代码与 SQLite 列类型；本枚举仅作为常量引用，消除散落的魔法数字。
    """
    NONE = 0       # 未评级
    USABLE = 1     # 可用
    BACKUP = 2     # 备选
    PREFERRED = 3  # 优选


# 评级标签：单一事实源，供 report_service / asset_info_view / search_view 共用
RATING_LABELS = {
    AssetRating.NONE.value: "未评级",
    AssetRating.USABLE.value: "★ 可用",
    AssetRating.BACKUP.value: "★★ 备选",
    AssetRating.PREFERRED.value: "★★★ 优选",
}


@dataclass
class FileChecksum:
    """文件校验和信息"""
    file_path: str
    algorithm: ChecksumAlgorithm
    hash_value: str
    file_size: int
    computed_at: datetime = field(default_factory=datetime.now)


@dataclass
class CopyTask:
    """单个文件拷贝任务"""
    source_path: str
    dest_path: str
    file_size: int = 0
    status: CopyStatus = CopyStatus.PENDING
    progress: float = 0.0
    source_checksum: Optional[str] = None
    dest_checksum: Optional[str] = None
    error_message: str = ""
    speed_mbps: float = 0.0


@dataclass
class BackupTarget:
    """备份目标位置"""
    path: str
    name: str = ""
    status: CopyStatus = CopyStatus.PENDING
    total_files: int = 0
    completed_files: int = 0
    total_bytes: int = 0
    copied_bytes: int = 0
    verified: bool = False
    error_message: str = ""


@dataclass
class BackupJob:
    """备份作业（包含多个目标）"""
    job_id: str
    source_path: str
    targets: List[BackupTarget] = field(default_factory=list)
    status: BackupStatus = BackupStatus.IDLE
    algorithm: ChecksumAlgorithm = ChecksumAlgorithm.XXHASH64
    total_files: int = 0
    total_bytes: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


@dataclass
class RenameRule:
    """重命名规则"""
    pattern: str = "{scene}_{shot}_{take}_{original}"
    scene: str = ""
    shot: str = ""
    take: str = ""
    prefix: str = ""
    suffix: str = ""
    start_number: int = 1
    padding: int = 3  # 序号位数


@dataclass
class MediaMetadata:
    """素材元数据"""
    file_path: str
    file_name: str
    file_size: int = 0
    file_type: str = ""
    camera_make: str = ""
    camera_model: str = ""
    lens_model: str = ""
    iso: int = 0
    aperture: str = ""
    shutter_speed: str = ""
    focal_length: str = ""
    date_taken: Optional[datetime] = None
    width: int = 0
    height: int = 0
    scene: str = ""
    shot: str = ""
    take: str = ""


@dataclass
class VideoMetadata:
    """视频元数据"""
    duration_seconds: float = 0.0
    width: int = 0
    height: int = 0
    codec: str = ""
    frame_rate: float = 0.0
    bit_rate: int = 0
    audio_codec: str = ""
    audio_sample_rate: int = 0


@dataclass
class Workspace:
    """工作区 - 项目的父级容器，对应物理目录

    一个工作区下可有多个项目（严格 1:N）。工作区的 path 是该工作区下所有项目
    的默认工作目录根（导入素材可默认复制到 path/<项目名>/ 下）。
    """
    workspace_id: str
    name: str
    path: str = ""  # 物理目录路径
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Project:
    """项目"""
    project_id: str
    name: str
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    base_path: str = ""
    workspace_id: Optional[str] = None  # 所属工作区 ID（1:N，可空以兼容旧数据）


@dataclass
class ShootingLog:
    """拍摄日志"""
    log_id: str
    project_id: str
    scene: str
    shot: str
    take: str
    description: str = ""
    camera: str = ""
    lens: str = ""
    iso: int = 0
    aperture: str = ""
    shutter_speed: str = ""
    notes: str = ""
    file_paths: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class MediaAsset:
    """素材资产记录"""
    asset_id: str
    project_id: str
    file_path: str
    file_name: str
    file_size: int = 0
    file_type: str = ""
    asset_type: str = "other"
    checksum_algorithm: str = "xxhash64"
    checksum_value: str = ""
    scene: str = ""
    shot: str = ""
    take: str = ""
    date_imported: datetime = field(default_factory=datetime.now)
    date_taken: Optional[datetime] = None
    camera_make: str = ""
    camera_model: str = ""
    backup_locations: List[str] = field(default_factory=list)
    log_id: Optional[str] = None
    is_working_copy: bool = False
    original_path: str = ""
    width: int = 0
    height: int = 0
    duration_seconds: float = 0.0
    lens_model: str = ""
    focal_length: str = ""
    video_metadata: str = ""  # VideoMetadata 的 JSON 序列化（codec/frame_rate/bit_rate/audio_codec/audio_sample_rate）
    rating: int = 0  # 镜次评级：0=未评级, 1=可用, 2=备选, 3=优选
