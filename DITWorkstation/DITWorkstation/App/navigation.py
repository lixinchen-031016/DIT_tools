"""导航配置（单一事实源）

将 NAV_ITEMS 从 main_window 抽离到独立模块，消除 Views↔main_window 循环依赖。
视图跳转应通过 get_nav_index 查询索引，或通过 data_bus 发 navigate_to 信号。
"""
from typing import Optional

# 导航项单一事实源：顺序即 SOP 流程顺序
NAV_ITEMS = [
    ("dashboard", "🏠 项目概览", "当前项目进度看板与 SOP 引导"),
    ("import", "① 📁 媒体导入", "导入图片视频到项目"),
    ("backup", "② 📦 数据备份", "安全拷贝与多重备份"),
    ("log", "③ 📋 拍摄日志", "场景/镜头/镜次管理"),
    ("raw", "④ 🎞 RAW提取", "JPG筛选后提取RAW"),
    ("rename", "⑤ ✏️ 文件重命名", "批量重命名与元数据"),
    ("search", "⑥ 🔍 素材检索", "按条件快速检索"),
    ("asset_info", "⑦ ℹ️ 素材信息", "查看素材EXIF与元数据详情"),
    ("report", "⑧ 📊 报告生成", "数据管理与QC报告"),
]


def get_nav_index(key: str) -> Optional[int]:
    """按 key 查询导航索引，未找到返回 None。"""
    for i, (k, _, _) in enumerate(NAV_ITEMS):
        if k == key:
            return i
    return None
