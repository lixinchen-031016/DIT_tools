# DIT 工作站代码库静态与动态分析报告

清晰的分层架构，职责分离总体到位：

```
App(配置/会话/EVENT总线) → Models(数据模型) → Services(业务逻辑)
   → ViewModels(TaskViewModel 任务状态) → Views(Qt UI) → Utils(工具/单例/safe_slot)
```

- **App 层**：`AppConfig` 全局配置 + `session_context` 事件总线（`data_bus`）+ 全局当前工作区/项目状态。
- **Services 层**（核心，解耦于 UI）：`DatabaseService`（2100 行，9 表 + 连接池 + FTS5 + 版本化迁移）、`BackupService`（791 行，并行多目标备份 + 断点续传 + 快照持久化）、`MediaImportService`、`ChecksumService`（带缓存）、`RawExtractionService`、`ArchiveService`（归档/恢复，含路径穿越防护）、`ThumbnailService`、`ReportService`、`VolumeMonitor`、`CardAutomationService`。
- **ViewModels 层**：`TaskViewModel` 统一管理后台任务状态机，视图只订阅信号。
- **Views 层**：9 个功能视图 + 共享控件（`WorkspaceProjectSelector`、`table_factory`、`settings_dialog` 等）。

**亮点（已做得好的部分）**：① 线程级 SQLite 连接池 + `busy_timeout=5000` 防 `SQLITE_BUSY`；② 归档解压做了路径穿越 / zip-bomb / 符号链接 / 先写临时再校验的多重防护；③ 所有 SQL 值均参数化（`?`），标识符拼接来源受控；④ 备份"边拷贝边算校验和"减少一次读盘；⑤ 迁移前自动 `*.pre-migration.bak` 备份；⑥ `safe_slot` 异常安全装饰器 + 友好文案。

---

## 三、按问题域的关键发现

### 3.1 性能瓶颈
1. **`get_media_assets(project_id)` 无分页**（`database_service.py:1249`）：`SELECT *` 全量加载并转为对象列表。一天拍摄常产生数千~数万条 RAW+JPG 记录，若视图/导出调用此方法会一次性占满内存。
2. **`search_assets` 用 `list()` 物化**（`database_service.py:1679`）：虽底层 `iter_search_assets` 已用 `fetchmany` 游标，但 UI 之外的调用方（如 CSV 导出）若走 `search_assets` 会全量展开。
3. **备份重试重扫全源**（`backup_service.py:425` `retry_failed_files` 调用 `scan_source`）：重扫整个源目录 stat 所有文件，大卡上代价高；源树未变动时属于冗余 I/O。
4. **`verify_backup` 全量重算目标哈希**（`backup_service.py:137-222`）：对每个备份位置重读并全量哈希比对。逻辑正确但 O(n) 磁盘读，大归档下耗时显著（可考虑复用已存 `asset.checksum_value` + 按需抽样）。
5. **文件扫描循环重复且未并行**：四处服务各自 `rglob` + 扩展名过滤（见 3.2），大目录扫描未利用线程池。

### 3.2 代码重复（技术债最集中处）
1. **`calculate_speed` 完全重复**：`Utils/common.py:167-171` 与 `Services/backup_service.py:787-791` 字节级相同。
2. **文件扫描 `rglob`+扩展名循环 ×4**：`media_import_service.py:98-106`、`raw_extraction_service.py:37-39 & 58-63`、`backup_service.py:56-64`，累计 30+ 行可抽公共扫描器。
3. **线程池备份循环 ×2**：`backup_service.py:308-337`（首备）与 `431-475`（重试）结构几乎逐行相同（submit→as_completed→future.result()→异常置 FAILED→shutdown）。
4. **UPDATE 样板 ×5+**：`update_workspace:663`、`update_project:809`、`update_project_template:901`、`update_backup_template:1001`、`update_media_asset:1265` 均为"遍历 kwargs→白名单→`SET col=?`→WHERE id"。
5. **`extract_raw_files` 与 `extract_raw_files_streaming` ≈90% 重复**（`raw_extraction_service.py:93-201` vs `203-292`）。
6. **`scan_jpg_folder` / `scan_raw_folder`** 重复的 `rglob(f"*{ext}")` + `rglob(f"*{ext.upper()}")` 大小写两遍模式。
7. **`normalize_path` 查询键规范化** 在 `database_service.py` 多处重复前置（~5 处）。

### 3.3 安全弱点
- **SQL 注入：低风险**。所有值参数化；`f-string` 仅拼接受控标识符（迁移字典、白名单字段）。**唯一隐患**：UPDATE 拼装依赖"白名单纪律"，新增字段若忘记加白名单即成注入点（无类型化字段注册表兜底）。
- **归档解压：防护良好**（路径穿越/zip-bomb/符号链接/临时目录先校验）—— 已正确加固，属正面结论。
- **subprocess**：全部列表参数、无 `shell=True`；`explorer /select,{target}` 为单参拼接，风险低。
- **无** `eval/exec/pickle/yaml.load(不Safe)/verify=False`/硬编码密钥。
- **MD5 用于完整性校验**（checksum_service / media_import）：作防篡改偏弱，但当前仅用于完整性/缓存 key，可接受，仍建议迁移到以 XXHash64 为默认、MD5 仅兼容。

### 3.4 可维护性隐患
1. **过度宽泛的 `except Exception` 吞噬**：全仓约 130+ 处。最严重：
   - `common.py:187-196` `_load_settings` 配置损坏时静默 `return {}` —— **用户设置静默丢失且无任何提示**。
   - `database_service.py` 各 `update_*` 失败仅 `return False` + 日志，调用方**无法区分"记录不存在"与"DB 错误"**，难以做精准 UI 提示。
2. **巨型模块 / 上帝类**：`database_service.py` 2100 行（19 处 except）、`asset_info_view.py` 1391 行（11 处）、`backup_view.py` 986、`media_import_view.py` 931、929（shooting_log_view）。
3. **无静态分析 / 类型检查门禁**：CI 只跑 pytest，风格/类型错误无法在合入门禁拦截；CI 注释与用例数漂移。
4. **全局可变配置单例**：`apply_saved_config()` 用 `setattr(config, ...)` 直接改写模块级 `config`，测试虽重置 DB/会话单例但未重置 config，存在测试间耦合风险。
5. **FTS5 可移植性**：依赖系统 SQLite 编译了 FTS5，部分 Linux 发行版未编译 → 自动回退 LIKE（README 已承认 Linux 未纳入验证），全文检索性能与一致性无保障。

---

## 四、优化项（按优先级排序）

> 评分：P0=建议立即做；P1=本迭代内；P2=后续规划。每项含「依据 / 影响范围 / 建议动作」。

### P0-1 收敛过度宽泛的异常捕获，提升错误可见性
- **依据**：`_load_settings` 静默吞掉损坏的 `settings.json`（`common.py:187`）；各 `update_*` 返回 `False` 不带原因（`database_service.py:690,829,925,1038`）；全仓 `except Exception` ≈130 处，不利排障。
- **影响范围**：配置、所有写操作的失败诊断；用户可感知的"操作失败但无原因"。
- **建议**：① 用具体异常类型（`sqlite3.Error`/`OSError`/`ValueError`）替代 `except Exception`；② `update_*` 失败区分"不存在(NotFound)"与"错误(DBError)"并返回结构化结果或抛业务异常；③ `_load_settings` 损坏时告警并保留原文件（`.corrupt` 备份）而非静默丢弃。

### P0-2 抽取重复代码（重复率最高、收益最大）
- **依据**：3.2 列出的 7 类重复（重复代码约占 Services 层 15%+）。
- **影响范围**：`common.py`、`backup_service.py`、`raw_extraction_service.py`、`media_import_service.py`、`database_service.py`。
- **建议**：① 统一 `calculate_speed` 到 `common.py` 并删除备份内副本；② 新增 `Utils/scanner.py`：一个 `scan_files(root, exts, recursive)` 替换 4 处扫描循环；③ 备份线程池 submit/as_completed/shutdown 抽为 `_run_parallel_targets(targets, worker, ...)`；④ UPDATE 白名单抽为 `_build_update(table, id_col, **kwargs)` 通用方法 + 字段注册表（同时消除注入隐患，见 P1-8）；⑤ `extract_raw_files` / `streaming` 合并为带 `pre_scanned` 参数的单函数。

### P0-3 大结果集内存与分页治理
- **依据**：`get_media_assets` 无分页（`database_service.py:1249`）；`search_assets` 全量 `list()`（`:1679`）。
- **影响范围**：素材信息视图、CSV 导出、检索结果处理；大项目（万级素材）内存与卡顿。
- **建议**：视图与导出统一走 `iter_search_assets`（游标 + `fetchmany`）；为 `get_media_assets` 增加 `limit/offset`；导出改为流式写入。

### P1-5 配置层持久化健壮性
- **依据**：`apply_saved_config` 直接 `setattr` 改写全局 `config`；测试未隔离 config；`settings.json` 损坏静默丢失。
- **影响范围**：配置读写、多工作区/数据库目录切换、测试稳定性。
- **建议**：配置读写加 schema 校验（字段类型/路径可写性），失败回滚到默认值并提示；测试 fixture 增加 config 重置。

### P1-6 巨型模块拆分
- **依据**：`database_service.py` 2100 行、`asset_info_view.py` 1391 行、`backup_view.py` 986 行。
- **影响范围**：可读性、合并冲突概率、单测聚焦度。
- **建议**：`database_service` 按实体拆子模块（workspace/project/asset/log/backup_job/template）+ 保留门面类；视图拆"列表 / 详情面板 / 批量操作"子组件。

### P1-7 备份重试与校验的 I/O 优化
- **依据**：`retry_failed_files` 重扫全源（`backup_service.py:425`）；`verify_backup` 全量重算哈希（`:137`）。
- **影响范围**：大卡重备、定期完整性校验耗时。
- **建议**：重试复用上一次 job 快照中的文件清单（已存 `pending_files/failed_files`）而非重扫；校验支持"仅比对已存 `checksum_value`"与"抽样/全量"两档。

### P1-8 字段白名单注册表（去重 + 防注入）
- **依据**：5+ 处 UPDATE 样板各自硬编码白名单；新增字段漏加白名单即静默丢弃或成注入隐患。
- **影响范围**：`database_service` 全部更新接口。
- **建议**：用 `@field_registry(MediaAsset)` 集中声明可写字段 + 序列化规则（list→`|`/json、bool→int），通用 `_build_update` 消费，消除重复并单点保证安全。

### P2-10 测试覆盖增强
- **依据**：核心 services 覆盖好，但 UI 交互路径、错误/取消路径、并发竞态、迁移幂等边界仍有缺口。
- **影响范围**：回归安全网。
- **建议**：补 `TaskViewModel` 取消语义、备份中断恢复、迁移失败回滚、文件丢失清理确认、配置损坏恢复等用例。

---

## 五、新增功能建议（按优先级排序）

> 每项含「功能目标 / 适用场景 / 预期收益 / 影响范围」。

### F1（高）破坏性操作 Undo / 回收站
- **目标**：删除项目/素材、批量重命名等不可逆操作可撤销。
- **场景**：误操作删除素材记录或重命名后想回退；当前删除直接落库、无后悔药。
- **收益**：大幅降低误操作损失与客服压力；提升专业用户对"数据安全"的信任（与产品定位高度契合）。
- **影响**：`database_service` 增加操作快照/回收表；Views 增加撤销入口；与现有 `operation_logs` 联动。

### F3（高）定期 / 计划性完整性校验
- **目标**：基于现有 `verify_backup` 增加定时调度（每日/每周）+ 报告。
- **场景**：长期归档盘位腐烂（bit-rot）、备份盘意外篡改的早期发现。
- **收益**：把"一次性校验"升级为"可运维的数据保全"，契合 DIT 行业合规诉求。
- **影响**：新增调度器（复用 `workers`）+ 校验报告视图；与 `operation_logs`、`report_service` 复用。

### F4（中）操作审计日志查看器
- **目标**：提供 `operation_logs` 的完整检索/筛选/时间线 UI（当前仅看板显示最近 5 条）。
- **场景**：团队追溯"谁何时备份/重命名/删除"；交付交接留痕。
- **收益**：把已采集的审计数据变现为可见价值，强化"可追溯性"卖点。
- **影响**：新增 `LogsView`；`database_service.get_operation_logs` 加分页/筛选。

### F5（中）增量 / 差异备份
- **目标**：基于已存校验和，仅拷贝自上次备份后新增/变更的文件。
- **场景**：同日多轮补拍、续备大卡，避免重复拷贝未变文件。
- **收益**：显著缩短备份时间、节省目标盘空间；与断点续传互补。
- **影响**：`BackupService` 增加差异比对（复用 `ChecksumService` 缓存）；UI 增加"增量模式"。

### F7（低）设置导入/导出与多机同步
- **目标**：`settings.json`、备份方案模板、项目模板可打包迁移。
- **场景**：多机协作、重装后快速恢复工作环境。
- **收益**：提升专业用户迁移体验。
- **影响**：`common.py` 设置读写层扩展导入/导出。
