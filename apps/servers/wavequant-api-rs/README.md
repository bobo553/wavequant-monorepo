# WaveQuant Rust API

Axum + Tokio 后端，迁移 Python `apps/servers/wavequant-api` 与 NestJS `apps/servers/api` 的既有入口；量化判断调用 `wavequant-quant-rs`，具体规则参考 `packages/wavequant-core/src/wavequant/`。

## 目录

| 目录 | 职责 |
| --- | --- |
| `src/bootstrap/` | 强类型启动配置、命令、监听和关闭 |
| `src/presentation/` | 路由、请求校验、响应映射、同源边界、安全头与错误处理 |
| `src/application/` | 行情/研究读模型、量化任务、图形、自选刷新与扫描快照 |
| `src/storage/` | 研究快照、任务/自选 SQLite、数据库、Redis 与缓存适配 |

## 使用

默认保留 Rust 工程的 `127.0.0.1:8010` 回环监听。以下为使用说明，本轮没有执行：

```powershell
cargo run --locked -p wavequant-api-rs -- --listen 127.0.0.1:8010
```

行情、研究产物与基础设施由启动配置提供。生产运行前应显式选择数据路径和来源。可配置原生 quant worker 的绝对路径与有界进程数；请求与结果绑定引擎、配置和行情指纹。队列预算不足时可在原串行入口处理，领域计算错误不变成空结果或成功响应。

在线行情默认使用真实 **AKShare Python SDK**。Rust 负责 API、数据校验、周期聚合、趋势、策略和账户；独立 Python 子进程仅调用 SDK 数据接口，不调用 Python 量化或回测逻辑。日线、复权因子、五分钟线固定读取 SDK 新浪接口；目录、日历与涨停梯队分别使用 SDK 对应接口。TDX 仍可显式选择，不作为在线失败的隐式后备。

SDK 环境沿用 `packages/wavequant-core/pyproject.toml` 的 `akshare` 可选依赖。默认寻找本项目 core 虚拟环境；也可在启动 Rust 前设置 `WAVEQUANT_AKSHARE_PYTHON` 为该环境 Python 的绝对路径。每个 Rust 服务进程惰性启动并保留一个常驻 SDK 进程，所有 provider 实例共享，日线和复权等请求复用已导入的 SDK。最多接收 4 个在途请求（含等待），通过独占管道串行调用；等待及调用共用 `--akshare-timeout` 总预算。响应上限20 MiB，超时、退出或协议异常时回收，后续请求重建，不自动重试当前请求。返回元数据仍使用 `transport=python_sdk_process` 和真实 `sdk_version`，来源摘要包含常驻协议与适配器源码。服务关闭后管道EOF使空闲SDK进程退出。

保留的业务包括：

- Python catalog、view、stock-view、summary、theory、TDX/AkShare 行情和多周期读取。
- 结构/买点扫描快照、涨停梯队、后台任务、取消、进度、预览和任务历史。
- 自选分组、导入、修订冲突、设置、刷新队列与重试。
- NestJS 用户创建、邮箱唯一性、数据库健康、进程健康和 OpenAPI。
- 原 CLI 索引、结构/信号刷新与多周期预计算。

`/health/live` 仅代表进程存活；数据库/缓存及研究运行状态有独立接口。步骤诊断从实际每日计算观察器记录，图形与预览投影同一份正式结果。未配置基础设施或缺少行情时返回明确不可用状态。

## 趋势级别与 K 线周期

`level` 与 `timeframe` 是两个独立维度。同一个 `timeframe` 内按照同一核心规则依次计算：

| 结构 `level` | 含义 | 输入 |
| --- | --- | --- |
| 0 | 技术折线 | 当前周期的 K 线 |
| 1 | 一级趋势 | level 0 的技术折线高低点 |
| 2 | 二级趋势 | level 1 的趋势结果 |
| 3 | 三级趋势 | level 2 的趋势结果 |

`timeframe=1d/1w/1mo/3mo/1y` 分别为日、周、月、季、年 K 线。例如 `timeframe=1w` 仍然计算周 K 线内部的 level 0、1、2、3，不会将一级、二级、三级分别映射为日、周、月。API 先从同源日线聚合目标周期，再以该周期身份创建一套独立分析器和缓存。非日线返回 `trading_applicable=false`，保留趋势结构，不执行原日线交易规则。

`buy_interval_level=1/2/3` 选择日线策略使用的趋势资格级别。旧 `/api/structure-signals` 的查询过滤器 `trend_level=0` 保留 Python 协议的“全部 1–3 级趋势”含义；它是筛选器哨兵，与分析结果中的结构 `level=0`（技术折线）不同。结构信号记录的 `trend_level` 仍为实际的 1、2、3。

## 启动参数

| 参数 | 默认值或边界 |
| --- | --- |
| `-h` / `--help` | 显示参数帮助并退出，不启动服务或访问数据 |
| `--listen` / `--port` | `127.0.0.1:8010`；只接受回环 IP 和非零端口 |
| `--root` | 读取 `WAVEQUANT_RESULTS_ROOT`；未设置时不启用封存研究与自选功能 |
| `--tdx-root` | 未设置；显式指向含 `vipdoc`、`T0002` 的 TDX 根目录 |
| `--web-root` | 未设置；提供静态站点目录后启用文件服务 |
| `--api-only` | 关闭静态文件服务 |
| `--web-url` | 未设置；API 根路径可跳转到指定回环 Web 地址 |
| `--allow-origin` | 可重复传入精确回环 HTTP Origin，必须含端口 |
| `--disable-akshare` | 默认在线源启用；该标志关闭在线行情 |
| `--akshare-timeout` | 30 秒，支持 1–60 秒的有限小数 |
| `--quant-worker` | 未设置时在当前进程计算；设置时必须为量化 CLI 可执行文件的绝对路径 |
| `--quant-workers` | 2，范围 1–8；只在配置 worker 路径时启用进程池 |
| `--record-backtest-diagnostics` | 默认关闭；开启后保存实际逐日步骤明细 |
| `--no-watchlist-refresh` | 仅本进程停止自动提交自选回测；持久化设置和手动接口不变，省略时沿用原自动刷新行为 |

管理动作互斥：`--check-infrastructure`、`--init-database`、`--index-runs`、`--refresh-structures`、`--watch-structures`、`--refresh-signals`、`--watch-signals`、`--refresh-timeframes`、`--watch-timeframes`。`--precompute-timeframes` 是 `--refresh-timeframes` 的别名。索引和快照发布需要配置 `WAVEQUANT_DATABASE_URL`；刷新由 CLI/Worker 驱动，HTTP 快照端点保持只读。

| Worker 参数 | 默认值或边界 |
| --- | --- |
| `--structure-run` | 使用实际封存目录中最近完成的运行 |
| `--structure-variant` | `lecture_v1` |
| `--structure-asof` | 按真实来源解析最新交易日 |
| `--structure-refresh-interval` | 300 秒，范围 60–86400 秒 |
| `--signal-family` | `all`；可选 `all/buy/structure` |
| `--signal-source` | `akshare`；可选 `tdx/snapshot/akshare`；snapshot 只支持 buy |
| `--signal-symbol` | 可重复，限定 AkShare 股票；在线 buy 刷新必须提供 |
| `--signal-scenario` / `--signal-start` | `base` / `2018-01-01` |
| `--signal-shard-count` / `--signal-shard-index` | 1 / 0；总数 1–16，索引从 0 开始；仅用于 AkShare 全目录 structure |
| `--timeframe-source` | `akshare`；可选 `akshare/tdx` |
| `--timeframe-symbol` / `--timeframe-asof` | 可重复股票范围 / 最新目录日期 |
| `--timeframe-shard-count` / `--timeframe-shard-index` | 1 / 0；总数 1–16，索引小于总数 |
| `--n-target-trend-confirmation-enabled` | 可传 `true/false`；单独传标志表示 true；省略时串行生成两种配置 |
| `--ordinary-trend-completion-enabled` | 同上；两个开关都省略时生成四种组合 |

周期预计算对每只股票只读取一次同源日线，再生成五个周期；每个周期内仍有四个结构级别。Watch 模式沿用最后一次已发布快照，失败记录具体来源错误；Ctrl-C 传播取消标记，停止后续计算和发布。

## 接口与数据边界

请求字段概览由 `GET /api/docs-json` 返回，`GET /api/docs` 提供同源浏览页。主要接口映射：

| 接口 | 实际来源与职责 |
| --- | --- |
| `GET /api/catalog`、`/api/health`、`/api/view` | `operations.sqlite` 哈希链、封存 manifest、研究 CSV/JSON；view 的 strict/proxy 保留组合结果语义 |
| `GET /api/stock-view`、`/api/stock-summary`、`/api/theory` | 封存日线上的独立个股账户或该次分析的趋势投影 |
| `GET /api/tdx-catalog`、`/api/tdx-view` | 本地 TDX；view 为原始不复权行情 |
| `GET /api/akshare-catalog`、`/api/akshare-view` | 交易所证券目录、Sina 原生 HTTPS；view 为同源原始行情 |
| `GET /api/market-bars`、`/api/market-timeframe` | 带版本约束的年度分页、同周期行情与结构快照 |
| `GET /api/tdx-theory`、`/api/akshare-theory` | 相同来源和周期的结构投影 |
| `GET /api/tdx-backtest`、`/api/akshare-backtest` | 同源因果复权、分钟数据、统一策略和真实账户；附带同次分析的 theory |
| `GET /api/backtest-version`、`/api/backtest-job`、`/api/backtest-jobs`、`/api/backtest-preview` | 引擎/参数身份、幂等任务、持久历史与已完成交易日前缀预览 |
| `GET /api/structure-signals`、`/api/buy-signals` | 原子发布的 SQL 扫描快照，Redis 仅作缓存 |
| `GET /api/limit-up-ladder` | 东方财富指定交易日涨停池 |
| `GET/POST /api/watchlists` | SQLite 分组、成员、设置、导入及自动刷新 |
| `POST /api/v1/users` | PostgreSQL 用户创建和邮箱唯一性约束 |
| `GET /health/live`、`/health`、`/api/infrastructure/health` | 进程、用户数据库、研究数据库/Redis 健康分别返回 |
| `POST /api/trend-analysis` | 显式 identity + bars，按当前周期逐 K 线传播 level 0→1→2→3 |

`akshare` 保留原 API 的源选择名称；实际返回 `upstream=sina`、原生 provider 版本和源指纹，不冒用 Python 包版本，也不跨供应商拼接日线或分钟数据。北交所目录可返回，但固定 Sina 通道不提供北交所历史行情。TDX 回测需要 gbbq 与可判定的股票元数据；缺少复权或历史状态证据时明确拒绝成交计算。分钟缺失只在策略执行配置允许时使用显式日线回退，完整分钟数据内部不一致会报错。

任务容量为 4 个并行计算，同股票运行中的请求互斥；相同 job ID 与参数幂等。HTTP 断开后已有回测任务继续。内存完成结果保留至多 24 个、15 分钟；轻量任务历史至多 256 条、7 天。回测结果缓存位于研究根目录 `.rust-computation-cache/`，容量上限 4 GiB，绑定来源版本、实际配置和引擎身份；详细诊断位于 `.calculation-steps/`。不完整结果不会作为有效缓存或扫描快照发布。

四级结构前缀另存于研究根目录 `.rust-structure-cache/`，使用有容量预算的 ArtifactCache；目录仅由服务配置生成，不接受 HTTP 路径参数。串行与子进程共用同一结构缓存，开启或关闭详细诊断均可命中。结构身份包含引擎、行情前缀、计算参数和 `timeframe`，同周期内保留 level 0–3；命中只复用结构状态，交易判断与账户仍逐日执行。结构缓存不可用或无匹配时继续同一核心计算，不返回伪造结果。完整账户结果缓存与结构前缀缓存分别管理。

`complete` 表示该次输入前缀已计算完成且没有已知不可用规则；它不表示编译、测试或 Python/Rust 行为一致性已经验证。图表 `counts` 消费领域层真实诊断，`event_counts` 单独表示可见审计事件频次；两者不互相冒充。正式结构、交易资格线与发展尾部独立返回，显示层不重跑趋势判定。
## 状态

当前为 MONOREPO-362 原生业务迁移，完整功能等价仍待验收。按用户启动要求执行必要编译和只读连通确认，未主动运行测试、回测、lint、格式或浏览器；数据库集成和账户数值等价未验证。默认在线行情已切换为真实 AKShare SDK。旧骨架 MONOREPO-361 的检查记录不适用于本轮。接口、来源、目录及剩余边界见 `docs/rust-migration-inventory.md`，最新启动记录见本目录 `progress.md`。


### 当前结构结果索引协议
`analysis[symbol]` 使用 `schemaVersion: 1`。`levels[0..3]` 为折线/一级/二级/三级，`state.trendData[level]` 和 `state.dynamicDataIndex[level]` 仍是本层实际 points 数组的位置。

每层保存一个点表，采用等长列 `barIndices`、`kinds`、`knownIndices`、`ordinals`、`paths`、`states`、`sourceIndices`。`barIndices`/`knownIndices` 为同次返回 bars 的下标；`sourceIndices` 为紧邻下层点表下标，无下层匹配的辅助点为 null。`points`/`publication` 为本层点表索引数组，`candidate`/`key`/`anchor`/`precedingStart` 为本层点表索引或 null。因此不要把 points 的数组位置、点表下标和行情下标混用。辅助点附在点表尾部，不插入结构 points。

结果只含索引、点类型和有限状态，日期和高低价格从已有 bars 读取；不保存 source/proof/definition 的完整嵌套对象。Worker 传输与完整结果缓存共用此类型。当前图表使用服务端只读坐标适配器，保留原有绘图接口，不在前端或适配器重新判断趋势。算法求值阶段的内部 KernelPoint 暂时仍保持原实现，索引结果不用于恢复交易资格证据。

本地 `cargo build` 默认对两个 Rust workspace 包启用 dev opt-level=2，保留调试信息和溢出检查。封存文件校验摘要按规范路径、长度、修改及创建时间复用，元数据变化后重新哈希，每次仍与清单摘要比较；首次读取仍执行完整校验。


### 已复权行情缓存

复权日线改为 Redis `market-kline-v2` 无物理TTL缓存。真实交易日历控制盘中新鲜度与16:10后的收盘对账，默认512 MiB / 2048项，按真实访问LRU淘汰；复权因子或历史数据修订后完整重建，数据源切换隔离，历史请求仅读取自己的可见前缀。配置、故障处理及验证边界见 [K线缓存设计](docs/kline-cache.md)。本地 Docker 端口6380，Rust 启动时设置 `WAVEQUANT_REDIS_URL=redis://127.0.0.1:6380/0`。

`performance.analysis_breakdown` 对新计算返回分析进程内阶段秒数：input_preparation、polyline_scan（含进度发送）、polyline_finalize、level1_total、indexed_result，以及 level1.* 子阶段。level1_total 包含对应子阶段，不能重复相加；总计还包括析构和阶段间开销。当前一次性结构入口不生成未消费的 publication，不运行来源指纹/完整帧复用缓存，也不复制被丢弃的层级返回值，仍调用共用趋势内核。


### 索引数据协议 v14
API数据响应使用 `wavequant.indexed.v1`，结构为 `$format/root/nodes`。`nodes[i]` 为 `[kind,payload]`：0空、1布尔、2数值、3文本、4子节点下标数组、5字段名和子节点下标的二元组数组。引用必须小于所属节点下标；重复点、证据及行情字段复用节点。所有HTTP数据返回 `x-wavequant-data-format` 头，标准OpenAPI文档和204存活接口保持原格式。
请求可使用普通JSON或同一节点协议。Web的统一读取入口按需生成共享只读视图，实际绘图库读取最终时间/价格坐标；缓存保存节点表。协议定义见 `packages/contracts/src/modules/indexed-document.ts`，浏览器读取器见 `apps/webs/wavequant-web/public/indexed-document.js`。计算规则、同日顺序和已知时钟不因传输格式改变。


### 精简趋势状态响应
当前仅折线与一级趋势的计算分支返回 `schemaVersion: 1` 的普通 JSON：`symbol`、`timeframe`、`dataVersion`、`complete`、`unavailableRules`、`state`、`timing`。`state` 包含 `version/currentIndex/dynamicDataIndex/enabled/trendState/data`；`data` 是折线、一级、二级、三级的点索引数组。HTTP 不返回内部 `trendData`、K线和 `theory`，也不再包装成通用JSON节点表。后端内部检查点/Worker协议保持不变。

K线通过 `GET /api/trend-bars?data_version=<dataVersion>` 单独读取。该接口读取计算所用的精确复权快照，提供 `ETag` 和私有缓存；快照被有界缓存清理后返回 `410 TREND_BARS_EXPIRED`，需重新计算，不能拼接普通不复权行情。`path` 仅在非零时出现以保留中断；`referenceOnly: true` 是上层需要而本级不绘制的辅助端点。所有数组索引从0开始，`dynamicDataIndex`属于本级数组；确认时钟仍属于K线数组。

权威跨端契约：`packages/contracts/src/modules/market/trend-analysis.schema.ts`。前端只按索引读取并投影坐标；不运行另一套趋势算法。普通行情浏览及封存研究协议不在本次切换范围。


取数观测：结构化日志 `market_source_timing` 记录日线、因子、本地复权及SDK边界耗时；`market_cache_timing` 记录实际缓存命中/来源和编码写入。SDK roundtrip包含其内部HTTP与Python处理，不能视为纯网络耗时；各字段存在包含关系，不能把总段与子段相加。计时不进入行情指纹或前端DTO。
