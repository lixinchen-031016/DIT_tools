# 变更记录

## 2026-09-12（新增「极简模式」界面模式）

- 功能模式体系扩展为三态：`App/feature_flags.py` 新增 `UsageMode.MINIMAL`、`MINIMAL_NAV_KEYS`（仅 `import`）、`is_minimal_mode()`；`get_active_nav_items()` 与 `is_enabled()` 改为三态裁决。
- 极简模式采用**特性白名单**（`_MINIMAL_ENABLED_FEATURES`）：未列出的特性（含未来新增特性）一律关闭，保证「仅保留媒体导入」的契约不被后续改动破坏；缺失/非法 `usage_mode` 仍回退团队模式。
- 新增自定义保存路径：`AppConfig.minimal_import_target_dir` 持久化于 `settings.json` 的 `app_config.minimal_import_target_dir`；新增 `get_minimal_import_target_dir()` / `set_minimal_import_target_dir()`（校验可写并按需创建目录）/ `resolve_minimal_import_target_dir()`（解析为 `<保存目录>/<项目名>/`）。
- `Views/main_window.py`：视图改为按「激活导航项」按需实例化——极简模式仅创建媒体导入视图，其余视图属性统一置为 `None`；备份恢复、完整性校验、回收站等依赖备份模块的菜单项隐藏；`Ctrl+F/Ctrl+B/Ctrl+L` 仅在对应页面激活时注册；相机卡自动化、完整性调度、多卡队列等后台服务在无依赖视图时安全跳过。关闭窗口的 worker 检查与状态栏逻辑保持 `None` 安全。
- `Views/Widgets/settings_dialog.py`：「使用场景」新增「极简模式（仅媒体导入）」选项，并在同分组内提供「媒体保存目录」设置项（选择/打开/清除，仅极简模式可见）；极简模式下隐藏备份默认选项与完整性校验分组。
- `Views/media_import_view.py`：极简模式下「复制到工作区」切换为「复制到保存目录」并默认勾选，新增「保存位置」行（选择/打开）；未设置目录时导入即时提示选择，取消则放弃导入；复制目标为 `<保存目录>/<项目名>/`；「去数据备份」入口仅在备份可用时提供。
- `Views/first_run_wizard.py`：极简模式使用精简欢迎文案与完成文案（仅提示导入链路）；非团队模式的默认工作区补齐逻辑调整为「个人模式绑定路径、极简模式仅确保工作区存在」。
- 文档同步：README「使用场景」「首启向导」新增极简模式说明；用户手册新增极简模式使用流程、导入选项说明与 FAQ（Q12.1）；变更记录随本次更新。

测试基线：`pytest DITWorkstationTests/ -q`，`464 passed`（新增极简模式相关用例 32 项：`test_minimal_mode.py` 7 项、`test_feature_flags.py` 15 项、`test_settings.py` 2 项、`test_settings_dialog.py` 4 项、`test_ui_headless.py` 5 项）；`ruff check` / `ruff format --check` 全通过。

## 2026-08-22（功能缺陷修复、文档同步与命令面板优化）

- 修复深色主题失效：`main.py` 在导入视图模块前应用主题，确保 13 个视图文件的 QSS 常量捕获正确调色板。
- 修复完整性校验调度器无法启动（`create_backup_service` 不存在 → `backup_service`）。
- 修复恢复向导进度回调线程安全问题（`_ProgressBridge` 信号桥接，UI 更新始终在主线程）。
- 修复更新检查阻塞 UI 线程（启动静默检查 + 设置对话框手动检查均改为 WorkerThread）。
- 修复保存搜索 `update()` 的 `workspace_id`/`project_id` 更新缺失。
- 修复完整性调度首次启动立即执行（改为等一个间隔周期）。
- 修复命令面板几何尺寸错位（改用布局）、个人模式导航项过滤。
- 修复 `saved_search_limit` 配置未使用、备份回拷路径穿越风险。
- 清理未使用导入/变量（F401/F841）、XMP 写入死代码、视频跳过检查。
- 命令面板优化为类似 Windows 运行对话框：紧凑尺寸、输入路径/URL 直接回车运行、快速命令入口。
- 更新用户手册、README 和快捷键大全，补充新功能章节与 FAQ。

测试基线：`pytest DITWorkstationTests/test_new_features.py DITWorkstationTests/test_settings_dialog.py -q`，`35 passed`。


## 2026-08-21（线程、UI 解耦与文档同步）

- 统一 `WorkerThread` 与 `SimpleWorkerThread` 状态机、取消语义和终态保护。
- 移除 `MainWindow` 对子视图私有刷新方法的直接调用，改由视图订阅 EventBus。
- 增加服务关闭生命周期、缩略图请求去重和可中断文件扫描。
- 补充 UI 对话框、首启向导和 `main.py` 启动路径的 offscreen 测试。
- 10,000 条合成素材批量入库基准从约 8.85s 降至约 7.86s。
- 同步 README 与用户手册中的版本格式、平台限制、测试清单和任务中心说明。

测试基线：`pytest DITWorkstationTests/ -q`，`402 passed`。

## 2026-08-18（完全未实现项收尾）

- 完成 F-1：校验和缓存增加 SQLite 跨会话持久化，按文件路径、大小、纳秒级修改时间和算法校验缓存有效性，并提供容量限制与统一清理接口。
- 完成 F-2：新增后台任务历史中心，支持项目/状态筛选、任务参数与错误详情查看，以及带恢复上下文的失败备份重试。
- 修复共享数据库与校验和服务初始化时的单例锁重入死锁。
- 增加跨会话缓存失效和任务中心无头 UI 回归测试。

测试基线：`pytest DITWorkstationTests -q`，`394 passed`。

## 2026-08-18（第三轮优化）

- 完成 O-12：拆分 EXIF、视频元数据和文件扫描器的高复杂度函数，保持可选依赖缺失和取消路径的降级行为。
- 完成 O-13：使用 Ruff 对应用源代码执行自动规范修复，完成 631 项导入、类型注解和安全语法清理；剩余诊断保留给异常边界、时区和 SQLite 兼容代码的后续人工审查。
- 完成 O-14：拆分主窗口和备份视图的巨型 `_setup_ui`，按导航、视图栈、快捷键、状态栏、备份配置和结果面板组织 UI 构建逻辑。
- 完成 F-5：素材检索增加按拍摄日期聚合的时间线模式，支持点击日期下钻到当天素材列表，并为 `date_taken` 增加数据库索引。
- 完成 F-6：操作日志查看器增加审计 PDF 导出，包含总量、成功/异常、按日/事件统计和明细；原有 CSV 导出保留。
- 修正拍摄日期筛选的 ISO 时间边界规范化，避免 `T` 与空格分隔符导致当天记录漏查。

测试基线：`pytest DITWorkstationTests -q`，`392 passed`。

## 2026-08-18

- 完成第二轮性能与可靠性治理：校验和缓存限容、批量素材入库、操作日志和任务历史清理、时区感知时间戳、字段注册表和备份快照查找优化。
- 项目健康看板增加容量趋势与低容量预警。
- 相机卡自动化支持可配置的导入、备份、RAW 提取、重命名和报告步骤链。
- README 的测试清单、测试数量、数据库表数量、索引数量和迁移版本与代码同步。

测试基线：`pytest DITWorkstationTests -q`，`389 passed`。
