"""导航配置（单一事实源）

将 NAV_ITEMS 从 main_window 抽离到独立模块，消除 Views↔main_window 循环依赖。
视图跳转应通过 get_nav_index 查询索引，或通过 data_bus 发 navigate_to 信号。
"""

# 导航项单一事实源：顺序即 SOP 流程顺序
# 文案不带序号（§4.1：序号与分组职责重复，由 NAV_GROUPS 承担流程语义）
NAV_ITEMS = [
    ("dashboard", "项目概览", "当前项目进度看板与 SOP 引导"),
    ("import", "媒体导入", "导入图片视频到项目"),
    ("backup", "数据备份", "安全拷贝与多重备份"),
    ("log", "拍摄日志", "场景/镜头/镜次管理"),
    ("raw", "RAW提取", "JPG筛选后提取RAW"),
    ("rename", "文件重命名", "批量重命名与元数据"),
    ("search", "素材检索", "按条件快速检索"),
    ("asset_info", "素材信息", "查看素材EXIF与元数据详情"),
    ("report", "报告生成", "数据管理与QC报告"),
]

# 导航分组（§4.1：按「工作台 / 工作流 / 输出与管理」三组组织，职责分区）。
# 仅影响主窗口导航列表的渲染（分组标题行）；NAV_ITEMS 顺序与 get_nav_index
# 的索引语义完全不变——索引仍是「激活导航列表中的位置」。
NAV_GROUPS: dict[str, list[str]] = {
    "工作台": ["dashboard"],
    "工作流": ["import", "backup", "log", "raw", "rename", "search", "asset_info"],
    "输出与管理": ["report"],
}


def get_nav_group(key: str) -> str:
    """按 key 查询所属分组名；未分组（或未来新增未登记的 key）返回空字符串。"""
    for group_name, keys in NAV_GROUPS.items():
        if key in keys:
            return group_name
    return ""


def get_nav_index(key: str) -> int | None:
    """按 key 查询导航索引，未找到或当前模式下未激活时返回 None。

    索引基于「当前激活导航列表」（功能模式过滤后），与主窗口导航列表、
    视图栈顺序保持一致。个人模式下 log / report 返回 None，
    调用方必须对 None 做容错（禁止直接传给 setCurrentRow()）。

    注意：feature_flags 为局部导入，避免 navigation ↔ feature_flags
    模块顶层循环导入（feature_flags 依赖本模块的 NAV_ITEMS）。
    """
    from DITWorkstation.App.feature_flags import get_active_nav_items

    for i, (k, _, _) in enumerate(get_active_nav_items()):
        if k == key:
            return i
    return None
