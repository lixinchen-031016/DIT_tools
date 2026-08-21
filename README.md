# 🎬 DIT 工作站

> 专业摄影数据管理桌面应用 — 从素材导入到报告生成的一站式 DIT 工作流

DIT 工作站是一款面向影视与摄影行业的桌面端数据管理工具，为 DIT（数字影像工程师）提供从素材导入、备份、RAW 提取、重命名、拍摄日志、素材信息、检索到报告生成的完整工作流。基于 PySide6 构建原生桌面体验，使用 SQLite 进行本地数据持久化，支持高性能校验、跨平台 PDF 报告输出与一键打包分发（macOS onedir / Windows 单文件）。

当前版本采用 `alpha.YYYYMMDD` 格式，日期部分为启动/打包当天的本地日期，例如 `alpha.20260821`（具体日期随启动或打包时间变化）。

---

## 📑 目录

- [✨ 功能特性](#-功能特性)
- [🆕 新版本亮点](#-新版本亮点)
- [🧰 技术栈](#-技术栈)
- [📁 项目结构](#-项目结构)
- [🚀 快速开始](#-快速开始)
- [🧪 测试](#-测试)
- [📦 打包分发](#-打包分发)
- [🎨 支持的媒体格式](#-支持的媒体格式)
- [🏗 架构说明](#-架构说明)
- [💾 数据存储](#-数据存储)
- [📖 用户文档](#-用户文档)
- [📄 许可证](#-许可证)

---

## ✨ 功能特性

DIT 工作站围绕 9 大功能模块构建完整的数据管理闭环：

| # | 模块 | 图标 | 说明 |
|---|------|------|------|
| 1 | 项目概览 | 🏠 | 当前项目进度看板，聚合展示导入/备份/日志/报告进度，提供 SOP「下一步」快捷跳转；支持项目归档/恢复（zip 打包，可选附带素材文件）与项目模板（从模板新建 / 保存为模板） |
| 2 | 媒体导入 | 📁 | 将图片/视频/RAW 导入项目，支持引用模式与复制模式，自动读取 EXIF，可选关联拍摄日志，按路径查重；扫描后可勾选要导入的文件（选中行显示缩略图预览），支持全选/反选 |
| 3 | 数据备份 | 📦 | 从存储卡多目标并行备份，校验和验证（XXHash64/MD5），支持导出 ASC MHL 校验清单，完成后回写素材的备份位置；备份前自动预检目标磁盘空间，支持对已有备份做独立完整性再校验；目标已存在且校验一致的文件自动跳过（断点续传），失败文件记入备份历史可一键重试；支持备份方案模板复用与相机卡自动备份 |
| 4 | RAW 提取 | 🎞 | 根据筛选后的 JPG 文件，从 RAW 源文件夹自动匹配并提取对应 RAW，提取后可自动入库 |
| 5 | 文件重命名 | ✏️ | 按场景/镜头/镜次规则批量重命名，支持模板变量，预览确认后执行，自动防覆盖 |
| 6 | 拍摄日志 | 📋 | 管理场景/镜头/镜次拍摄记录，支持「从代表素材填充 EXIF」，双向关联素材与日志 |
| 7 | 素材检索 | 🔍 | 按项目/场景/镜头/类型/关键词/日志/评级/日期/标签多维度组合检索（分页展示，每页 500 条），支持按拍摄日期时间线聚合并下钻到当天列表，结果可导出 CSV；支持按校验和跨项目查重 |
| 8 | 素材信息 | ℹ️ | 查看素材 EXIF 与元数据详情，支持缩略图预览、单个/批量重新读取 EXIF，可设置镜次评级，可一键导出项目素材清单 CSV；支持标签/备注编辑、批量评级、软删除、丢失文件检测与重新链接 |
| 9 | 报告生成 | 📊 | 生成 PDF 数据备份报告、素材统计报告和操作审计报表，含镜次评级分布统计及审计汇总；按平台查找预装中文字体 |

---

## 🆕 新版本亮点

### 工作区-项目两级结构
- **工作区**是项目的父级容器，对应一个物理目录，用于组织一组相关项目
- 严格 1:N 关系：一个工作区下可创建多个项目
- 默认工作区（id='default'）不可删除，删除非默认工作区时其下项目自动归入默认工作区
- 全局共享控件 `WorkspaceProjectSelector` 统一 9 个视图的工作区/项目选择体验

### 镜次评级（⭐）
- 4 档评级：未评级 / ★ 可用 / ★★ 备选 / ★★★ 优选
- 在「素材信息」视图设置评级，在「素材检索」视图按评级筛选
- 「报告生成」视图的素材统计报告含评级分布统计，优选行高亮显示

### 首启向导
- 首次启动且数据库无项目时自动弹出
- 团队模式为 5 页：欢迎 → 创建工作区 → 创建项目 → SOP 操作链提示 → 完成
- 个人模式为 3 页：欢迎 → 创建项目 → 完成（工作区由系统自动准备）
- 完成后自动跳转到「媒体导入」视图

### 使用场景（团队 / 个人模式）
- 「设置 → 使用场景」可切换团队模式（默认，完整 9 模块工作流）与个人模式（独立创作者）
- 个人模式精简为 7 个模块：项目概览 / 媒体导入 / 数据备份 / RAW 提取 / 文件重命名 / 素材检索 / 素材信息
- 个人模式仅做界面与交互裁剪：隐藏工作区操作（项目自动归入默认工作区且全部项目可见）、拍摄日志、素材评级、报告、模板、归档/恢复、审计面板与相机卡自动化；备份限单目标
- 不删除任何数据库数据，两种模式共享同一数据库格式；切换写入 `settings.json` 的 `app_config.usage_mode`，重启后生效，可随时切回

### 项目管理（归档 / 恢复）
- 「项目概览」提供「归档当前项目…」：把项目信息 + 拍摄日志 + 素材元数据打包为 zip，可选附带素材文件副本
- 归档包内含 manifest.json / assets.json / logs.json / checksums.txt，自包含、可交接
- 「恢复项目…」：从归档包重建项目到当前工作区，可选还原素材文件并自动校验校验和；与现有项目重名时自动追加时间戳后缀
- **归档安全防护**：恢复时校验 zip 成员路径（拒绝绝对路径 / 父目录跳转 / NUL 字符）、限制单文件与解压总大小、阻止符号链接越界，并采用「临时目录写入 → 校验通过后再移入目标」策略，避免校验失败留下半成品文件

### 备份健壮性
- 备份前对每个目标执行磁盘空间预检，剩余空间不足时拒绝启动并提示
- 「校验已有备份」：按素材的 backup_locations 对备份盘做独立完整性校验（存在性 + 校验和比对），检测位错误/文件丢失
- 「断点续传 / 失败重试」：重新备份时目标已存在且校验一致的文件自动跳过；拷贝失败的文件按目标记入备份历史，选中后「重试失败文件」只补拷失败项，中途拔卡/断电后可快速续备
- 「任务快照持久化」：备份任务运行中实时保存快照（含未处理文件列表），应用异常退出后重启可恢复到上次进度
- 「项目健康容量趋势」：项目概览记录备份目标容量历史，绘制最近趋势并在低于 15% 或预计 7 天内耗尽时预警
- 「相机卡 SOP 链」：自动化可按配置顺序执行导入、备份、RAW 提取、重命名和备份报告；任一步失败会停止后续步骤并保留任务状态
- 「素材拍摄时间线」：素材检索可按拍摄日期聚合浏览，点击日期卡片下钻到当天素材列表
- 「操作审计报表」：日志查看器支持将当前筛选结果及按日/事件汇总导出为 PDF，同时保留 CSV 导出
- 「素材检索」结果可导出 CSV 素材清单（utf-8-sig，Excel 直接打开）
- 「跨项目查重」：按校验和聚合重复入库素材，双击定位文件所在目录

### 素材标签与备注（🏷）
- 「素材信息」可为单个素材设置自定义标签（逗号分隔）与备注，保存后立即生效
- 「素材检索」新增「标签」过滤条件，按关键字模糊筛选
- CSV 素材清单导出与项目归档/恢复均包含标签与备注字段

### 批量操作
- 「素材信息」素材表格支持多选（Ctrl/Shift），可批量设置评级（★ 可用 / ★★ 备选 / ★★★ 优选 / 未评级）
- 可批量从项目移除素材记录（仅删除数据库记录，不动磁盘文件）
- 素材列表会在后台检查文件是否仍存在，状态列显示「✓ 正常」或「⚠ 文件已丢失」；检查期间不会阻塞界面
- 「清理丢失素材」只删除数据库中的失效素材记录，不删除任何磁盘文件，执行前会再次后台复查并要求确认

### 可恢复性：回收站、重命名回退与素材重新链接
- 删除素材或项目时优先写入数据库回收站，源媒体文件不会被删除。
- 通过「设置 → 回收站…」查看未过期记录并恢复；若同 ID 已被重新使用，或素材所属项目不存在，系统会拒绝覆盖并说明原因。
- 素材路径失效时可在「素材信息」中选择新根目录，先查看相对路径、文件名与大小、可选校验和匹配形成的预览，再确认回写；重名候选不会自动覆盖。
- 批量重命名会保存映射记录；仅在目标文件状态仍可确认时允许安全回退。

### 大项目与后台任务
- 素材检索和素材信息列表均按 cursor 分页加载，默认每页 500 条，避免首次打开项目时创建全部表格行。
- 搜索和项目素材 CSV 导出采用流式后台任务，导出范围不受当前页限制；可从进度对话框取消。
- 素材统计报告在后台迭代统计，项目归档以临时 ZIP 写入并在成功后原子替换目标；取消或异常不会覆盖已有归档文件。
- 文件扫描器支持扩展名过滤、递归、取消和批量回调；备份校验、丢失文件检查和重新链接均使用迭代读取。

### 版本标识
- 应用标题、状态栏和「帮助 → 关于 DIT 工作站」显示版本号。
- 版本格式固定为 `alpha.YYYYMMDD`；PyInstaller 打包时同步写入 macOS 的显示版本，内部构建号使用纯日期以满足平台格式要求。

### 操作审计与设置
- 新增 `operation_logs` 操作审计日志：导入、备份、RAW 提取、重命名、评级、标签更新等关键操作自动留痕
- 「项目概览」底部展示最近 5 条操作记录
- 新增「设置」菜单（Ctrl+,）：缩略图缓存清理、最近路径清空、数据/报告目录查看、备份默认验证选项
- 设置项持久化到 `settings.json`：拷贝后默认验证、存储卡自动识别等配置重启后保留

### 数据存储位置重设 📂
- 「设置 → 数据存储位置」可分别重设数据库目录、报告输出目录、日志目录与缩略图缓存目录
- 数据库目录更改：自动关闭连接、把数据库文件（含 WAL/SHM）移动到新位置并持久化，提示重启应用生效
- 报告/日志/缩略图目录更改：立即生效并持久化，历史文件保留在原目录可手动迁移

### 临时文件清理 🧹
- 「设置 → 清理临时文件」可一键删除日志文件（含轮转文件，删除前显示数量与占用空间）
- 缩略图缓存清理整合到同一分组，删除后下次查看自动重新生成

### SOP 操作链优化
- 「项目概览」看板提供 4 张统计卡片 + 4 个快捷跳转按钮
- 根据项目进度动态生成「下一步」提示文案
- 导航索引采用单一事实源 `NAV_ITEMS`，避免按钮跳转到错误视图

### 存储卡自动识别 💾
- 轮询系统挂载点（macOS `/Volumes`、Windows 盘符）检测新插入的存储卡
- 识别标准：顶层含 `DCIM`/`AVCHD`/`PRIVATE` 等相机目录，或至少 3 个媒体文件
- 检测到后自动跳转到「媒体导入」并预填源目录、开始扫描（可在「设置」中关闭）
- **相机卡自动化**：可在「设置 → 存储卡」中为相机卡配置「自动导入素材 + 自动执行备份方案」（关联指定项目与备份方案），插卡即自动完成导入与备份

### 项目模板 🧩
- 「项目概览 → 项目管理」新增「从模板新建项目…」：选择模板 → 填名称/工作区 → 一键建项
- 「保存当前项目为模板…」：把当前项目的名称/描述/工作目录保存为模板，供后续复用
- 首次初始化自动预置「标准影视项目」模板；模板支持名称/描述/默认工作目录/备注

### 检索与标签增强 🔍
- 素材检索结果改为分页展示（默认每页 500 条，支持上一页/下一页），不再粗暴截断前 2000 条
- 标签入库到独立 `asset_tags` 关联表：检索按规范化标签子串匹配，并支持标签输入自动补全
- 关键词检索扩展覆盖素材备注字段；导出 CSV 输出全部匹配结果（不分页）
- **FTS5 全文搜索**：关键词检索优先走 SQLite FTS5 索引（覆盖文件名/场景/镜头/备注/标签），系统 SQLite 未编译 FTS5 时自动回退 LIKE 查询

### TaskViewModel 后台任务管理
- 新增 ViewModel 层：`TaskViewModel` 统一管理后台任务状态（运行中/完成/失败/已取消），替代视图内散落的 WorkerThread 接线
- 通过统一的 `state_changed` / `progress` / `finished` 信号向界面反馈，任务取消即时生效
- 备份、备份校验等长任务已接入该机制，后续视图可复用

### 数据库连接池
- 数据库服务改为线程级连接池：每个线程复用同一连接，避免高频操作反复建连并重复设置 PRAGMA
- 多线程备份 / 校验 / 检索并发访问时性能与稳定性更佳

### 备份方案模板 📦
- 「数据备份」视图可把当前备份目标、校验算法与验证选项保存为「备份方案」，下拉即可复用
- 支持编辑 / 删除方案；方案同时用于「相机卡自动化」的自动备份

### 数据库迁移版本化
- 迁移机制使用 `PRAGMA user_version` 版本化管理（v1 素材字段补齐；v2 标签回填与项目模板；v3 FTS5 索引版本；v4 备份方案模板；v5 回收站、重命名历史与审计字段；v6 任务历史与备份快照；v7 容量趋势快照；v8 拍摄日期时间线索引；v9 跨会话校验和缓存），升级路径明确、幂等可重复执行
- **迁移前自动备份**：升级前自动生成 `*.pre-migration.bak` 数据库备份，升级失败可回滚，数据不丢失

### 日志轮转
- 日志文件改为 `RotatingFileHandler`（单文件 5MB，保留最近 10 个），长时间运行不再无限膨胀

---

## 🧰 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.13 | 运行时环境 |
| PySide6 | 6.11.1 | Qt GUI 框架，构建原生桌面界面 |
| SQLite | 内置 | 本地数据库持久化（WAL 模式） |
| xxhash | 3.8.1 | 高性能文件校验和计算 |
| Pillow | 12.3.0 | 图像处理 |
| exifread | 3.5.1 | EXIF 元数据读取 |
| pymediainfo | 6.1.0 | 视频元数据读取 |
| reportlab | 5.0.0 | PDF 报告生成 |
| pyinstaller | — | 单文件应用打包 |
| pytest | 9.1.1 | 测试框架 |

> **正式媒体依赖**（构建时必须安装）：
> - `rawpy` — 相机 RAW 全量解码缩略图
> - `ffmpeg` — 视频抽帧缩略图，`brew install ffmpeg`（macOS）/ 官网安装（Windows）
> - `av`（PyAV）— 视频解码与抽帧方案
> - MediaInfo — 视频元数据读取；构建脚本会检查动态库并随产物分发

---

## 📁 项目结构

```
DIT_tools/
├── .venv/                          # Python 虚拟环境
├── requirements.txt                # 依赖列表
├── README.md                       # 本文件
├── data/                           # 开发模式数据库目录
│   └── dit_workstation.db          # SQLite 数据库
├── build/                          # 打包脚本与 PyInstaller 配置
│   ├── build_macos.sh              # macOS 打包脚本
│   ├── build_windows.bat           # Windows 打包脚本
│   ├── DITWorkstation.spec         # PyInstaller 配置
│   └── DITWorkstation.entitlements.plist  # macOS 签名权限
├── dist/                           # 打包产物（gitignore）
│   ├── DITWorkstation.app          # macOS 应用包（onedir，约 130MB）
│   └── DITWorkstation.dmg          # macOS 安装镜像
└── DITWorkstation/
    ├── DITWorkstation/             # 应用主包
    │   ├── __init__.py
    │   ├── main.py                 # 应用入口
    │   ├── App/
    │   │   ├── __init__.py         # 全局配置 AppConfig
    │   │   ├── version.py          # alpha.YYYYMMDD 版本标识
    │   │   └── session_context.py  # EventBus + 全局项目/工作区状态
    │   ├── Models/
    │   │   └── __init__.py         # 数据模型（dataclass + Enum + RATING_LABELS）
    │   ├── Services/               # 业务逻辑层
    │   │   ├── database_service.py     # 数据库服务（SQLite，14 张业务表 + 连接池 + FTS5）
    │   │   ├── checksum_service.py     # 校验和服务（内存 + 跨会话持久化缓存）
    │   │   ├── media_import_service.py # 媒体导入服务
    │   │   ├── metadata_service.py     # 元数据读取服务（EXIF/视频）
    │   │   ├── backup_service.py       # 备份服务（含快照持久化）
    │   │   ├── archive_service.py      # 项目归档/恢复（含安全防护）
    │   │   ├── card_automation_service.py # 相机卡自动化
    │   │   ├── raw_extraction_service.py # RAW 提取服务
    │   │   ├── rename_service.py       # 重命名服务
    │   │   ├── report_service.py       # 报告生成服务
    │   │   ├── thumbnail_service.py    # 缩略图服务
    │   │   └── volume_monitor.py       # 存储卡挂载点监控
    │   ├── Utils/
    │   │   ├── __init__.py
    │   │   ├── common.py               # 工具函数 + 单例 + safe_slot 装饰器
    │   │   ├── scanner.py              # 可取消的统一文件扫描器
    │   │   └── workers.py             # 后台线程（WorkerThread/SimpleWorkerThread）
    │   ├── ViewModels/
    │   │   ├── __init__.py             # ViewModel 导出
    │   │   └── task_view_model.py      # TaskViewModel 后台任务状态管理
    │   └── Views/                     # Qt UI 视图层
    │       ├── main_window.py          # 主窗口 + NAV_ITEMS 单一事实源
    │       ├── first_run_wizard.py     # 首启向导（5 页 QWizard）
    │       ├── project_dashboard_view.py # 项目概览看板
    │       ├── media_import_view.py    # 媒体导入视图
    │       ├── backup_view.py          # 数据备份视图
    │       ├── raw_extraction_view.py  # RAW 提取视图
    │       ├── rename_view.py          # 文件重命名视图
    │       ├── shooting_log_view.py    # 拍摄日志视图
    │       ├── search_view.py          # 素材检索视图
    │       ├── asset_info_view.py      # 素材信息视图
    │       ├── report_view.py          # 报告生成视图
    │       └── Widgets/
    │           ├── workspace_dialog.py        # 工作区新建/编辑对话框
    │           ├── workspace_project_selector.py # 工作区-项目共享选择控件
    │           ├── settings_dialog.py            # 设置对话框
    │           ├── recycle_bin_dialog.py         # 回收站查看与恢复对话框
    │           ├── project_template_dialog.py    # 项目模板对话框
    │           ├── backup_template_dialog.py     # 备份方案对话框
    │           ├── status_panel.py               # 进度/状态/日志面板
    │           ├── table_factory.py              # 表格工厂
    │           ├── empty_state.py                # 空状态占位
    │           └── task_history_dialog.py        # 后台任务历史中心
    ├── DITWorkstationTests/           # 测试套件（当前 402 个测试）
    │   ├── conftest.py                 # 共享 fixture
    │   ├── test_database.py            # 数据库服务测试（73）
    │   ├── test_media_import.py        # 媒体导入测试（30）
    │   ├── test_utils.py               # 工具函数测试（39）
    │   ├── test_backup.py              # 备份服务测试（16）
    │   ├── test_thumbnail.py           # 缩略图测试（12）
    │   ├── test_backup_resume.py       # 断点续传测试（12）
    │   ├── test_session_context.py     # 会话上下文测试（10）
    │   ├── test_settings.py            # 设置持久化测试（19）
    │   ├── test_archive.py             # 归档/恢复测试（12）
    │   ├── test_raw_extraction.py      # RAW 提取测试（9）
    │   ├── test_tags.py                # 标签测试（9）
    │   ├── test_models.py              # 数据模型测试（8）
    │   ├── test_rename.py             # 重命名测试（7）
    │   ├── test_checksum.py           # 校验和服务测试（15）
    │   ├── test_workers.py            # 后台线程测试（7）
    │   ├── test_ui_headless.py        # 无头 UI 与模式切换测试（31）
    │   └── test_feature_flags.py      # 功能模式开关测试（15）
    └── docs/
        └── 用户手册.md                 # 用户操作手册
```

---

## 🚀 快速开始

### 环境要求

- Python 3.11+（推荐 3.13）
- macOS / Windows（提供原生打包脚本）
- Linux 可从源码运行；当前未提供 Linux 原生打包脚本，也未纳入本地跨平台验证
- macOS 正式构建需要 MediaInfo（`brew install mediainfo`）
- Windows 正式构建需要 MediaInfo，或设置 `MEDIAINFO_DLL` 指向其 DLL
- PDF 报告优先使用系统中文字体；缺失时使用 ReportLab 内置 CJK CID 字体

### 安装与运行

```bash
# 1. 进入项目目录
cd /path/to/DIT_tools

# 2. 创建虚拟环境（如果还没有）
python3 -m venv .venv

# 3. 激活虚拟环境
source .venv/bin/activate        # macOS
# .venv\Scripts\activate         # Windows

# 4. 安装依赖
pip install -r requirements.txt

# 5. 运行应用
python DITWorkstation/main.py
```

> 应用入口位于 `DITWorkstation/DITWorkstation/main.py`。首次启动若数据库无项目，会自动弹出首启向导。

---

## 🧪 测试

测试覆盖核心服务业务逻辑、回收站恢复、跨平台路径重新链接、分页、后台任务、归档取消、工具函数、会话上下文、数据模型、缩略图、功能模式、项目健康、文件状态扫描、可中断扫描、Worker 状态协议、缩略图请求去重和无头 UI 交互。

```bash
# 激活虚拟环境后
cd DITWorkstation
pytest DITWorkstationTests/ -v
```

### 性能基准

阶段 0 的合成数据基准生成独立数据库，不会读取或修改应用数据。它覆盖 1 万、5 万、10 万素材记录的项目打开、分页检索、流式 CSV 导出和归档，并将结果写为 JSON 与 Markdown，便于在性能改动前后比较。

```bash
# 仓库根目录执行
python build/benchmark_database_workloads.py --assets 10000

# 完整三档基准（可选加入文件导入、校验、归档与取消延迟）
python build/benchmark_database_workloads.py --assets 10000,50000,100000 --file-workloads
```

### 测试覆盖

| 测试文件 | 覆盖范围 | 测试数量 |
|----------|----------|----------|
| `test_database.py` | 数据库服务（14 张业务表 CRUD + 版本化迁移 + FTS5 + 连接池 + 拍摄日期时间线） | 73 |
| `test_utils.py` | 工具函数（format_size / sanitize_filename 等） | 39 |
| `test_media_import.py` | 媒体导入服务 | 30 |
| `test_backup.py` | 备份服务 | 16 |
| `test_thumbnail.py` | 缩略图服务（缓存、生命周期与请求去重） | 12 |
| `test_checksum.py` | 校验和服务（含跨会话持久化缓存） | 15 |
| `test_backup_resume.py` | 备份断点续传 / 失败重试 | 12 |
| `test_session_context.py` | EventBus + 全局项目/工作区状态联动 | 10 |
| `test_settings.py` | 应用设置持久化 | 19 |
| `test_archive.py` | 项目归档 / 恢复（含安全防护） | 12 |
| `test_raw_extraction.py` | RAW 提取服务 | 9 |
| `test_tags.py` | 素材标签关联表与检索 | 9 |
| `test_models.py` | 数据模型（AssetRating / RATING_LABELS） | 8 |
| `test_rename.py` | 重命名服务 | 7 |
| `test_ui_headless.py` | 无头 UI：布局/分页/设置/模板入口/模式切换/文件状态扫描/拍摄时间线下钻/任务中心/启动路由 | 31 |
| `test_volume_monitor.py` | 存储卡识别 | 7 |
| `test_workers.py` | 后台线程 WorkerThread | 7 |
| `test_templates.py` | 项目模板 CRUD 与默认模板 | 6 |
| `test_workspace_selector.py` | 工作区-项目选择控件 | 6 |
| `test_report.py` | 报告生成服务（素材/备份/审计 PDF 与 CSV） | 9 |
| `test_task_view_model.py` | TaskViewModel 状态机与线程生命周期 | 12 |
| `test_backup_import_closure.py` | 备份回写导入联动 | 4 |
| `test_backup_templates.py` | 备份方案模板 | 2 |
| `test_card_automation.py` | 相机卡自动化与可配置 SOP 链 | 3 |
| `test_feature_flags.py` | 团队/个人模式功能开关 | 15 |
| `test_field_registry.py` | 数据库字段注册表与标识符白名单 | 7 |
| `test_project_health.py` | 项目健康汇总、容量快照与趋势预测 | 3 |
| `test_workspace_repository.py` | 工作区及仓储门面一致性 | 7 |
| `test_recovery.py` | 回收站恢复、重新链接与重命名回退 | 9 |
| `test_version_and_recycle_bin.py` | alpha 版本格式与回收站 UI 恢复入口 | 3 |
| **合计** | | **402** |

测试共享 fixture（`conftest.py`）提供隔离的数据库实例、临时目录与工厂函数，保证测试间互不干扰。

当前本地验证环境为 macOS arm64、Python 3.13、PySide6 6.11.1，执行结果为 `402 passed`。测试使用 Qt `offscreen` 平台，不能替代 Windows/macOS/Linux 原生 GUI、文件系统和打包产物验证。

### 性能参考

在 macOS arm64 / Python 3.13、10,000 条合成素材记录上，当前基准示例为：入库 `7,859.63 ms`、完整列表读取 `429.97 ms`、首屏检索 `23.07 ms`、深分页检索 `25.75 ms`、流式 CSV 导出 `546.28 ms`、仅元数据归档 `915.97 ms`。这些数字用于同机回归比较，不构成跨机器 SLA。

---

## 📦 打包分发

支持使用 PyInstaller 一键打包为桌面应用，方便分发给非技术用户。

### macOS 打包

```bash
cd /path/to/DIT_tools
bash build/build_macos.sh
```

- **产物**：`dist/DITWorkstation.app`（onedir 应用包，约 130MB，arm64 架构；依赖置于 `Contents/Frameworks`，启动更快）+ `dist/DITWorkstation.dmg`（脚本自动生成，约 57MB）
- **版本**：构建当天自动采用 `alpha.YYYYMMDD` 显示版本；内部构建号为同一天的纯数字。
- **要求**：macOS 主机、Python 3.11+、MediaInfo（`brew install mediainfo`；构建产物随包携带动态依赖）
- **签名**：adhoc 签名（Hardened Runtime），分发时需 Apple Developer ID 正式签名才能通过 Gatekeeper
- **数据目录**：`~/Library/Application Support/DITWorkstation/`

### Windows 打包

```bat
cd \path\to\DIT_tools
build\build_windows.bat
```

- **产物**：`dist\DITWorkstation.exe`（单文件模式）
- **版本**：应用窗口、状态栏和“关于”对话框显示构建当天的 `alpha.YYYYMMDD`。
- **要求**：Windows 主机、Python 3.11+（`python` 或 `py` 启动器均可）、MediaInfo（从官网下载，或设置 `MEDIAINFO_DLL`；打包态随包分发 DLL）
- **数据目录**：`%APPDATA%\DITWorkstation\`
- **长路径**：已内嵌 `longPathAware` manifest，支持超过 260 字符的路径
  （媒体卡深层目录 + 多级备份目录）；若个别机器仍报路径过长，请以管理员
  执行 `reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f`

> 详细打包说明参见 `build/` 目录下的脚本与 `build/DITWorkstation.spec` 配置。

> **跨平台说明**：PyInstaller 不支持交叉编译，需在目标平台上分别执行对应脚本原生构建
> （macOS 上运行 `build/build_macos.sh`，Windows 上运行 `build/build_windows.bat`）；
> Linux 当前仅支持源码运行，尚无专用打包脚本。

> **运行时依赖说明**：视频元数据读取依赖 MediaInfo 动态库；视频缩略图按顺序尝试
> `ffmpeg`、macOS QuickLook 和 PyAV。正式构建会安装 Python 媒体依赖并携带 MediaInfo；
> ffmpeg 未安装时仍使用其余解码方案。PDF 报告在没有系统中文字体时使用内置 CJK CID 字体。

---

## 🎨 支持的媒体格式

DIT 工作站支持以下专业摄影与影视制作常用格式：

| 类别 | 扩展名 |
|------|--------|
| **RAW** | `.cr2` `.cr3` `.nef` `.arw` `.dng` `.orf` `.rw2` `.raf` `.pef` `.srw` |
| **图片** | `.jpg` `.jpeg` `.png` `.tiff` `.tif` `.bmp` `.gif` `.webp` |
| **视频** | `.mp4` `.mov` `.mkv` `.avi` `.mxf` `.m4v` `.wmv` `.flv` `.webm` |
| **音频** | `.mp3` `.wav` `.aac` `.flac` `.m4a` `.wma` |

---

## 🏗 架构说明

DIT 工作站采用清晰的分层架构，职责分离、易于维护：

```
App（配置/会话）→ Models（数据模型）→ Services（业务逻辑）→ ViewModels（任务状态）→ Views（UI 视图）→ Utils（工具）
```

### 分层职责

- **App 层** — `AppConfig` 全局配置 + `session_context` 事件总线与全局状态
- **Models 层** — 基于 `dataclass` 与 `Enum` 的数据模型（含 `AssetRating` 枚举与 `RATING_LABELS` 单一事实源）
- **Services 层** — 业务逻辑实现，与 UI 解耦，可独立测试
- **ViewModels 层** — `TaskViewModel` 统一管理后台任务状态机与信号转发，视图只订阅状态
- **Views 层** — PySide6/Qt 视图组件，负责界面交互
- **Utils 层** — 通用工具、单例服务、`safe_slot` 装饰器、后台线程

### 关键设计

- **共享服务单例**：`get_db_service()` / `get_checksum_service()` 提供全局共享实例，避免重复初始化与缓存浪费
- **EventBus 跨视图通信**：`data_bus` 广播 `assets_changed` / `logs_changed` / `projects_changed` 等事件，实现视图间解耦联动
- **safe_slot 异常安全**：装饰 Qt 槽函数，捕获异常并弹出友好提示，避免槽函数崩溃
- **WorkerThread 后台线程**：基于 `QThread`，支持进度回调与取消，保证 UI 在长时间任务期间不卡顿
- **TaskViewModel 状态机**：后台任务统一状态（运行中/完成/失败/已取消），通过 `state_changed` 信号驱动界面，取消请求即时生效
- **SQLite FTS5 全文搜索**：关键词检索走 FTS5 索引（文件名/场景/镜头/备注/标签），无 FTS5 的 SQLite 自动回退 LIKE
- **线程级连接池**：每线程复用同一数据库连接，多线程任务下减少建连开销
- **WorkspaceProjectSelector 共享控件**：消除 9 个视图中重复的工作区/项目选择逻辑
- **NAV_ITEMS 单一事实源**：导航栏顺序集中定义，所有跳转按钮通过 `get_nav_index(key)` 查询索引，避免硬编码错位
- **SQLite WAL 模式**：保证并发读取性能与数据一致性

---

## 💾 数据存储

应用使用本地 SQLite 数据库存储所有项目数据，数据保留在用户本机。

### 存储路径

| 用途 | 开发模式 | 打包后（macOS） | 打包后（Windows） | 打包后（Linux） |
|------|----------|-----------------|-------------------|-----------------|
| 数据库文件 | `data/dit_workstation.db` | `~/Library/Application Support/DITWorkstation/` | `%APPDATA%\DITWorkstation\` | `~/.local/share/DITWorkstation/` |
| 日志目录 | `~/.dit_workstation/logs/` | 同左 | 同左 | 同左 |
| 报告输出 | `~/Documents/DIT_Reports/` | 同左 | 同左 | 同左 |

> 若数据库、设置或报告目录不可写（如 macOS TCC 权限拒绝），应用会尝试回退到 `~/.ditworkstation/`，最后回退到系统临时目录。
> 日志目录初始化失败时应用仍可运行并输出控制台日志；通过设置重新指定可写日志目录后，文件日志可以恢复。

### 数据库结构

- **模式**：SQLite，启用 WAL（Write-Ahead Logging）模式
- **表**：14 张业务表（另有 1 张可选的 FTS5 虚拟表）
  - `workspaces` — 工作区（工作区-项目两级结构的父级）
  - `projects` — 项目（归属于工作区）
  - `shooting_logs` — 拍摄日志
  - `media_assets` — 媒体素材（含 EXIF、评级、备份位置等字段）
  - `asset_tags` — 素材标签关联表
  - `backup_jobs` — 备份作业记录（含失败文件与任务快照）
  - `operation_logs` — 操作审计日志
  - `recycle_bin` — 项目/素材软删除快照及保留期
  - `rename_history` — 批量重命名映射及回退状态
  - `project_templates` — 项目模板
  - `backup_templates` — 备份方案模板
  - `task_history` — 后台任务历史与恢复上下文
  - `storage_health_snapshots` — 备份目标容量历史
  - `checksum_cache` — 跨会话校验和缓存（按路径、大小、修改时间和算法校验有效性）
- **索引**：21 个显式索引，覆盖常用查询字段、拍摄日期时间线聚合和校验和缓存淘汰以保证检索性能
- **迁移**：`PRAGMA user_version` 版本化迁移（v1–v9），幂等可重复执行；升级前自动生成 `*.pre-migration.bak` 备份，失败可回滚

---

## 📖 用户文档

详细操作指南请参阅用户手册：

- [`DITWorkstation/docs/用户手册.md`](DITWorkstation/docs/用户手册.md)

---

## 📄 许可证

本项目采用 [MIT License](https://opensource.org/licenses/MIT) 开源协议。

---

<p align="center">
  <sub>DIT 工作站 — 为专业影像数据管理而生</sub>
</p>
