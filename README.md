# 🎬 DIT 工作站

> 专业摄影数据管理桌面应用 — 从素材导入到报告生成的一站式 DIT 工作流

DIT 工作站是一款面向影视与摄影行业的桌面端数据管理工具，为 DIT（数字影像工程师）提供从素材导入、备份、RAW 提取、重命名、拍摄日志、素材信息、检索到报告生成的完整工作流。基于 PySide6 构建原生桌面体验，使用 SQLite 进行本地数据持久化，支持高性能校验、跨平台 PDF 报告输出与单文件应用打包分发。

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
| 1 | 项目概览 | 🏠 | 当前项目进度看板，聚合展示导入/备份/日志/报告进度，提供 SOP「下一步」快捷跳转；支持项目归档/恢复（zip 打包，可选附带素材文件） |
| 2 | 媒体导入 | 📁 | 将图片/视频/RAW 导入项目，支持引用模式与复制模式，自动读取 EXIF，可选关联拍摄日志，按路径查重；扫描后可勾选要导入的文件（选中行显示缩略图预览），支持全选/反选 |
| 3 | 数据备份 | 📦 | 从存储卡多目标并行备份，校验和验证（XXHash64/MD5），支持导出 ASC MHL 校验清单，完成后回写素材的备份位置；备份前自动预检目标磁盘空间，支持对已有备份做独立完整性再校验 |
| 4 | RAW 提取 | 🎞 | 根据筛选后的 JPG 文件，从 RAW 源文件夹自动匹配并提取对应 RAW，提取后可自动入库 |
| 5 | 文件重命名 | ✏️ | 按场景/镜头/镜次规则批量重命名，支持模板变量，预览确认后执行，自动防覆盖 |
| 6 | 拍摄日志 | 📋 | 管理场景/镜头/镜次拍摄记录，支持「从代表素材填充 EXIF」，双向关联素材与日志 |
| 7 | 素材检索 | 🔍 | 按项目/场景/镜头/类型/关键词/日志/评级/日期/标签多维度组合检索（上限 2000 条），结果可导出 CSV；支持按校验和跨项目查重 |
| 8 | 素材信息 | ℹ️ | 查看素材 EXIF 与元数据详情，支持缩略图预览、单个/批量重新读取 EXIF，可设置镜次评级，可一键导出项目素材清单 CSV；支持标签/备注编辑与批量评级、批量删除 |
| 9 | 报告生成 | 📊 | 生成 PDF 数据备份报告与素材统计报告，含镜次评级分布统计，跨平台中文字体支持 |

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
- 5 步引导：欢迎 → 创建工作区 → 创建项目 → SOP 操作链提示 → 完成
- 完成后自动跳转到「媒体导入」视图

### 项目管理（归档 / 恢复）
- 「项目概览」提供「归档当前项目…」：把项目信息 + 拍摄日志 + 素材元数据打包为 zip，可选附带素材文件副本
- 归档包内含 manifest.json / assets.json / logs.json / checksums.txt，自包含、可交接
- 「恢复项目…」：从归档包重建项目到当前工作区，可选还原素材文件并自动校验校验和；与现有项目重名时自动追加时间戳后缀

### 备份健壮性
- 备份前对每个目标执行磁盘空间预检，剩余空间不足时拒绝启动并提示
- 「校验已有备份」：按素材的 backup_locations 对备份盘做独立完整性校验（存在性 + 校验和比对），检测位错误/文件丢失
- 「素材检索」结果可导出 CSV 素材清单（utf-8-sig，Excel 直接打开）
- 「跨项目查重」：按校验和聚合重复入库素材，双击定位文件所在目录

### 素材标签与备注（🏷）
- 「素材信息」可为单个素材设置自定义标签（逗号分隔）与备注，保存后立即生效
- 「素材检索」新增「标签」过滤条件，按关键字模糊筛选
- CSV 素材清单导出与项目归档/恢复均包含标签与备注字段

### 批量操作
- 「素材信息」素材表格支持多选（Ctrl/Shift），可批量设置评级（★ 可用 / ★★ 备选 / ★★★ 优选 / 未评级）
- 可批量从项目移除素材记录（仅删除数据库记录，不动磁盘文件）

### 操作审计与设置
- 新增 `operation_logs` 操作审计日志：导入、备份、RAW 提取、重命名、评级、标签更新等关键操作自动留痕
- 「项目概览」底部展示最近 5 条操作记录
- 新增「设置」菜单（Ctrl+,）：缩略图缓存清理、最近路径清空、数据/报告目录查看、备份默认验证选项

### SOP 操作链优化
- 「项目概览」看板提供 4 张统计卡片 + 4 个快捷跳转按钮
- 根据项目进度动态生成「下一步」提示文案
- 导航索引采用单一事实源 `NAV_ITEMS`，避免按钮跳转到错误视图

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

> **可选依赖**（未安装时自动降级，不影响核心功能）：
> - `rawpy` — 相机 RAW 全量解码缩略图（替代仅 EXIF 内嵌预览），`pip install rawpy`
> - `ffmpeg` — 视频抽帧缩略图，`brew install ffmpeg`（macOS）/ 官网安装（Windows）
> - `av`（PyAV）/ `opencv-python-headless` — 无 ffmpeg 时的备选视频抽帧方案
> - macOS 无以上依赖时，应用自动使用系统 QuickLook（qlmanage）生成视频缩略图

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
│   ├── DITWorkstation.app          # macOS 应用包（约 45MB）
│   └── DITWorkstation.dmg          # macOS 安装镜像
└── DITWorkstation/
    ├── DITWorkstation/             # 应用主包
    │   ├── __init__.py
    │   ├── main.py                 # 应用入口
    │   ├── App/
    │   │   ├── __init__.py         # 全局配置 AppConfig
    │   │   └── session_context.py  # EventBus + 全局项目/工作区状态
    │   ├── Models/
    │   │   └── __init__.py         # 数据模型（dataclass + Enum + RATING_LABELS）
    │   ├── Services/               # 业务逻辑层
    │   │   ├── database_service.py     # 数据库服务（SQLite，5 张表）
    │   │   ├── checksum_service.py     # 校验和服务（带缓存）
    │   │   ├── media_import_service.py # 媒体导入服务
    │   │   ├── metadata_service.py     # 元数据读取服务（EXIF/视频）
    │   │   ├── backup_service.py       # 备份服务
    │   │   ├── raw_extraction_service.py # RAW 提取服务
    │   │   ├── rename_service.py       # 重命名服务
    │   │   └── report_service.py       # 报告生成服务
    │   ├── Utils/
    │   │   ├── __init__.py
    │   │   ├── common.py               # 工具函数 + 单例 + safe_slot 装饰器
    │   │   └── workers.py             # 后台线程（WorkerThread/SimpleWorkerThread）
    │   ├── ViewModels/
    │   │   └── __init__.py             # 预留层
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
    │           └── workspace_project_selector.py # 工作区-项目共享选择控件
    ├── DITWorkstationTests/           # 测试套件（134 个测试）
    │   ├── conftest.py                 # 共享 fixture
    │   ├── test_database.py            # 数据库服务测试（47）
    │   ├── test_media_import.py        # 媒体导入测试（25）
    │   ├── test_utils.py               # 工具函数测试（16）
    │   ├── test_session_context.py     # 会话上下文测试（10）
    │   ├── test_models.py              # 数据模型测试（8）
    │   ├── test_backup.py              # 备份服务测试（7）
    │   ├── test_raw_extraction.py      # RAW 提取测试（7）
    │   ├── test_rename.py             # 重命名测试（7）
    │   └── test_checksum.py           # 校验和服务测试（7）
    └── docs/
        └── 用户手册.md                 # 用户操作手册
```

---

## 🚀 快速开始

### 环境要求

- Python 3.13+
- macOS / Windows / Linux
- macOS 视频元数据读取需额外安装 MediaInfo（`brew install mediainfo`）
- Windows 视频元数据读取需从 https://mediaarea.net/en/MediaInfo 下载安装 MediaInfo

### 安装与运行

```bash
# 1. 进入项目目录
cd /path/to/DIT_tools

# 2. 创建虚拟环境（如果还没有）
python3 -m venv .venv

# 3. 激活虚拟环境
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# 4. 安装依赖
pip install -r requirements.txt

# 5. 运行应用
python DITWorkstation/main.py
```

> 应用入口位于 `DITWorkstation/DITWorkstation/main.py`。首次启动若数据库无项目，会自动弹出首启向导。

---

## 🧪 测试

项目包含 134 个单元测试，覆盖全部核心服务的业务逻辑、工具函数、会话上下文与数据模型。

```bash
# 激活虚拟环境后
cd DITWorkstation
pytest DITWorkstationTests/ -v
```

### 测试覆盖

| 测试文件 | 覆盖范围 | 测试数量 |
|----------|----------|----------|
| `test_database.py` | 数据库服务（5 张表 CRUD + 迁移） | 47 |
| `test_media_import.py` | 媒体导入服务 | 25 |
| `test_utils.py` | 工具函数（format_size / sanitize_filename 等） | 16 |
| `test_session_context.py` | EventBus + 全局项目/工作区状态联动 | 10 |
| `test_models.py` | 数据模型（AssetRating / RATING_LABELS） | 8 |
| `test_backup.py` | 备份服务 | 7 |
| `test_raw_extraction.py` | RAW 提取服务 | 7 |
| `test_rename.py` | 重命名服务 | 7 |
| `test_checksum.py` | 校验和服务 | 7 |
| **合计** | | **134** |

测试共享 fixture（`conftest.py`）提供隔离的数据库实例、临时目录与工厂函数，保证测试间互不干扰。

---

## 📦 打包分发

支持使用 PyInstaller 打包为单文件桌面应用，方便分发给非技术用户。

### macOS 打包

```bash
cd /path/to/DIT_tools
bash build/build_macos.sh
```

- **产物**：`dist/DITWorkstation.app`（单文件应用包，约 45MB，arm64 架构）+ `dist/DITWorkstation.dmg`
- **要求**：macOS 主机、Python 3.11+、MediaInfo（`brew install mediainfo`）
- **签名**：adhoc 签名（Hardened Runtime），分发时需 Apple Developer ID 正式签名才能通过 Gatekeeper
- **数据目录**：`~/Library/Application Support/DITWorkstation/`

### Windows 打包

```bat
cd \path\to\DIT_tools
build\build_windows.bat
```

- **产物**：`dist/DITWorkstation\DITWorkstation.exe`
- **要求**：Windows 主机、Python 3.11+ 加入 PATH、MediaInfo（从官网下载）
- **数据目录**：`%APPDATA%\DITWorkstation\`

> 详细打包说明参见 `build/` 目录下的脚本与 `build/DITWorkstation.spec` 配置。

> **跨平台说明**：PyInstaller 不支持交叉编译，需在目标平台上分别执行对应脚本原生构建
> （macOS 上运行 `build/build_macos.sh`，Windows 上运行 `build/build_windows.bat`）；
> 可选依赖 `rawpy` 由脚本单独宽容安装，无预编译 wheel 的平台会自动降级，不影响打包。

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
App（配置/会话）→ Models（数据模型）→ Services（业务逻辑）→ Views（UI 视图）→ Utils（工具）
```

### 分层职责

- **App 层** — `AppConfig` 全局配置 + `session_context` 事件总线与全局状态
- **Models 层** — 基于 `dataclass` 与 `Enum` 的数据模型（含 `AssetRating` 枚举与 `RATING_LABELS` 单一事实源）
- **Services 层** — 业务逻辑实现，与 UI 解耦，可独立测试
- **Views 层** — PySide6/Qt 视图组件，负责界面交互
- **Utils 层** — 通用工具、单例服务、`safe_slot` 装饰器、后台线程

### 关键设计

- **共享服务单例**：`get_db_service()` / `get_checksum_service()` 提供全局共享实例，避免重复初始化与缓存浪费
- **EventBus 跨视图通信**：`data_bus` 广播 `assets_changed` / `logs_changed` / `projects_changed` 等事件，实现视图间解耦联动
- **safe_slot 异常安全**：装饰 Qt 槽函数，捕获异常并弹出友好提示，避免槽函数崩溃
- **WorkerThread 后台线程**：基于 `QThread`，支持进度回调与取消，保证 UI 在长时间任务期间不卡顿
- **WorkspaceProjectSelector 共享控件**：消除 9 个视图中重复的工作区/项目选择逻辑
- **NAV_ITEMS 单一事实源**：导航栏顺序集中定义，所有跳转按钮通过 `get_nav_index(key)` 查询索引，避免硬编码错位
- **SQLite WAL 模式**：保证并发读取性能与数据一致性

---

## 💾 数据存储

应用使用本地 SQLite 数据库存储所有项目数据，数据保留在用户本机。

### 存储路径

| 用途 | 开发模式 | 打包后（macOS） | 打包后（Windows） |
|------|----------|-----------------|-------------------|
| 数据库文件 | `data/dit_workstation.db` | `~/Library/Application Support/DITWorkstation/` | `%APPDATA%\DITWorkstation\` |
| 日志目录 | `~/.dit_workstation/logs/` | 同左 | 同左 |
| 报告输出 | `~/Documents/DIT_Reports/` | 同左 | 同左 |

> 若默认数据目录不可写（如 macOS TCC 权限拒绝），自动回退到 `~/.ditworkstation/`。

### 数据库结构

- **模式**：SQLite，启用 WAL（Write-Ahead Logging）模式
- **表**：5 张
  - `workspaces` — 工作区（工作区-项目两级结构的父级）
  - `projects` — 项目（归属于工作区）
  - `shooting_logs` — 拍摄日志
  - `media_assets` — 媒体素材（含 EXIF、评级、备份位置等字段）
  - `backup_jobs` — 备份作业记录
- **索引**：9 个，覆盖常用查询字段以保证检索性能
- **迁移**：`_migrate_db()` 自动为旧表补齐新列，幂等可重复执行

---

## 📖 用户文档

详细操作指南请参阅用户手册：

- [`DITWorkstation/docs/用户手册.md`](DITWorkstation/docs/用户手册.md)

---

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

---

<p align="center">
  <sub>DIT 工作站 — 为专业影像数据管理而生</sub>
</p>
