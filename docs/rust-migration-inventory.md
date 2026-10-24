# Rust 原生迁移来源清单

维护范围：`MONOREPO-362`，WaveQuant Rust 迁移会话。整理日期：2026-10-24。

本文用于定位现有实现与记录覆盖边界；功能状态、验收与验证证据仍以根 `feature_list.json` 为唯一事实来源。“迁移中”表示任务已覆盖到该领域，不表示功能完成或与 Python 等价。用户先要求保留现有全部功能且不运行验证，随后要求启动 Rust 与前端，因此执行必要编译和启动连通确认；未主动运行回测、测试、lint、格式或浏览器。最新要求将在线数据默认切换为 AKShare Python SDK；下文早期阶段的未编译记录为历史事实，最新执行记录以 feature_list.json 为准。

默认行情通过 `data/akshare.rs` 调用内嵌的 `rust_sdk_worker.py`，使用 core 项目已有 AKShare 可选依赖环境。进程协议只开放行情、复权、分钟、目录、日历和涨停接口；Rust 仍承担 API、统一趋势、策略和账户计算。SDK 请求有进程并发、时间和输出预算，元数据及数据指纹绑定真实 SDK 版本与适配器源码。默认 API 固定同一 SDK 上游；显式兼容入口的其他 SDK 上游和 TDX/BaoStock 数据源保留。SDK 分钟档案使用独立表，不把旧直接抓取缓存冒充 SDK 数据。

## 来源与规模

本清单根据当前任务 worktree 的文件内容整理，不含未合并的其他会话改动。CodeGraph 对该 worktree 返回未索引，以下以文件搜索与源码阅读补足，不据此推断运行结果。

| 来源根目录 | 源文件数 | 物理行数 | 范围 |
| --- | ---: | ---: | --- |
| `apps/servers/wavequant-api/src` | 19 | 5,506 | Python HTTP、读模型、任务、自选与基础设施 |
| `apps/servers/api/src` | 20 | 348 | NestJS 生产 TS，排除 `.test.ts` / `.spec.ts` |
| `packages/wavequant-core/src` | 225 | 55,365 | Python 核心、适配器与随包 CLI |
| 合计 | 264 | 61,219 | 包含空行、注释与 `__init__.py`，不含测试目录/产物 |

源码中匹配 Python `def` / `async def` 声明的数量为 API 202 处、Core 2,027 处（包括方法、局部函数和私有辅助函数；不是独立业务规则数量）。因此不能用少量接口壳、返回空数组、固定布尔值或代理 Python 子进程来宣称迁移完成。原有 Python 不应在 Rust 覆盖未确认时删除。

## TrendAnalyzer 草稿流程与落地边界

来源为用户附件 `已粘贴的文本.txt`，原文件位于 `C:/Users/zb/.codex/attachments/b600fc10-571d-4941-b42e-72252e110e68/`。

需要保留的结构：

1. `TrendLevel`：折线 0、一级 1、二级 2、三级 3。四层属于同一种 K 线周期，`timeframe`（日/周/月等）是独立维度；例如周 K 先生成周 K 折线，再依次生成该周期的一、二、三级趋势，不跨周期取源。
2. `TrendAnalyzerState`：`version`、`currentIndex`、四层 `trendData` 与 `dynamicDataIndex`；构造时恢复可用缓存并合并各级开关。
3. 折线使用独立入口；一级至三级使用同一个 `executeLevelFunction`，只通过 `level` 选择来源与归属。
4. 结构起点记录与交易判断分开；交易链依次读取交替结束、资格区间、全局买卖点及反转状态。
5. 记录前置趋势全局起点、动态起点与下一层的数据偏移；允许导出完整状态和查询各层索引。

草稿不是可直接逐行翻译的算法实现：`judgeTrendState` 固定返回空多交替，三个检查函数固定返回 `true`，前置起点以数组长度的三分之一模拟，必须替换为 Python 的真实规则与证据。

主流程中的下列歧义必须有明确契约，不能默默形成新的交易规则：

| 草稿现象 | 影响与落地约束 |
| --- | --- |
| `runNextLevel` 注释表示运行下一级，但 `true` 分支使用 `continue` 回到外层循环 | 本迁移 feature 已采用语义：`true` 在当前 K 线立即推进下一等级。不能照抄为无限重跑折线。 |
| `while (true)` 中每次 `currentData = kLineData`，`kCount` 只用于日志 | 必须按当前 K 线前缀推进并以数据末尾终止；不得把未来行情传给当前判断。 |
| `currentIndex` 同时充当消费游标和前置趋势回溯起点 | 消费游标单调推进；结构起点保存在对应层字段，不能反转后把已消费索引倒退。 |
| 所有等级收到同一个 `currentData` | 按仓库不变量：一级仅从技术折线取源、二级从一级取源、三级从二级取源。 |
| `previousTrendData.length > 0` 被当作“新增数据” | 需用来源版本/已消费记录身份判定新增，不能每根 K 线重复追加同一个起点。 |
| `enableSecondaryReversal` 被注释为买点开关，代码却在关闭时跳过整个层级 | 必须区分结构持续更新与买点启用；若关闭买点导致高层结构停止，可能改变图表与后续来源，需明确说明该配置语义。 |
| 单个 `version` 就允许缓存恢复 | 恢复还需绑定行情身份、来源版本、配置、规则版本及已消费前缀，防止同长度不同股票/修订行情复用。 |
| 只有反转交易成立才返回 `runNextLevel` | 结构更新和交易资格不能互相替代；需保持 Python 已有的结构事实、六态确认及交易资格三个状态含义。 |

草稿中“空翻多/多翻空”的标签不能单独证明资格成立或买卖点成立；Python 中相应确认、资格、风控门禁仍属于完整迁移范围。

当前源码进一步区分三种复用：分析器内部按来源版本复用单层计算；受信任 `StructurePrefixCache` 按完整行情前缀、周期、参数和引擎身份直接复用四层结构与证书，命中时跳过结构计算；`CalculationStepCache` 复用已封存诊断块，只节省重复编码/写盘。结构前缀命中不恢复或跳过交易资格、分钟风控、持仓或资金推进；详细诊断仍读取该次实际使用的结构。API 的服务器配置决定结构缓存目录，不接受 HTTP 请求指定文件路径。具体接口和边界见 `docs/rust-computation.md`，当前未运行验证或性能测量。

## Python HTTP 接口清单

2026-10-24 Rust调度修订：用户确认结构变化推进上层、交易单独判断，且折线函数只返回折线和上一个趋势起点。`analyzer.rs` v8 的单层函数不调用全层准备或其他级别，主调度显式将 `data` / `nextOffset` 传入下级消费者（更高结构级别）；偏移使用本级数组坐标。默认讲义折线使用追加游标，三层整波复用同一持久状态机，来源修订仅重建相应路径。原始结构缓存不绑定asof，但市场许可仍按日更新；首次初始化/冻结前缀修订可重建，确认和许可仍有历史扫描，不宣称全部线性化。新增Rust回归源码未执行，本轮未改Python、编译、测速或重启，运行中的旧二进制不包含此改动。

入口：`apps/servers/wavequant-api/src/wavequant_api/server.py`。共有 25 个 GET 业务路径与 1 个 POST 业务路径；静态资源、主页跳转和 favicon 另计。当前整体迁移中，表内功能均须落实到真实 Rust 用例，路由存在本身不是完成证据。

| HTTP 路径 | 现有来源/职责 | 必须保持的边界 |
| --- | --- | --- |
| `GET /api/catalog` | `ChartRepository.catalog`，可通过基础设施缓存 | run/variant 与研究产物目录 |
| `GET /api/tdx-catalog` | `market_data.catalog("tdx")` | 无配置提示、版本 ETag/304 |
| `GET /api/akshare-catalog` | `market_data.catalog("akshare")` | 禁用提示、拒绝查询参数、ETag/304 |
| `GET /api/infrastructure/health` | `Infrastructure.health` | 数据库与 Redis 分项状态；未配置不冒充健康 |
| `GET /api/health` | `ChartRepository.health(run)` | 研究运行健康，与进程存活探针含义不同 |
| `GET /api/market-bars` | `application/market_bars.py` | 历史分页、来源、日期、版本冲突、ETag/304 |
| `GET /api/market-timeframe` | `application/market_timeframes.py` | source/symbol/asof/timeframe、两项趋势配置、快照身份 |
| `GET /api/tdx-view`、`GET /api/akshare-view` | `MarketTimeframeService.view` | 默认 1d；来源可用性与 asof |
| `GET /api/tdx-theory`、`GET /api/akshare-theory` | `MarketTimeframeService.theory` | 同份行情与趋势结果；配置进入身份 |
| `GET /api/view` | `ChartRepository.view` | run/variant/symbol/asof/scenario |
| `GET /api/stock-view` | `ChartRepository.stock_view` | run 上下文、scenario、两项趋势配置 |
| `GET /api/theory` | `ChartRepository.theory` | run 上下文、无 scenario、两项趋势配置 |
| `GET /api/stock-summary` | `ChartRepository.stock_summary` | run/variant/asof/scenario 聚合 |
| `GET /api/structure-signals` | `application/structure_snapshots.py` | lookback/signal_type/trend_level/markets；读模型不临时编造空结果 |
| `GET /api/buy-signals` | `application/buy_signal_snapshots.py` | scenario/start/lookback；AkShare 必须一个 symbol |
| `GET /api/limit-up-ladder` | `application/limit_up_ladder.py` | 日期、刷新、行情池归一化、有效量值与封板时间 |
| `GET /api/backtest-version` | `ChartRepository.backtest_version` | run/variant 当前策略版本 |
| `GET /api/tdx-backtest`、`GET /api/akshare-backtest` | `start_backtest` → repository 对应方法 | 发起受限任务并等结果；参数、版本、同股占用与幂等身份 |
| `GET /api/backtest-job` | `BacktestJobs.get/outcome` | running 202；completed/failed 结果、错误与进度 |
| `GET /api/backtest-jobs` | `BacktestJobs.snapshot` + 自选汇总 | 活跃/历史任务与自选刷新状态 |
| `GET /api/backtest-preview` | `BacktestJobs.preview` | job/after 修订序号；消费已有结果，禁止第二套趋势算法 |
| `GET /api/watchlists` | `WatchlistStore.load` | 自选快照与修订；不可用返回 503 |
| `POST /api/watchlists` | `WatchlistStore` + `WatchlistRefresh` | save/add/remove/import/settings/retry 六类动作、revision 冲突 |

`server.py` 另包含 loopback Host 校验、Origin 白名单、跨站写入拒绝、单值查询参数、大小限制、JSON 与有限数值编码、静态文件映射、安全响应头、错误脱敏及停机时刷新线程关闭。`PUT` / `DELETE` / `PATCH` 返回 405。不要在迁移时把读写能力或外网访问范围扩大。

回测入口选项：`volume_filter`、`net_reward_risk_filter`、`shallow_base_breakout_enabled`、`n_target_trend_confirmation_enabled`、`ordinary_trend_completion_enabled`、`buy_interval_level`、`initial_capital`、`max_position_weight`、`backtest_job`、`strategy_version`。必需参数为 run/variant/symbol/asof/scenario/start。

## Python API 模块映射

根目录为 `apps/servers/wavequant-api/src/wavequant_api/`。

| 来源组 | 迁移边界 | 状态 |
| --- | --- | --- |
| `server.py`、`cli.py`、`dev.py` | HTTP、启动参数、开发进程入口、静态页面/反向代理接入 | 迁移中；完整行为待迁移 |
| `backtest_jobs.py` | 限流、同股互斥、请求幂等、完成耗时冻结、历史 SQLite、TTL、预览与计算步骤 | 未完整迁移 |
| `watchlists.py`、`watchlist_refresh.py` | 分组/成员、乐观修订、旧版导入、设置、自选刷新队列、重试、持久汇总 | 未完整迁移 |
| `application/market_bars.py`、`market_timeframes.py` | 行情分页、周期转换、asof、版本快照、读模型缓存 | 未完整迁移 |
| `application/structure_snapshots.py`、`buy_signal_snapshots.py` | 版本化结构/买点查询与预计算存储 | 未完整迁移 |
| `application/backtest_preview.py`、`limit_up_ladder.py` | 预览修订发布、涨停梯队与外部池转换 | 未完整迁移 |
| `infrastructure/settings.py`、`services.py`、`cache.py`、`database.py` | 配置、MySQL/PostgreSQL/SQLite 仓储、Redis JSON、健康与释放 | 未完整迁移 |

数据库声明表：`wavequant_research_runs`、`wavequant_structure_signal_snapshots`、`wavequant_buy_signal_snapshots`、`wavequant_market_timeframe_snapshots`。自选与回测历史分别有 SQLite 文件；不能用一个进程内 Map 代替它们的重启恢复语义。

## NestJS 功能映射

来源根目录：`apps/servers/api/src/`。实际生产业务较小，不包含股票量化判断。

| 来源 | 接口/功能 | Rust 迁移要求 |
| --- | --- | --- |
| `modules/users/presentation/http/users.controller.ts` | `POST /api/v1/users` | 共享契约输入校验，成功 201，响应 presenter |
| `modules/users/application/use-cases/create-user/create-user.use-case.ts` | 邮箱查重后创建 | 重复邮箱 409 `Email already registered`；数据库唯一约束继续存在 |
| `modules/users/infra/typeorm/*`、`infra/database/migrations/1760973197060-CreateUsersTable.ts` | users 持久化 | UUID、name、唯一 email、createdAt/updatedAt；不能仅内存保存 |
| `health/health.controller.ts` | `GET /health` | 数据库 ping 的真实健康状态 |
| `app.setup.ts`、`main.ts` | `/api` 前缀、URI v1、CORS、压缩、安全头、Swagger `/api/docs`、停机钩子 | 配置与 HTTP 行为兼容；Bearer 文档声明不等于已实现认证 |
| `shared/pipes/zod-validation.pipe.ts`、`shared/utils/zod-swagger.ts` | Zod 到请求错误/文档 | Rust 校验与 OpenAPI 对齐现有 contracts |

状态：迁移中，需与实际 Rust 实现逐项对照；本清单不声明数据库、Swagger或契约兼容已经完成。

## Core 全部生产源码分组

以下路径统一相对于 `packages/wavequant-core/src/wavequant/`。文件数包含各组 `__init__.py`。表中所有组都属于用户“现有全部功能”，不能因为没有 HTTP 路由就自动排除 CLI、纸面交易、恢复和研究工具。

| 来源组 | 文件/行 | 主要职责与代表源码 | 状态 |
| --- | --- | --- | --- |
| `domain/models` | 5 / 428 | `model.py` Bar/NSetup/信号模型，`config.py` 策略配置，`validated_bars.py` 行情边界，`a_share_security.py` 证券规则 | 迁移中 |
| `domain/market_state` | 10 / 1,232 | candle_strength、volume_candle、volume_reversal、market_turn、market_regime、squeeze_state、washout、control_bar、wave_strength | 迁移中；细项未完整覆盖 |
| `domain/market_structure` | 73 / 25,623 | 折线、N、主控波浪、统一多级趋势、普通/特殊反转、六态、确认、知识时钟、证据与历史缓存 | 迁移中；细项未完整覆盖 |
| `domain/strategies` | 66 / 15,517 | 资格生命周期、N轮次、买卖点、分批退出、量价风控、时间门禁、逐日策略与审计 | 迁移中；细项未完整覆盖 |
| `application/analytics` | 15 / 3,873 | backtest、research、system_research、intraday_entry、intraday_wave_exit、holding_drawdown、execution_diagnostics、trade_evidence、evidence_statistics、screening_funnel、quality、notion、foundation_audit、validation_suite | 未完整迁移 |
| `application/trading` | 6 / 812 | order_service、paper_venue、intent_execution、stream_runtime、account_report | 账户/纸面撮合/FIFO 报告已有 Rust 代码，完整调度仍待迁移 |
| `application/governance` | 3 / 539 | operations、platform_audit、运行完整性、恢复与就绪状态 | 未完整迁移 |
| `infrastructure/market_data` | 9 / 1,057 | CSV/JSON、通达信、AkShare/history、分钟数据、security_master、data_catalog、合成数据工具 | 未完整迁移 |
| `infrastructure/persistence` | 7 / 1,021 | artifact_cache、calculation_step_cache/chunk、event_store、experiment_registry、operational_store | 未完整迁移 |
| `infrastructure/computation` | 3 / 287 | trend_workers、process_memory；有界多进程与资源边界 | 未完整迁移 |
| `infrastructure/filesystem` | 3 / 124 | project_paths、backup_bundle；新目录备份/恢复与边界 | 未完整迁移 |
| `interfaces/charts` | 6 / 2,162 | visualization、chart_geometry、market_data_repository、tdx_browser、akshare_browser | 未完整迁移 |
| `interfaces/research_tools` | 10 / 1,599 | stock/tdx/akshare/streaming_backtest、research_panel、signal_execution_stream、strategy_evidence、lecture/hierarchical profile 校验工具 | 未完整迁移 |
| `interfaces/screening` | 3 / 671 | structure_scanner、buy_scanner | 未完整迁移 |
| `interfaces` 根 | 2 / 378 | CLI 总入口与包文件 | 未完整迁移 |
| 包根及 application/domain/infrastructure 根 | 4 / 42 | 初始化、版本与包导出 | 待对应 Rust 导出确认 |

### 趋势与证据迁移细项

`domain/market_structure/` 不能简化为单个高低点比较。主要来源分组：

- 折线/形态：`polyline.py`、`polyline_lifecycle.py`、`foundations.py`、`lecture_drawing.py`、`n_shape.py`、`n_defense.py`、`price_action.py`、`candle_primitives.py`、`a_wave.py`、`a_wave_rules.py`、`abc_candidate.py`、`wave_projection.py`、`bottom_n_targets.py`、`positive_n_reformation.py`。
- 共用趋势：`trend_judgement.py:judge_trend`、`trend_pipeline.py:calculate_trend_hierarchy`、`trend_levels.py`、`lecture_trend.py`、`secondary_trend.py`、`tertiary_trend.py`、`trend_lines.py`、`trend_primitives.py`、`trend_body_reversal.py`、`n_trend_reversals.py`、`trend_structure.py`、`trend_landmarks.py`、`trend_foundations.py`。
- 确认/交替/退出：`trend_confirmation.py`、`hierarchical_confirmation.py`、`hierarchical_development.py`、`n_trend_confirmation.py`、`bull_alternation_confirmation.py`、`squeeze_alternation.py`、`alternation_breakout.py`、`alternation_duration.py`、`confirmation_gap.py`、`observation_window.py`、`failed_breakout_exit.py`、`source_bear_exit.py`。
- 层级推进和时序：`trend_cascade.py`、`trend_level_upgrades.py`、`trend_lifecycle.py`、`trend_source_stream.py`、`trend_staging.py`、`trend_stage_work.py`、`trend_market_index.py`、`trend_promotion_scope.py`、`structural_n_ownership.py`、`trend_display.py`、`trend_prefix_view.py`、`trend_options.py`。
- 版本化证据/历史：`owned_evidence*.py`、`owned_record_prepared_history.py`、`evidence_record_sequence.py`、`finite_source_receipts.py`、`prepared_*history.py`、`trend_point_records.py`、`trend_point_version.py`、`trend_frozen_state.py`、`trend_history_changes.py`、`trend_evidence_reuse.py`、`trend_readonly_cache.py`、`trend_publication*.py`、`trend_upgrade_checkpoint.py`、`strategy_geometry_version.py`、`calculation_plan.py`、`calculation_steps.py`。

每级结构结果、确认知识日、可交易时间、消费者资格和显示身份须保留各自含义。Rust 可以重写 Python 的容器/缓存实现，但不可丢失其预算、来源修订失效、历史快照不可变及未来数据不可见约束。

### 交易规则迁移细项

`domain/strategies/` 的规则来源索引如下。对应原生模块已按候选、资格、目标、风险、历史和日调度拆分；本列表用于追溯来源，实际代码覆盖见后文及相关 workspace 进度，不把文件名称对应视作运行等价性证据：

- 总入口/配置：`integrated_strategy.py`、`daily_strategy.py`、`lecture_strategy.py`、`strategy.py`、`strategy_profiles.py`、`features.py`、`market_calculations.py`、`backtest_analysis.py`。
- 资格/时间：`bull_eligibility.py`、`buy_intervals.py`、`trading_intervals.py`、`market_timing.py`、`signal_clock.py`、`hierarchical_entry.py`、`hierarchical_n.py`、`squeeze_n_round.py`、`n_record_reconfirmation.py`。
- 入场：`attack_quality.py`、`eligible_polyline_n.py`、`folded_n.py`、`whole_wave_entry.py`、`shallow_base_breakout.py`、`combined_a_entry.py`、`five_top_entry.py`、`gap_entry_gate.py`、`inverse_n_entry.py`、`inverse_reentry.py`、`mother_child_inverse_n.py`、`multilevel_squeeze.py`、`nested_alternation_breakout.py`、`secondary_pullback_entry.py`、`secondary_reclaim_entry.py`、`same_level_recovery.py`、`completed_wave_recovery.py`、`ten_full_entry.py`、`one_p_breakout_entry.py`、`two_t_entry_gate.py`、`two_t_resistance.py`、`secondary_resistance.py`、`n_consolidation.py`、`wave_continuation.py`。
- 退出：`breakout_shadow_exit.py`、`double_volume_lower_close_exit.py`、`double_volume_fade_exit.py`、`double_bullish_shadow_exit.py`、`gap_bearish_exit.py`、`limit_up_distribution_exit.py`、`pressure_exit.py`、`staged_exit.py`、`trend_flip_exit.py`、`wave_exhaustion_exit.py`。
- 历史消费：`backtest_history_reuse.py`、`chart_drawing_history.py`、`chart_entry_history.py`、`combined_a_replay.py`、`daily_risk_histories.py`、`event_history.py`、`hierarchy_stream.py`、`history_cursor.py`、`market_preview.py`、`polyline_target_stream.py`、`recovery_entry_history.py`、`strategy_history_cache.py`、`structural_n_stream.py`、`wave_projection_stream.py`。

纸面交易还要求事件账本、现金预留、仓位、撮合、成交费用、T+1/可卖约束、重启重放及 asof 账户报告；这些职责来自 `application/trading/` 与 `infrastructure/persistence/`，不能用“生成买卖信号”替代。

### CLI 功能边界

`interfaces/cli.py` 声明：`validate`、`backtest`、`research`、`import-tdx`、`run-tdx`、`demo`、`notion-research`、`foundation-audit`、`system-research`、`platform-audit`、`paper-demo`、`journal-check`、`strategy-evidence`、`system-run`、`system-status`、`system-backup`、`system-restore`、`system-alert-ack`、`account-report`。保留对应功能不代表现在可以执行它们；用户本次禁止运行验证、回测与探针。

## Rust 起始缺口与实施顺序

本任务开始时，Rust API 只有启动配置、停机与健康探针；Rust Quant 只有行情输入领域、用例和 JSON/CLI 适配。现有工程骨架的历史检查不能作为本轮迁移证据。

建议按依赖顺序实现：

1. 对齐行情、索引、价格/成交量、证券、日期、配置与错误契约，并保留现有 `@repo/contracts` 对外字段。
2. 逐 K 线 `TrendAnalyzer` 状态、前缀可见性、统一各级核心与缓存恢复；再完整搬迁普通/特殊反转、确认、资格及交易规则。
3. 完成数据源和持久化端口，接入行情分页、周期快照、结构/买点查询；复用同一份分析结果。
4. 完成回测执行、账户、任务状态、预览与自选刷新；实现返回真实产物的兼容接口。
5. 完成 Nest 用户/数据库健康/OpenAPI、研究 CLI、纸面交易与运维功能；逐项更新覆盖证据。

没有实现的兼容入口应明确返回可识别的不可用错误，且在此清单保留缺口；这仅避免伪造结果，不能算迁移完成。最终完成标准是全部功能有真实 Rust 实现与对应来源映射，并按用户未来授权安排验证；当前没有等价性或可运行性结论。

## 本轮执行模块代码覆盖（未验证）

新增目录：`apps/servers/wavequant-quant-rs/src/execution/`。仅按源码实现，不代表已编译、测试、运行或与 Python 全部输出等价。

| Rust 文件 | 已写出的来源行为 | 边界 |
| --- | --- | --- |
| `journal.rs` | `event_store.py` 的事务内读取/判定/追加、唯一身份幂等、哈希链、SQLite WAL/FULL 与禁止更新/删除 | 使用独立 `rust_execution_events_v1` 表和 Rust 序列化格式；备份由原生 `journal-check --backup` 与目录备份 CLI 接入，不直接读取旧 Python 账本 |
| `account.rs` | `order_service.py` 的订单接收、报价/时间门禁、资金预留、仓位/行业/总敞口/风险预算、T+1、费用预算、部分成交、撤单竞态、拒单、停机、对账后恢复、公司行为；`account_report.py` 的 asof FIFO 成本、费用、分红与净值归因 | 点时证券事实由调用方提供；不包含证券主数据服务；只支持既有 0/1 日结算模型 |
| `venue.rs` | `paper_venue.py` 的独立订单/成交日志、开盘可见性、按接受顺序共享流动性、方向性 tick 滑点、限价、逐笔佣金/卖税、部分成交、撤销/拒绝、公司行为、恢复投递及回报失败停机 | 是明确的纸面开盘模型，无真实券商或委托队列；OMS 和 venue 必须使用不同数据库路径 |
| `market.rs` | `a_share_security.py` 板块识别；`data.py` 历史 ST/板块涨跌幅与开盘权限；`backtest.py:transaction_fee` 日期分段佣金/过户费/印花税 | 以 Python 当前日期界线为迁移来源，不另作证券规则更新；新股无涨跌幅窗口仍不推断 |
| `engine.rs` | `Strategy::on_close` 读取已收盘前缀，接收显式订单意图，在下一对应标的开盘提交与撮合，记录拒绝、成交、权益与未执行信号 | 新的原生纸面逐 bar 驱动，不是完整 `analytics/backtest.py` 迁移；不产生默认买入持有或假信号 |

`ReplayEngine` 的开盘容量要求调用方传入已知原始股数容量，不使用当日收盘总成交量推断开盘可成交数量。日期相关税费函数与纸面 venue 的固定费率配置是 Python 中不同的既有模型，不应混写成一套相同手续费。

后续已新增 `execution/strategy_backtest.rs`，代码覆盖如下，均未运行验证：

- `BacktestConfig` / `size_entry`：历史均量参与率、复权手数与最低数量、共享现金、仓位权重、已有仓位风险、手续费后再约束、实际滑点和往返费用后的净收益风险比。原 Python 逐手递减改为对单调约束二分定位，没有使用预设买入数量。
- `StrategyBacktest`：既有持仓追加、T+1 可卖余额、按当前持仓或初始持仓累计减仓、两类九成减仓向上取整、费用分摊、分批交易的最终归并、待退出余额与优先卖出、成交后风险观察标志。卖出数量超过历史容量时延期，未改成自动按容量部分成交。
- `run_strategy_backtest`：多标的同一时刻先处理退出，再按相对量和股票代码处理入场；按日开、已完成五分钟、下一五分钟开盘及日收推进。分钟显式要求与日线同 source/data_version，并调用 `data::minute::verify_minute_day` 检查 48 根时段、量和 OHLC 一致性；午间跨时段使用真实下一分钟开盘时间。
- 同收盘执行明确标注为 `daily_close_simulation`；非一字涨跌停模拟成交仅在显式配置与已观察证据允许时启用，并保留无排队验证标记。普通滑点卖价不得低于可证明的交易所跌停价格。
- `DecisionStrategy` 只接收历史已收盘 K 线与当前实际已知部分 K 线；`EntryDecision` 必须含真实入场价格、止损、目标/风险参数和已有资格门禁证据。`ExitDecision` 直接接受 `analysis::trading::RuleDecision`。同一时点多个策略退出规则必须由正式策略解决优先级，执行器不猜测替代它；`hard_exit_decision` 提供既有止损、目标与持有时间门禁供正式策略调用。

后续代码已接入 `analysis/system.rs` 的正式策略/账户调度、`analysis/trading/day.rs` 的波浪和资格事件消费、显式缺失分钟日线回退、`analytics/holding_drawdown.rs` 的持仓回撤及 `analytics/runner.rs` 的研究报告。冻结日信号与在线日策略复用同一系统账户入口；旧的私有简化 `SignalStrategy` / `account_configuration` 已移除。`execution/stream.rs` / `intent.rs` 实现持久意图编排，详见后文。无分钟来源时不伪造分钟成交；实际回退记录进入审计。HTTP 协议适配由 API workspace 维护，当前没有执行结果、数值等价或字段兼容性的验证结论。Python 原调用链中使用整日 K 线过滤开盘买单与 Rust 已知数据门禁的差异，仍需在允许验证后按明确时点契约评估。

### 日线风险观察器

新增 `analysis/trading/risk.rs`，直接对应 `domain/strategies/pressure_exit.py`、`breakout_shadow_exit.py`、`staged_exit.py` 和 `wave_exhaustion_exit.py` 的规则，仍为迁移中、未验证：

- 压力区：20 日量价供给蜡烛、供给区接触后的正 N 不利响应、供给高点突破和涨幅门槛、旧纪录高点受阻、攻击/响应窗口巨量减三成及次日巨量延续全清。候选、已知前高与基准均量在事件发生时冻结。
- 前高突破：只消费统一结构中前一交易日已经闭合且可知的 H 点，保留突破当笔/次笔、阴线子线、跳空大阴线、倍量开板和上影减九成。次日低开全清证据仅带昨日警告与当日开盘，不附带当日完整量价。
- 量价退出：母子/子母警告、放量跌破、巨量跳空、三倍量次日大阴线、阳线半实体失守、阴线外包、三成/七成累计减仓、支撑破位、次日低开/延续、十倍量、涨停封板例外，并复用 `exits.rs` 的分阶段支撑和其他已实现规则。
- 波浪退出：五顶/十足已知目标、子线破低、低开量增、实体内包长上影、C 等幅目标一跳接近、普通 A 的 C0.618/等幅、目标处吞没与阳阻失败、上影/量价减仓及其后续清仓。`RiskConfig` 保留原配置比例；`wave_reduced` 必须由实际成交状态回填。

`evaluate_daily_risks` 返回独立风险证据集合；空仓或没有已产生的目标事件时，相应持仓/目标规则不适用，不能因此阻断新的入场。`RiskState.projection_events` 需要真实、已知的 `wave_projection_*` / `wave_ordinary_entry` / `wave_c_equal_target` 事件；普通 `n_completed` 不能充当测幅完成。`evaluate_holding_risks` 接收实际持仓上下文、其专属波浪事件与独立 `HoldingRiskState`，按成交身份重置阶梯/量价状态。`resolve_exit_precedence` 对应 Python 回测的母子倒 N、波浪全清、压力、量价、硬风控、逆阻、部分退出及阶梯优先顺序，保留五顶上影与较轻减仓的例外。`DecisionStrategy.open_risks` 在所有开盘成交前受理昨日已冻结警告的低开全清，取消待入场单。`analysis/system.rs` 现通过 `observe_account`、持仓所属波浪事件筛选、真实日信号及成交身份提供这些上下文；完整链路已经接线，尚无运行验证或 Python 等价性证据。

### 持久化、治理与目录恢复

新增 `persistence/` 和 `governance/`，代码已实现但未运行：

- `EventStore`：事务内幂等追加、哈希链、不可更新/删除触发器、只读 WAL 快照、在线 SQLite 备份。采用独立 `rust_events_v1` 表；执行账户使用的 `rust_execution_events_v1` 也独立，不向 Python 原账本混写不同 JSON 数值编码的哈希。
- `ArtifactCache`：规范 JSON、内容寻址、zlib、校验和、身份校验、容量触发器、按创建顺序分批淘汰、引擎版本失效和每键弱引用锁。缓存读取/写入失败按 miss/false 返回，不把有效计算结果变为空扫描。写超时 500ms、读超时 30s；初始化争用直接跳过可丢弃缓存；解压上限显式配置。
- `ExperimentRegistry`：开始时核验输入指纹、不可重用运行身份、完成时间约束、成功时再次核验输入和非空产物、终态不可重写、产物完整性报告。
- `WorkspaceLock` / 运行与告警状态：操作系统锁随进程退出释放；锁文件不删除。告警按内容和范围去重，确认保留故障，条件消失才关闭，重复确认要求同一理由。只提供本地持久收件箱。
- 目录快照：新目标和不交叉树约束、拒绝链接/重解析点与不安全成员路径、冻结前后指纹一致、SQLite 在线快照和完整性检查、已 checkpoint 数据的字节保留、清单/额外文件核对、只恢复到新目录。
- `run_operations` / `run_platform_audit`：离线配置、身份/配置冲突、旧 RUNNING 标记 INTERRUPTED、冻结源码与行情来源、工程/数据/研究/纸面阶段、运行报告、就绪差距、告警、备份/恢复和账本头对比、最终输入/环境核对、产物封存及失败记录。`operations_status` 只读运行账本和已完成产物，不创建或改写监控数据。

这些编排函数通过 `OperationsPipeline` 接实际原生阶段服务，接口不填充“passed”替身。原生 CLI 阶段装配已接 `analytics/validation_suite.rs` 的治理研究、`analytics/runner.rs` 的独立研究，以及真实数据审计、纸面账户和恢复服务；代码接线不代表整套系统验收已完成。本轮没有创建任何备份、恢复任何目录或执行任何检查。

### 原生 CLI、工程夹具与浅底突破

`presentation/{cli,commands,native}.rs` 保留 `inspect` / `analyze`，新增 `validate`、`import-tdx`、`system-run`、`system-status`、`system-backup`、`system-restore`、`system-alert-ack`、`journal-check`、`account-report`、`paper-demo`、`platform-audit`、`system-research` 命令。账户报告及账本检查使用只读 SQLite，不会因路径拼错创建空库；新增输出和恢复目录拒绝覆盖已有内容。

- `NativePipeline` 接真实行情加载、PIT 数据审计、来源指纹、TDX `.day` / `gbbq` 因果复权、账户/撮合器日志、治理编排。固定工程门禁仅在将来显式调用相关 CLI 时执行 `cargo test -p wavequant-quant-rs --lib --tests`，失败及零个实际测试均不能生成通过证据。本次没有运行该命令。
- 原 Python `platform_audit.run_paper_demo` 的十根工程行情、legacy N 配置和显式清仓保留；入场读取真实 `TrendAnalyzer` 交易候选并要求恰好一个可执行 LONG，再按账户风险、现金、份额和手续费预算计算订单。独立 Venue 记录部分成交，账户重开后重复接收、T+1 清仓、对账与备份比对都由真实服务执行。代码没有预设任何夹具已通过结论。
- 浅底突破对应 `shallow_base_breakout.py`：同源正式生命周期和下级已闭合点产生浅交替候选，61.8% 至不足 2/3 回撤、B 段时长、40–120 根横盘窗口、18% 区间、强阳、倍量、目标空间及已消费身份门禁。仅保留前一交易日已知候选作为本日机会，当前低点或将来确认不能补作入场。
- 待清仓 `strict_structure_unresolved` 若次日开盘高于前收，执行层保留该日观察；当日收盘仍不高于前收且没有更高优先退出/成交时才按同收盘退出。强收盘撤销旧延期请求，来源为原 Python `analytics/backtest.py`。

研究 CLI 的策略/场景/窗口报告由正式 runner 接入，当前文档不把它记为已运行；Python 原账本的直接读写兼容未提供。所有上述代码仍待用户允许后的验证。

### 通用持久策略与意图桥接

新增 `execution/stream.rs` / `intent.rs`，对应 `stream_runtime.py` / `intent_execution.py`。行情、策略版本、意图、已评估记录、拒绝和机会确认与 `Account` 共用同一执行日志；资金投影忽略这些非财务记录。行情提交后再计算，评估/意图一起提交；重投相同时间及内容可以恢复，冲突内容拒绝。已完成评估直接读取持久身份，不重复运行策略。

`TrendStreamStrategy` 复用真实 `TrendAnalyzer`，同品种递增输入追加到原实例，仅进程恢复或前缀变化才重建；计算异常丢弃半推进缓存，后续可从已提交 BAR 恢复。LONG 使用实际日策略候选，EXIT 使用实际退出事件与 Python 原有 high 失效价。不可执行候选、未覆盖规则、同日同侧多个规范信号均返回错误，不能生成替代信号。

`execute_intent` 保留显式因果时钟、原始价格/复权因子、跳空阈值、证券份额、手续费后现金/风险/权重、目标净赔率、过期、同一订单重试及已接受订单后确认的恢复逻辑。入场经济拒绝为终态 FILTERED，退出拒绝保持 DEFERRED；持久化和意外内部错误直接上抛，不误记为策略拒绝。纸面工程夹具已经调用这一通用桥接器，仍未运行验收。

### 研究面板、证据流程与 CLI 后续覆盖

以下覆盖更新替代前文相应阶段的“仍待连接”记录；只表示代码已写出，不表示命令已执行或等价性已证实。

- `analytics/research_panel.rs` 对应 `interfaces/research_tools/research_panel.py`：只读取首条本地记录判断入选资格；按固定 SHA-256 与必选名单冻结样本，先保存名单再读取全量 OHLC；使用真实 `.day` / `gbbq` 复权、前后指纹及隔离名单。缺失或不支持的来源不会由另一只结果更好的股票替换。
- `analytics/strategy_evidence.rs` 对应 `interfaces/research_tools/strategy_evidence.py`：冻结候选与严格参照、校验区间、真实逐前缀策略信号、候选/拒绝计数、前缀因果比较代码、期间账户与双倍成本、联合日期区块统计、相对控制组统计、仅训练段选择、CASH 候选、滚动窗口、等权基准及实验登记。当前实现采用有界独立 Rust 线程；报告明确写出线程后端，不伪称 Python 原进程池。冻结 CSV 指纹进入每个分析器来源身份。
- `presentation/commands.rs` 已连接原 CLI 的全部 19 个命令名称：`validate`、`backtest`、`research`、`import-tdx`、`run-tdx`、`demo`、`notion-research`、`foundation-audit`、`system-research`、`platform-audit`、`paper-demo`、`journal-check`、`strategy-evidence`、`system-run`、`system-status`、`system-backup`、`system-restore`、`system-alert-ack`、`account-report`。`import-tdx` / `run-tdx` 保留原默认目录、股票与当日结束日期，以及多股票参数。独立 `system-research` 接 `runner`；治理研究阶段接 `validation_suite`。
- `demo` 以原固定种子、500 根、5 个模拟证券调用显式 `synthetic_dataset`，再调用真实基础策略与账户；仅输出 `synthetic_engineering_only` 类型。演示行情不是线上行情来源，也没有内置收益结果。
- 新增隐藏 `internal-worker` 直接接原生计算进程的长度帧协议；该入口不向 stdout 打印普通 JSON、日志或帮助文案。
- 原工程门禁仍保留为产品运行功能；本次迁移没有调用门禁、研究、回测、纸面示例、恢复或任何运行探针。源文件记录与查阅不作为验证证据。

研究账户衔接已改为 `runner::portfolio_with_confirmation`：冻结信号进入同一系统账户适配器，调用方逐候选传递 `n_target_trend_confirmation_enabled`，CASH 使用 false。日策略候选的实际攻击时间、回撤、量比与市场状态已规范进 `EntryDecision.evidence`；桥接层优先读取真实时间，缺少 LONG 回撤或市场状态时明确拒绝，不能用当前日期或笼统状态替代来源。以上仍只属于代码覆盖，完整策略语义与各配置下的执行效果未验证。

### 旧独立批候选入口与日策略边界

新增 `analysis/trading/legacy_candidates.rs`，保留 Python `domain/strategies/integrated_strategy.py` 中 `_analysis=None` 的候选准备方法。输入 `LegacyStructureHistory = BTreeMap<usize, Vec<KernelFrame>>` 必须是统一核心已经计算的逐日同周期结果；本模块不调用趋势判定器，不生成第二份趋势结构。

| Rust 公开接口 | Python 方法来源 | 行为与边界 |
| --- | --- | --- |
| `hierarchical_n_candidates(&history)` | `domain/strategies/hierarchical_n.py:hierarchical_n_candidates` | 读取当前路径的二、三级 `publication` 许可点，枚举真实 L/H 后的最低回调 L；前高被再破或原点失守即结束，按坐标和首次可知日去重，保留 `n_level=2/3` |
| `folded_positive_n_candidates(points, earliest, allow_equal_origin)` / `folded_inverse_n_candidates(points, earliest)` | `domain/strategies/folded_n.py` 同名方法 | 复用 `targets::folded_n_candidates` 的完整包络和最早边界，返回旧分支的 `n_level=1` 元数据 |
| `legacy_candidate_sets(&history, &config)` | `integrated_strategy.py` 的 `candidate_sets` / `folded_sets` 构建段 | 保留原顺序：先全部每日折线和高层候选，再全部折叠候选；折线候选为 `n_level=0`，整波且 lecture 配置才追加高层及折叠来源 |
| `prepare_legacy_candidates(bars, &history, &config, timeframe)` | `integrated_strategy.py` 的 N 准备、攻击去重、量价与严格攻击质量段 | 返回 `LegacyNPreparation { candidates, events, counts }`，保留结构断路和期限、母子/子母/内包同棒条件、攻击完成日去重、真实 N 观察、control 和 regime 证据。该返回对应进入底部归属及入场仲裁之前的候选，不发订单、不建立 `TradingInterval` |

统一 `TrendAnalyzer` 使用 Python `_analysis` 分支的资格作用域折线来源：只从共享技术折线取 N 顶点，保留 `n_level=0`，不自动追加上述旧批候选。这里的 `n_level` 是原候选来源元数据；结构 `level=0/1/2/3` 分别是同一 K 线周期的折线、一级、二级和三级，日/周/月/季/年由独立 `timeframe` 表达。Python 兼容配置 `buy_interval_level=0` 表示不限交易资格，与结构折线级不是同一含义。

`analysis/trading/day.rs` 与 `trading.rs` 的非日线入口返回 `trading_not_applicable`，保留结构分析，不执行日级 N、资格或风险规则，也不以正常不适用状态污染 `unavailable_rules`。当日结构暂停仍由 `strict_structure_unresolved` 产生实际退出并阻断新买入；历史暂停不等于永久缺实现。

`analysis/trading/diagnostics.rs` 从实际被消费的候选、资格窗口、量价、拒绝和信号分支累计 `counts`，未发生的分支不凭缺失记录填入零。整波量价审计在主 N 筛选后发布；后续逆 N 终止普通局部轧空，真实新 N 再确认按原时钟恢复。`TradingDayState::set_minor_points(Option<Vec<ConfirmedPivot>>)` 区分未提供次级数据与显式提供空集合，保持 `minor_data_available` 的来源含义。

以上仅为源码迁移和接口接线记录；本次没有运行测试、格式化、lint、类型检查、构建、回测、Git 状态/差异检查或运行探针，不能据此认定编译可用或与 Python 输出等价。


AKShare桥接脚本归属Rust工程：`apps/servers/wavequant-quant-rs/scripts/rust_sdk_worker.py`，协议回归位于`tests/python/test_rust_sdk_worker.py`。旧Python工程不再新增源码或测试；Rust仍嵌入同一脚本并复用既有AKShare环境。
