# WaveQuant Rust Quant

趋势调度、判断、偏移、六态、买点与卖点的 Mermaid 文档见 [Rust 趋势、买点与卖点流程](../../../docs/trend-analysis-flow.md)。涉及逻辑变化时须按该文档及根 AGENTS.md 同步维护。

AKShare 数据进程已改为常驻复用：每个Rust进程首次数据请求时启动一个，随后日线、复权、目录等实例共享它；请求串行，等待与调用计入原超时预算，最多4个在途请求。超时或协议异常会终止进程，下次请求重建。Python源文件不变，Rust负责逐行协议和生命周期；数据身份包含协议源码。

在线数据默认由 `data::akshare::AkShareProvider` 调用真实 AKShare Python SDK；Rust 保留全部趋势、策略、账户和研究计算。设置 `WAVEQUANT_AKSHARE_PYTHON` 指向安装 core 的 `akshare` 可选依赖的隔离环境。Python 数据进程只接受白名单接口请求，不加载 Python 量化服务；超时、并发和响应大小由 Rust 限制。显式 TDX/BaoStock 数据源保留，旧原生 HTTP 方法仅作为显式兼容实现，不用于默认在线 API。

原生量化库与 CLI，负责趋势、策略、执行账户、数据适配、研究和本地运行治理。HTTP 服务通过本 crate 公开入口使用这些规则。

具体方法来源为 `packages/wavequant-core/src/wavequant/`。主流程按照用户 TrendAnalyzer 草稿组织：逐根消费已知行情，`runNextLevel=true` 在当前根立即进入下一级。折线为第 0 层；一级、二级、三级调用同一核心，分别读取下层真实结构。方向发布证书、交易资格与结构分别保存，展示端只投影已有结果。

`level` 与 `timeframe` 是独立维度：一套日 K、周 K 或月 K 各自计算完整四层，不把一级理解为日线、二级理解为周线或三级理解为月线。选择 `timeframe=1w` 时，数据链始终是「周 K → 折线（0）→ 一级趋势（1）→ 二级趋势（2）→ 三级趋势（3）」，层级推进不会切换周期。日线交易方法的适用范围仍遵循 Python 来源，非日线结构不因此跳过。

## 目录

| 目录 | 职责 |
| --- | --- |
| `src/analysis/analyzer.rs` | 游标、动态起点、四层调度、来源版本缓存、状态恢复 |
| `src/analysis/kernel/` | 折线、统一趋势、普通/特殊翻转、六态、生命周期与发布证书 |
| `src/analysis/market_state/` | K 线关系、量价、市场状态、强弱、洗盘和转势 |
| `src/analysis/trading/` | N 目标、资格、轧空、主控波浪、入场仲裁及退出规则 |
| `src/analysis/system.rs`、`intraday.rs` | 日线/分钟装配、纯信号入口、实际账户与冻结信号执行 |
| `src/data/` | TDX、股本变动、Sina、BaoStock、目录、分钟缓存、CSV/JSON、PIT 资料 |
| `src/execution/` | 资金、仓位、费用、T+1、撮合、持久意图、纸面交易和报告 |
| `src/analytics/` | 研究任务、指标、持仓回撤、证据、数据诊断与结果导出 |
| `src/persistence/`、`src/governance/` | 日志、产物/步骤缓存、运行登记、告警、备份与恢复 |
| `src/infrastructure/computation/` | 原生子进程、有界队列、取消、请求身份与内存记录 |
| `src/presentation/` | CLI 参数、命令装配与输出 |
| `src/modules/market_data/` | 原有整数 OHLCV 输入检查接口 |

## 入口

在仓库根目录按需使用，以下为使用说明，本轮没有执行：

```powershell
cargo run --locked -p wavequant-quant-rs -- --help
cargo run --locked -p wavequant-quant-rs -- analyze request.json
cargo run --locked -p wavequant-quant-rs -- validate data/daily.csv
```

保留原项目的导入、backtest、research、notion-research、system-research、strategy-evidence、foundation-audit、demo、paper-demo、platform-audit、system-run/status/backup/restore/alert-ack、journal-check 和 account-report 功能。工程验收、历史模拟及恢复操作只在显式调用对应命令时运行。

纯分析入口为 `analysis::analyzer::analyze`，纯信号入口为 `analysis::system::run_system_signals`。实际账户入口支持逐日步骤观察器；预览读取同一次计算的状态，不再运行第二套趋势算法。冻结日信号执行共用完整账户风控。

单层调用遵循草稿边界：`execute_polyline_function(asof)` 只计算折线，返回本层 `data` 和上一个趋势起点 `nextOffset`；调用方通过 `execute_level_from(level, asof, &parent.data, parent.next_offset)` 显式传给直接上一级。偏移是返回的本级 data.points 数组中的下标，与原始K线坐标独立。每级保存偏移之前的来源与运行状态，消费偏移后的父级尾部；首次初始化或冻结区来源修订可重建一致前缀。结构变化推进上层，交易门禁单独判断；每一级函数不自行调用其他级别或账户。

折线和三级趋势先取得核心结果 `preceding_start`，再定位这个点在本层 `data.points` 数组中的位置作为偏移。`KernelPoint.index` 仍为原K线坐标，两者不可混用。例如前趋势起点来自第120根K线、但在折线数组中位于第8项，则返回偏移8，一级读取折线数组 `[8..]`；其自身返回的偏移则属于一级数组。尚无前趋势时返回0初始化，已声明但不在本级数组中的前趋势起点报错。`trendData`、`dynamicDataIndex` 与 `nextOffset` 均使用各自级别数组位置。

默认讲义折线保留 `PolylineCursor`，三级共用 `TrendCursor` 状态机。原始结构按来源身份复用，当日许可与确认仍按可见行情更新。v8 源码尚未编译/测速，不能据此宣称完整回测已线性化或已提速；既有服务需按后续启动安排加载。

应用可通过 `TrendAnalyzer::set_structure_cache` 或 `run_system_backtest_with_cache` 注入受信任结构缓存。同一股票、周期、行情前缀、规则/引擎版本与配置命中后，直接复用完整四层结构及其正式证书，跳过对应结构计算；交易资格、分钟观察与账户仍逐根推进。`structureCache.hits/misses` 记录本次真实读取结果。此缓存与仅节省诊断编码/写盘的 24 类步骤缓存分开，HTTP 提交的 state 不提供受信任结构事实。

CSV/JSON 保留可空的原始 ST、交易状态和收盘成交权限，不把未知值改写为已知可交易。分钟数据要求和日线的来源、版本、日期、时段、OHLC 与成交量一致。缺分钟的日线回退由配置显式启用，并记录其执行模型。

## 状态与边界

当前功能为 MONOREPO-362，完整业务等价待验收。用户要求启动后已执行必要编译与启动连通确认；未主动运行测试、回测、格式、lint 或浏览器。最新默认行情改用 AKShare Python SDK，具体执行记录见本目录 `progress.md`。MONOREPO-361 的骨架检查记录不代表本次迁移结果。详细映射见 `docs/rust-migration-inventory.md`，状态见根 `feature_list.json`。

本轮保留已有 Python 实现。Rust 执行账本与通用事件账本使用独立表及哈希协议，不向 Python 原账本混写。合成数据始终标为工程夹具；证券主数据、交易日历或分钟数据不完整时不补造事实。备份、恢复与研究导出使用新目录并拒绝覆盖既有结果。


### 当前结构结果索引协议
`analysis[symbol]` 使用 `schemaVersion: 1`。`levels[0..3]` 为折线/一级/二级/三级，`state.trendData[level]` 和 `state.dynamicDataIndex[level]` 仍是本层实际 points 数组的位置。

每层保存一个点表，采用等长列 `barIndices`、`kinds`、`knownIndices`、`ordinals`、`paths`、`states`、`sourceIndices`。`barIndices`/`knownIndices` 为同次返回 bars 的下标；`sourceIndices` 为紧邻下层点表下标，无下层匹配的辅助点为 null。`points`/`publication` 为本层点表索引数组，`candidate`/`key`/`anchor`/`precedingStart` 为本层点表索引或 null。因此不要把 points 的数组位置、点表下标和行情下标混用。辅助点附在点表尾部，不插入结构 points。

结果只含索引、点类型和有限状态，日期和高低价格从已有 bars 读取；不保存 source/proof/definition 的完整嵌套对象。Worker 传输与完整结果缓存共用此类型。当前图表使用服务端只读坐标适配器，保留原有绘图接口，不在前端或适配器重新判断趋势。算法求值阶段的内部 KernelPoint 暂时仍保持原实现，索引结果不用于恢复交易资格证据。


### 2026-10-24 计算内核改为索引（v13）
- PointArena 的不可变点版本只保存行情位置、kind/ordinal、known_index、路径、状态和来源/证明点号；不保存日期或价格副本。包含关系尾部更新产生小索引记录，原有闭合点时钟不被改写。
- 折线、整波、普通/特殊翻转及六态的状态均改为 PointId；路径和区间极值树只保存来源位置。严格突破证明使用链式引用，六态临时审计不累积到全局证明表。比较只读取行情标量，while 推进；每步不构造完整点或JSON证明。
- 一级、二级、三级调用同一趋势函数；规则判定保留原行为。当前数组入口直接借用折线的点号调用一级，再从点表导出 IndexedAnalysis；无 KernelPoint 中间结果。兼容接口显式请求旧结构时才展开完整对象，既有交易/投影适配层仍消费兼容数据，不能声称整个应用都无对象分配。
- 本级数组偏移依旧为前趋势起点在该级 points 中的位置；全局 PointId、行情位置与本级数组位置不混用。同日高低点保留 kind/ordinal。
- 二级、三级、交易仍停用，Python未改。新增三级索引/兼容证明、来源引用、行情读取和不可变时钟回归源码，未执行；本轮不回测、不测速、不打开浏览器。按用户默认要求执行启动编译和重启，结果另记，性能幅度和数值等价待验证。


### 2026-10-24 全链路索引引用（v14，待验证）
- KernelPoint 改为共享不可变点版本引用；关键位、交易/资格状态、历史帧和各级缓存的 clone 只复制引用。字段读取是同一记录的视图；修改建立新版本，旧记录及已知时钟保持不变。
- Rust JSON 证据使用带编号的共享节点DAG。字面量构造、serde转换与读取复用节点；嵌套更新只复制修改路径的容器下标，避免复制完整证明树。金额继续精确十进制，布尔、枚举及数字Map键保持原serde语义；外部SDK文件和SQL JSON仍按原数据协议读取。
- Worker协议schema3、产物缓存schema2、结构前缀缓存v2、HTTP数据统一wavequant.indexed.v1节点表。节点引用只指向更早节点，有数量/深度和输入边界；相同内容节点合并。既有不可变账本/封存源格式不改。
- 前端统一API、目录/周期缓存、分年行情和自选入口解码节点表；同一节点按需生成一个只读访问视图，浏览器缓存保留规范化表，旧缓存兼容。绘图库仅在消费最终坐标时读取数值，不重新计算趋势。跨端协议定义新增于contracts。
- 核心PointId、各级数组位置和行情坐标继续区分；上一个趋势起点偏移语义不变。二级、三级、交易保持关闭，Python不修改。
- 新增Rust共享引用/COW版本、Worker/缓存往返、数字键/Decimal和非法引用用例，以及JS惰性共享/不可变/缓存复用和原型字段边界用例，未运行。开发阶段没有测试、回测、浏览器、lint、格式或Git检查；启动编译与重启结果另记，未宣称结果等价或性能改善已确认。


AKShare桥接脚本归属Rust工程：`apps/servers/wavequant-quant-rs/scripts/rust_sdk_worker.py`，协议回归位于`tests/python/test_rust_sdk_worker.py`。旧Python工程不再新增源码或测试；Rust仍嵌入同一脚本并复用既有AKShare环境。
