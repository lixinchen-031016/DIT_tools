# 🎬 DIT 工作站

> 专业摄影数据管理桌面应用

DIT 工作站是一款面向影视与摄影行业的桌面端数据管理工具，为 DIT（数字影像工程师）提供从素材导入、备份、RAW 提取、重命名、拍摄日志到检索与报告的一站式工作流。基于 PySide6 构建原生桌面体验，使用 SQLite 进行本地数据持久化，支持高性能校验与跨平台 PDF 报告输出。

---

## 📑 目录

- [✨ 功能特性](#-功能特性)
- [🧰 技术栈](#-技术栈)
- [📁 项目结构](#-项目结构)
- [🚀 快速开始](#-快速开始)
- [🧪 测试](#-测试)
- [🎨 支持的媒体格式](#-支持的媒体格式)
- [🏗 架构说明](#-架构说明)
- [💾 数据存储](#-数据存储)
- [📖 用户文档](#-用户文档)
- [📄 许可证](#-许可证)

---

## ✨ 功能特性

DIT 工作站围绕 7 大核心模块构建完整的数据管理闭环：

| # | 模块 | 图标 | 说明 |
|---|------|------|------|
| 1 | 媒体导入 | 📁 | 将图片、视频、RAW 文件导入项目，支持引用模式（原文件不动）和复制模式（拷贝到工作区），自动读取 EXIF 元数据，可选关联拍摄日志，按路径查重防重复导入 |
| 2 | 数据备份 | 📦 | 从存储卡安全拷贝素材到多个目标，支持多目标并行备份，拷贝后校验和验证（XXHash64/MD5），生成 ASC MHL 2.0 格式哈希清单 |
| 3 | RAW 提取 | 🎞 | 根据筛选后的 JPG 文件，从 RAW 源文件夹中自动匹配并提取对应的 RAW 文件，按文件名 stem 匹配，提取后可校验验证 |
| 4 | 文件重命名 | ✏️ | 批量重命名文件，支持模板变量（`{scene}`/`{shot}`/`{take}`/`{original}`/`{number}`/`{prefix}`/`{suffix}`/`{date}`），预览确认后执行，自动防覆盖 |
| 5 | 拍摄日志 | 📋 | 管理场景/镜头/镜次拍摄记录，支持为媒体文件创建日志并关联，双向关联素材与日志，创建日志时同步 scene/shot 到素材 |
| 6 | 素材检索 | 🔍 | 按项目、场景、镜头、文件类型、关键词、拍摄日志、日期范围多维度组合检索，结果显示关联日志列 |
| 7 | 报告生成 | 📊 | 生成 PDF 格式的数据备份报告和素材统计报告，跨平台中文字体支持 |

---

## 🧰 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.13 | 运行时环境（虚拟环境 Python 3.13.9） |
| PySide6 | 6.11.1 | Qt GUI 框架，构建原生桌面界面 |
| SQLite | 内置 | 本地数据库持久化（WAL 模式） |
| xxhash | 3.8.1 | 高性能文件校验和计算 |
| Pillow | 12.3.0 | EXIF 元数据读取 |
| reportlab | 5.0.0 | PDF 报告生成 |
| pytest | 9.1.1 | 测试框架 |

---

## 📁 项目结构

```
DIT_tools/
├── .venv/                          # Python 虚拟环境
├── requirements.txt                # 依赖列表
├── README.md                       # 本文件
└── DITWorkstation/
    ├── DITWorkstation/             # 应用主包
    │   ├── __init__.py
    │   ├── main.py                 # 应用入口
    │   ├── App/
    │   │   └── __init__.py         # 全局配置 AppConfig
    │   ├── Models/
    │   │   └── __init__.py         # 数据模型（dataclass + Enum）
    │   ├── Services/               # 业务逻辑层
    │   │   ├── __init__.py
    │   │   ├── backup_service.py       # 备份服务
    │   │   ├── checksum_service.py     # 校验和服务
    │   │   ├── database_service.py     # 数据库服务（SQLite）
    │   │   ├── media_import_service.py # 媒体导入服务
    │   │   ├── raw_extraction_service.py # RAW 提取服务
    │   │   ├── rename_service.py       # 重命名+元数据服务
    │   │   └── report_service.py       # 报告生成服务
    │   ├── Utils/
    │   │   ├── __init__.py
    │   │   ├── common.py               # 通用工具函数+日志器
    │   │   └── workers.py             # 后台线程（WorkerThread/SimpleWorkerThread）
    │   ├── ViewModels/
    │   │   └── __init__.py
    │   └── Views/                     # Qt UI 视图层
    │       ├── __init__.py
    │       ├── main_window.py          # 主窗口与导航
    │       ├── media_import_view.py    # 媒体导入视图
    │       ├── backup_view.py          # 数据备份视图
    │       ├── raw_extraction_view.py  # RAW 提取视图
    │       ├── rename_view.py          # 文件重命名视图
    │       ├── shooting_log_view.py    # 拍摄日志视图
    │       ├── search_view.py          # 素材检索视图
    │       └── report_view.py          # 报告生成视图
    ├── DITWorkstationTests/           # 测试套件（77 个测试）
    │   ├── __init__.py
    │   ├── test_backup.py              # 备份服务测试（7）
    │   ├── test_checksum.py            # 校验和服务测试（7）
    │   ├── test_database.py            # 数据库服务测试（24）
    │   ├── test_media_import.py        # 媒体导入测试（25）
    │   ├── test_raw_extraction.py      # RAW 提取测试（7）
    │   └── test_rename.py             # 重命名测试（7）
    └── docs/
        └── 用户手册.md                 # 用户操作手册
```

---

## 🚀 快速开始

### 环境要求

- Python 3.13+
- macOS / Windows / Linux

### 安装与运行

```bash
# 1. 进入项目目录
cd /path/to/DIT_tools

# 2. 创建虚拟环境（如果还没有）
python3 -m venv .venv

# 3. 激活虚拟环境
source .venv/bin/activate

# 4. 安装依赖
pip install -r requirements.txt

# 5. 运行应用
python DITWorkstation/main.py
```

> 应用入口位于 `DITWorkstation/DITWorkstation/main.py`。

---

## 🧪 测试

项目包含 77 个单元测试，覆盖全部 6 个核心服务的业务逻辑。

```bash
# 激活虚拟环境后
cd DITWorkstation
source ../.venv/bin/activate
pytest DITWorkstationTests/ -v
```

### 测试覆盖

| 测试文件 | 覆盖服务 | 测试数量 |
|----------|----------|----------|
| `test_backup.py` | 备份服务 | 7 |
| `test_checksum.py` | 校验和服务 | 7 |
| `test_database.py` | 数据库服务 | 24 |
| `test_media_import.py` | 媒体导入服务 | 25 |
| `test_raw_extraction.py` | RAW 提取服务 | 7 |
| `test_rename.py` | 重命名服务 | 7 |
| **合计** | | **77** |

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
App（配置）→ Models（数据模型）→ Services（业务逻辑）→ Views（UI 视图）→ Utils（工具）
```

### 分层职责

- **App 层** — `AppConfig` dataclass 全局单例，集中管理应用配置
- **Models 层** — 基于 `dataclass` 与 `Enum` 的数据模型定义
- **Services 层** — 业务逻辑实现，与 UI 解耦，可独立测试
- **Views 层** — PySide6/Qt 视图组件，负责界面交互
- **Utils 层** — 通用工具函数与日志器

### 关键设计

- **后台任务**：基于 `QThread` 的 `WorkerThread` / `SimpleWorkerThread`，支持进度回调与取消，保证 UI 在长时间任务（备份、校验、导入）期间不卡顿
- **配置中心**：`AppConfig` dataclass 全局单例，统一管理运行时配置
- **数据持久化**：SQLite + WAL 模式，保证并发读取性能与数据一致性

---

## 💾 数据存储

应用使用本地 SQLite 数据库存储项目数据，所有数据保留在用户本机。

### 存储路径

| 用途 | 路径 |
|------|------|
| 数据库文件 | DIT_tools/data/dit_workstation.db |
| 日志目录 | `~/.dit_workstation/logs/` |
| 报告输出目录 | `~/Documents/DIT_Reports/` |

### 数据库结构

- **模式**：SQLite，启用 WAL（Write-Ahead Logging）模式
- **表**：3 张
  - `projects` — 项目信息
  - `shooting_logs` — 拍摄日志
  - `media_assets` — 媒体素材
- **索引**：8 个，覆盖常用查询字段以保证检索性能

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
