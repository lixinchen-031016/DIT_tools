"""功能模式开关（团队版 / 个人版）

设计文档：DITWorkstation/docs/功能模式开关设计方案.md（路线 A：运行时功能模式）

核心语义：
- 模式是设备级配置，持久化在 settings.json 的 app_config.usage_mode；
- 模式在启动时确定，主窗口按模式构建，修改后重启生效（不做运行时重建）；
- 个人模式只做 UI 与交互裁剪，不删除数据库数据、不做数据库迁移；
- 非法值与缺失值均回退到团队模式（老用户升级后行为不变）。

模块职责：
- 读取并校验当前使用模式；
- 持久化使用模式；
- 返回当前模式激活的导航项；
- 判断导航项是否可见；
- 判断组件级特性是否启用。

依赖约束（避免循环导入）：
- 本模块可以导入 App.navigation.NAV_ITEMS；
- App.navigation 不应在模块顶层反向导入本模块，
  navigation.get_nav_index() 需要过滤激活列表时使用局部导入。
"""
from enum import Enum

from DITWorkstation.App import config
from DITWorkstation.App.navigation import NAV_ITEMS


class UsageMode(str, Enum):
    """使用模式：团队版（完整 DIT 工作流）/ 个人版（独立创作者裁剪界面）"""
    TEAM = "team"
    PERSONAL = "personal"


# 个人模式保留的导航项（顺序与 NAV_ITEMS 一致：dashboard/import/backup/raw/rename/search/asset_info）
PERSONAL_NAV_KEYS = (
    "dashboard",
    "import",
    "backup",
    "raw",
    "rename",
    "search",
    "asset_info",
)

# 组件级特性开关：仅团队模式可用的特性集合。
# 个人模式下 is_enabled() 对这些特性返回 False；未知特性按启用处理，
# 避免未来新增特性被意外禁用。
_TEAM_ONLY_FEATURES = frozenset({
    "workspace_selector",   # 工作区下拉 / 新建 / 编辑
    "shooting_log",         # 拍摄日志（导航、关联入口、日志筛选）
    "ratings",              # 素材评级（控件、批量操作、筛选）
    "report",               # 报告生成
    "multi_target_backup",  # 多目标备份（个人模式仅允许单目标）
    "backup_templates",     # 备份方案模板
    "mhl_export",           # MHL 校验清单导出
    "project_templates",    # 项目模板
    "archive_restore",      # 归档 / 恢复
    "audit_panel",          # 最近操作审计面板
    "sop_guide",            # SOP 团队引导（含日志/报告流程的文案与入口）
    "card_automation",      # 存储卡自动导入/备份
})


def get_usage_mode() -> UsageMode:
    """读取当前使用模式。

    非法值与缺失值均回退到 UsageMode.TEAM（保证老设置文件与脏数据安全）。
    """
    raw = getattr(config, "usage_mode", None) or UsageMode.TEAM.value
    try:
        return UsageMode(str(raw))
    except ValueError:
        return UsageMode.TEAM


def set_usage_mode(mode) -> None:
    """校验并持久化使用模式到 settings.json 的 app_config.usage_mode。

    同时写回全局 config.usage_mode 供当前进程读取；
    界面层面的模式在下次启动时生效（主窗口启动时按模式构建）。

    Args:
        mode: UsageMode 枚举或其字符串值（"team" / "personal"）

    Raises:
        ValueError: mode 不是合法的 UsageMode 值（拒绝写入脏数据）
    """
    mode = UsageMode(mode)  # 兼容枚举与字符串；非法值抛 ValueError
    config.usage_mode = mode.value
    # 局部导入：Utils.common 在模块顶层导入 App.config，
    # 顶层反向导入会在特定加载顺序下形成循环
    from DITWorkstation.Utils import save_app_settings
    save_app_settings(usage_mode=mode.value)


def is_personal_mode() -> bool:
    """当前是否为个人模式。"""
    return get_usage_mode() == UsageMode.PERSONAL


def is_team_mode() -> bool:
    """当前是否为团队模式。"""
    return get_usage_mode() == UsageMode.TEAM


def get_active_nav_items() -> list[tuple[str, str, str]]:
    """返回当前模式激活的导航项列表。

    团队模式返回全部 9 项；个人模式返回 7 项（隐藏 log / report），
    相对顺序与 NAV_ITEMS 保持一致。所有导航索引相关逻辑
    （导航列表、视图栈、快捷键、F5、跨视图跳转）都必须以本列表为准。
    """
    if is_personal_mode():
        return [item for item in NAV_ITEMS if item[0] in PERSONAL_NAV_KEYS]
    return list(NAV_ITEMS)


def is_nav_enabled(key: str) -> bool:
    """判断指定导航项在当前模式下是否可见。"""
    return any(item[0] == key for item in get_active_nav_items())


def get_active_nav_index(key: str) -> int | None:
    """按 key 查询其在激活导航列表中的索引；未激活或不存在时返回 None。

    调用方必须对 None 做容错，禁止把 None 直接传给 setCurrentRow()。
    """
    for i, item in enumerate(get_active_nav_items()):
        if item[0] == key:
            return i
    return None


def is_enabled(feature: str) -> bool:
    """判断组件级特性在当前模式下是否启用。

    团队模式全部启用；个人模式仅关闭 _TEAM_ONLY_FEATURES 中的特性；
    未知特性按启用处理（避免新增特性被意外禁用）。
    """
    if is_team_mode():
        return True
    return feature not in _TEAM_ONLY_FEATURES


def ensure_personal_default_workspace_path(db_service) -> str | None:
    """个人模式：确保 default 工作区拥有合法物理路径（步骤1）。

    设计文档指出个人模式无「创建工作区」步骤，default 工作区 path 初始为空，
    导致导入时「复制到工作区」被禁用、且没有任何入口设置目录。

    本函数在两个时机调用：
    - 应用启动时（main.py，兼容旧库：已有个人模式用户的 default 工作区 path 可能仍为空）
    - 首启向导完成后（first_run_wizard.py，个人模式跳过工作区创建步骤的场景）

    团队模式直接跳过（默认工作区由其显式创建工作区逻辑管理，本函数不干预）。

    Args:
        db_service: DatabaseService 实例（调用方负责提供，保持单例一致）

    Returns:
        实际生效的 default 工作区目录路径；非个人模式、目录不可写或失败时返回 None。
    """
    if is_team_mode():
        return None
    try:
        from DITWorkstation.Utils import is_writable_directory, logger
        ws = db_service.get_or_create_default_workspace()
        if ws.path:
            if is_writable_directory(ws.path):
                return ws.path
            logger.warning(f"个人模式默认工作区目录不可写: {ws.path}")
            return None
        # 引用配置项（步骤4），不再硬编码路径，支持环境变量覆盖
        target = config.personal_default_workspace_path
        if not is_writable_directory(target, create=True):
            logger.warning(f"个人模式默认工作区目录不可写: {target}")
            return None
        if db_service.update_workspace("default", path=str(target)):
            return str(target)
    except Exception as e:
        logger.warning(f"确保个人模式默认工作区路径失败: {e}")
    return None
