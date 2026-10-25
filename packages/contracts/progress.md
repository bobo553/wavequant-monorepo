2026-10-25 MONOREPO-362 本轮提交准备：用户要求提交并推送。资格v25共用核心22项、索引/偏移7项、API四级投影1项及前端DTO/资格/边界/索引/时间轴18项定向回归通过；pnpm --filter @repo/contracts build含类型声明完成。API首次测试编译因ApiError缺Debug失败，补cfg_attr(test,derive(Debug))；随后旧仅折线断言与既有四级启用不符，测试改为实际finish一二三级及资格后断言third_level_only、逐级enabled=true，保留无交易观察/空数组/state空断言，修复后通过。两项仅测试变更，不改变生产API或交易规则，独立复核无阻塞。无旧Python源码或pyproject变更，不包含缓存、产物、凭据或F盘迁移配置。未运行全量lint/套件、浏览器或新股票回测；沿用此前允许记录共享工具配置限制后使用最小检查的授权。全市场及完整Python等价未验证，资格性能和既有Worker摘要问题仍留记录，整体in-progress。当前分支提交后按规则同步并合并本地main，用户本次要求推送当前分支；main不自动推送。

2026-10-25 MONOREPO-362 资格区间最终启动：Rust 必要启动编译 cargo +stable build --locked -p wavequant-api-rs -p wavequant-quant-rs 完成（2m30s，build-qualification-v22-final.log），已重启 API PID63988 / 8010，自动回测关闭。共享契约运行产物生成及前端生产启动编译完成，前端 PID44188 / 3003。以上仅为启动记录；未运行测试、回测、浏览器、性能或 HTTP 探针，资格结果、Python 等价及图表实际展示仍待验证。Python 未修改，未提交或推送，整体仍 in-progress。

2026-10-25 MONOREPO-362 Rust资格确认v22：数组快速路径按本级买点开关与空翻多事实启动独立确认；共用普通严格2/3/重突破/时间门槛及N轧空、量价、跳空规则。确认当日检查结束，结束日无资格，旧证据不能自动重开；复用六态关键位上下文，不重跑四级趋势，不执行买卖或账户。DTO增加启用/当前资格和四级紧凑索引区间，图表从确认日显示有效区域并保留确认/结束边界，历史回放屏蔽未来。现有级别选择映射独立开关，默认仅二级。版本更新隔离旧结果，Python未修改；流程图同步。新增边界回归源码未执行，测试/回测/浏览器/独立类型或lint检查待提交时验证；仅按默认约定进行启动所需编译和重启，启动结果另记。未提交合并或推送，完整迁移仍in-progress。

2026-10-24 MONOREPO-362 研究配置瘦身与自选独立：新增GET /api/research-config及共享ResearchConfigurationSchema，仅runs(id/start/end)、variants，不返回股票、sessions、rows/completed_at或静态说明；校验日志链、路径、清单摘要与report.json内容摘要，不调用run.daily或扫描其他封存文件，实际分析/完整目录仍保留原封存校验。在线初始化改用新接口；完整/api/catalog仅切换非行情历史scope时按需读取，复用进行中请求，失败保留上一个scope，忽略过期切换。首屏自选脚本删除所有研究配置/目录预取；watchlist-bootstrap自身数据就绪即构造可按需查询的股票投影、启用自身管理、接管搜索/分类/选股，不等待研究配置或图表；正式分析未就绪时保存主动选择，配置失败不清空/禁用列表。自选编辑仍使用服务器确认revision，自动分析任务仍单独需要策略配置，不把它等同列表加载。更新首屏不请求研究接口回归源码，未执行；本轮未跑测试、浏览器/E2E、回测、lint/格式、专项类型检查、Git状态差异或提交合并。按默认重启约定完成必要Rust启动编译2m55s及contracts运行产物生成，最终API71344/8010、前端73460/3003且页面预热结束；未发送新接口探针，不宣称新耗时/体积或故障场景已实测。Python/趋势/交易规则未改，整体in-progress，接口联调及历史切换待提交验证。日志build-research-config.log、build-research-config-contracts.log。

2026-10-20 · MONOREPO-334 合并前最终记录

修复提交99a1d27已通过7517560同步main cae59b0；仅session-handoff.md文档冲突，双方记录完整保留，Web/API/contracts生产代码未被同步改变。同步后pytest test_watchlists.py：25 passed（15.14s），git diff --check main通过，工作树干净。Web先前最终6项Vitest/28项Node、Hook重验、lint/类型及生产构建证据有效且未改源码，不重复运行；浏览器/E2E和在线视觉未运行。按用户授权本地no-ff合并，不推送。334完成，330保持原有in-progress。

2026-10-20 · MONOREPO-334 提交合并验证

用户要求合并本地main，不推送。Node24.16.0/pnpm10.26.2；Web Vitest6项与Node28项通过，Hook整理后3项重跑通过；lint无错误（保留6条既有环境变量警告），typecheck和生产build通过，共享contracts及基础配置构建通过。API自选文件首次24通过/1失败；旧测试遗漏main已有的两个默认字段，补齐完整预期后该项通过；自选事务模块mypy通过。首次Web类型/构建缺共享playwright配置产物，补建后通过，没有启动浏览器或运行E2E。浏览器与在线3003效果待验收，未重启共享服务。

2026-10-17 · MONOREPO-330 趋势与交易规则提交验证

用户明确要求提交并推送feat-三级趋势统一。本次提交覆盖普通升降整波结束默认关闭选项及Web/API/contracts传递、缺失初始关键位重建、独立实体反包放量清仓、轧空买入价格门槛和候选正N突破前量二选一。未改变既有失败相关业务逻辑。

Core定向pytest：test_body_volume_clear.py、test_squeeze_buy_quality.py、test_trend_missing_initial_key.py、test_trend_rebound_without_flip.py、test_lifecycle_frozen_context.py，116 passed/71.37秒，含真实国芳三级因果回归。API首次用系统Python收集因缺redis失败，改用项目现有API .venv后test_ordinary_trend_option.py为2 passed/4 subtests。Web两文件node测试首次2项fixture遗漏已有buy_interval_level默认值，补齐明确默认2并保留deepEqual完整断言后10项通过；无产品逻辑变更。pnpm --filter @repo/contracts build、pnpm --filter wavequant-web typecheck成功；Web lint为0错误/6项环境变量警告。Core python -m build --no-isolation成功生成sdist/wheel，git diff --check无错误。

定向mypy --follow-imports=silent检查attack_quality、two_t_resistance、daily_risk_histories、integrated_strategy、trend_options、trend_level_upgrades、lecture_trend共7文件，integrated_strategy仍有25处类型错误。将HEAD 6258955源码隔离归档至主目录.codex-runtime/submit-trend-rules-20261017/baseline，同配置检查已有6文件复现同样25处错误，未新增类型错误但门禁未全通过。保留此前生命周期相邻测试失败记录，本轮不重复当作已通过。不合并main；按用户指令提交并推送特性分支。未运行Web完整构建、浏览器/E2E或完整测试套件，未重启服务。MONOREPO-330保持in-progress。

2026-10-17 · MONOREPO-330 普通升降整波结束选项（待提交验证）

自选后台回测上下文新增 ordinary_trend_completion_enabled，枚举true/false，缺失默认false，与API及前端保存/恢复一致。

本轮按开发规则只阅读、编辑与记录，未运行测试、lint、类型、格式、构建、运行探针或浏览器；未提交/合并/推送。新华传媒2020-11-05至2020-11-23升级与2021-12-09所在资格区间的真实行情回放仍待提交时验证，不能以本轮代码修改宣称样本已验证。MONOREPO-330保持in-progress，既有失败记录保留。

2026-10-17 · MONOREPO-330 缓存实际耗时与字节契约（代码已修改，待验证）

回测步骤契约新增可选encoding_seconds、write_seconds、encoded_bytes、reused_bytes，对应Core缓存边界测量和Web展示；elapsed_seconds仍为共享起止跨度，不能相加为独占算法时间。字段兼容旧进度数据。

本轮未运行测试、lint、类型或构建，待提交时验证；MONOREPO-330保持in-progress，旧失败与验证记录保留。

2026-10-15 · 提交阶段验证与分支保存（未通过全部回归）

用户要求提交并推送 feat-三级趋势统一，明确不合并 main。本次保存累计实现及已知失败，MONOREPO-330 保持 in-progress；不代表完整验收通过。此前“未运行”的记录是各开发阶段历史，本段为最新提交验证结果。

2026-10-15 提交阶段：ESM、CJS、声明构建通过。

2026-10-15 · MONOREPO-330 完整行情底图与回测进度分离（代码已修改，待验证）

BacktestPreviewPublisher 从同一任务的行情源一次生成 market_bars/market_asof，节流快照同时交付完整行情和 bars 已完成前缀；asof、processed、theory 与账户权益继续对齐真实完成日。完整行情仅供显示，不传入策略、N、资格或六态计算。共享预览契约及 legacy 入口校验总长度、顺序、末日和前缀所有价格/成交量/复权字段一致，旧快照兼容。

PriceChart 通过 chartMarketBars 让 K 线、成交量、鼠标行情、导航和视窗采用完整行情；结构/资格/N/目标仍读取已完成 bars 与 asof。进度增加不再缩短 K 线或把视图拉回进度日；完成结果接管保持日期范围。准备阶段行情按年补到 2018，实时快照接管后停止，迟到响应不能覆盖结果；缓存预览清除旧 market_bars，避免切设置混用。进度文案区分 K 线数量和回测截止日，复制 K 线涨跌使用完整行情前收盘。

新增/扩展 API 发布完整行情但不提前账户、前缀口径不一致/未来成交拒绝、完整行情视窗与连续折线/B/S 更新的回归用例，仅编写未运行。按用户要求未运行测试、检查、格式、lint、类型、构建、Git 检查、浏览器或测速，无提交/合并/推送。本轮未重启正在运行的服务；API 无自动重载，需重启后端并刷新页面才使用新协议。MONOREPO-330 保持 in-progress，实际界面及新回测待验证；既有失败和未完成事项保留。

2026-10-14 · MONOREPO-330 同日交易与账户快照（代码已修改，待验证）

BacktestPreviewSchema 扩展为 phase=daily 的同日完整快照，包含真实 markers/signals/orders/trades/audit/curve 与 backtest。约束行情数、权益数、revision 和截止日一致，并拒绝未来信号、订单、成交和标记。旧市场几何快照不能被当作完整日账户快照消费。仅改 src，未生成 dist。

遵守用户要求，本轮仅阅读和修改源码、编写回归用例与维护记录；未运行测试、检查、格式、lint、类型、构建、Git 检查、浏览器、服务探针或测速。未提交、合并、推送或手动重启。MONOREPO-330 保持 in-progress，既有失败记录不变；不能用旧版通过结果代表当前改造。

2026-10-14 · MONOREPO-330 实际进度、非遮挡预览及刷新保持视图（实现待验证）

新增BacktestPreviewSchema及TBacktestPreview，定义job/revision和可空市场前缀，约束processed/total、K线数量、尾日和理论asof一致；市场模块公开导出。仅修改src，未生成dist。前端legacy入口同步校验任务、股票、周期、前缀数量和行情字段。按用户要求未运行类型、构建、测试或其他检查；实际消费待验证，未提交、合并、推送，330保持in-progress。

2026-10-13 · MONOREPO-330 新增BacktestPipelineSchema及步骤schema：固定24项且编号唯一有序，定义计算/缓存状态、数量、耗时和错误，市场模块公开导出。只修改源码；遵守用户要求不运行类型/构建/检查/测试，不生成dist。API与legacy Web消费加法协议，Web对显示输入做边界守护；消费者验证待提交阶段。未提交合并推送。

2026-10-12 启动当前分支：仅为页面运行用esbuild从当前src生成本worktree忽略的dist/index.mjs与index.cjs，包含buy_interval_level；没有运行DTS、类型检查、单测、lint、格式或正式包构建。用户已取消合并，未提交/合并/推送。此前待验证项保留。

2026-10-12 最新开发 · MONOREPO-330 回测设置级别契约

WatchlistSettingsSchema.context增加buy_interval_level；BuyIntervalLevelSchema为协议字符串1/2/3，缺省2，导出推导类型TBuyIntervalLevel。旧自选设置保持可读，数字/非法字符串不被宽松转换。API使用一致的参数边界，Web保存/恢复与后台任务沿用所选级别。

Web消费者schema兼容用例只写未执行。开发阶段未运行契约构建、类型或测试，未更新dist产物，未提交/合并/推送；消费者与构建验证待实际提交准备。MONOREPO-330仍in-progress。

MONOREPO-326（2026-10-11）：新增MarketBarSchema/MarketBarsViewSchema/MarketBarsPageSchema及推导类型，校验年度坐标、固定全截面身份、SHA256版本、真实可用年降序和分页连续性。允许历史截面后的同年已知交易日，React运行时安装同一Zod解析器供public年度loader使用，移除手写重复响应校验。共享契约构建、5项契约回归与Web消费者类型及生产构建通过；真实14页/2995bars共享schema与实际loader组合校验通过。无新增依赖或价格口径变化。

MONOREPO-306/307（2026-10-09，合并阶段V110）：用户最新授权合并main并推送。已同步main d2f35d8并保留逐方向确认和同波唯一展示；N选项仍默认关闭，启用时独立N新周期和完整证书波身份生效。Core198/238项及23子用例、API65项及35子用例、Web93项与19拓扑/组件、相关类型及构建通过。main全部302个功能对象完整保留，旧本任务304/305/306重新编号306/307/308；既有基线失败仍在308待修。Git合并与推送结果以当前交付和日志为准，完整套件/浏览器/外部分钟未运行。

MONOREPO-307（2026-10-09）：WatchlistSettingsSchema.context新增n_target_trend_confirmation_enabled，沿用请求协议字符串true/false枚举，缺字段默认false。共享契约构建及Web消费者类型、保存/恢复、后台队列定向回归通过；任务Web依赖指向本任务契约产物，主检出依赖未修改。

MONOREPO-298（2026-10-08）：新增WatchlistSnapshotSchema、WatchlistSettingsSchema、WatchlistDocumentSchema与类型，统一自选股票、分类、顺序、修订、回测来源/参数及暂停字段。Web运行时复用Zod校验，服务端输入另有边界和数量校验；契约构建、Web与Nest API消费者类型通过，9项Web存储/契约与后台观察回归通过。无新依赖。

# Progress

2026-10-24 MONOREPO-362 偏移续算：KernelOptions.trend_offset_enabled默认true，Rust backtest查询同名参数false可关闭，参与规范化任务/结果缓存身份，@repo/contracts导出TrendExecutionOptionsSchema。直接下级数组全局偏移转换为路径内已确认/已知点偏移；每路径一个偏移前检查点及末尾状态，核对保留前缀后恢复，纯追加从末尾续算，尾部修订从检查点包含起点重算，早期修订/无状态/配置变化/关闭均重建。来源兼容边界只保留相同前缀点号，修订尾部重新导入；三级共用函数。整波前起点不再被六态key覆盖，折线六态单独记录方向起点；桂林偏移1439/120→1417/121，绘图点/六态/行情版本与修改前一致，开关两模式完整state一致。固定1455已确认折线点5次中位数：8次尾部修订15.083→5.300ms少64.9%，修订主循环11640→304点（每次38点）；16次追加36.647→11.970ms但旧代码已有追加游标，不把全部收益算新功能。冷算均1455点，早期修订仍全算。API各3次新计算分析关闭65.326/开启73.767ms，后端662.830/636.315ms，HTTP800.336/795.047ms；首次无续算状态，没有分析提速，不能把网络/保存波动归因偏移。当前API每次新建Analyzer，本轮无跨请求状态缓存，页面首次不获得尾部65%收益。按用户本次要求执行差分检查及基准，三级完整结果等价；未跑完整测试/浏览器/E2E/lint/格式/类型/Git检查，contracts未构建待提交验证。启动构建完成，Windows文件占用排除后差分重试成功并恢复API51616/8010，前端3003继续运行、自动回测关闭；Python未改，未提交合并，完整迁移仍in-progress。设计及证据API-rs/docs/trend-offset.md和.codex-runtime/rust-startup/trend-offset-verification.json。

## Current State

`@repo/contracts` 是跨应用数据结构的唯一事实来源。

## Completed

- 纳入 workspace 级进度与 Harness 管理。

## Verification

- `pnpm verify`：lint、类型检查、测试和包构建通过。

## Risks and Next Steps

- 契约变化需同步验证 API、Web 与 Mobile 消费者。

- MONOREPO-153：新增LimitUpStockSchema/LimitUpLadderSchema及推导类型，Web复用验证API响应的日期、来源、连板数和可空数值。包构建和消费者Web类型/构建、真实接口浏览器解析通过。
2026-10-17 · 本次提交验证补充：contracts build 通过；功能随 MONOREPO-330 保持 in-progress，端到端资格表现和 2120 日性能待验收。
2026-10-20 · MONOREPO-334 自选成员增量契约（待提交时验证）

移除WatchlistSnapshotSchema总计20000条成员配额，添加WatchlistMembersMutationSchema统一add/remove、groupId与股票输入。保留股票格式、名称长度及分类数量校验。Web/API增量操作和契约超旧配额回归源码已补充；未运行构建、类型或测试，提交阶段需先构建contracts再验证消费者。


### 2026-10-24 全链路索引引用（v14，待验证）
- KernelPoint 改为共享不可变点版本引用；关键位、交易/资格状态、历史帧和各级缓存的 clone 只复制引用。字段读取是同一记录的视图；修改建立新版本，旧记录及已知时钟保持不变。
- Rust JSON 证据使用带编号的共享节点DAG。字面量构造、serde转换与读取复用节点；嵌套更新只复制修改路径的容器下标，避免复制完整证明树。金额继续精确十进制，布尔、枚举及数字Map键保持原serde语义；外部SDK文件和SQL JSON仍按原数据协议读取。
- Worker协议schema3、产物缓存schema2、结构前缀缓存v2、HTTP数据统一wavequant.indexed.v1节点表。节点引用只指向更早节点，有数量/深度和输入边界；相同内容节点合并。既有不可变账本/封存源格式不改。
- 前端统一API、目录/周期缓存、分年行情和自选入口解码节点表；同一节点按需生成一个只读访问视图，浏览器缓存保留规范化表，旧缓存兼容。绘图库仅在消费最终坐标时读取数值，不重新计算趋势。跨端协议定义新增于contracts。
- 核心PointId、各级数组位置和行情坐标继续区分；上一个趋势起点偏移语义不变。二级、三级、交易保持关闭，Python不修改。
- 新增Rust共享引用/COW版本、Worker/缓存往返、数字键/Decimal和非法引用用例，以及JS惰性共享/不可变/缓存复用和原型字段边界用例，未运行。开发阶段没有测试、回测、浏览器、lint、格式或Git检查；启动编译与重启结果另记，未宣称结果等价或性能改善已确认。

- v14最终启动编译成功3分04秒（build-index-flow-final.log）；后端PID51588/8010，存活204，AKShare和关闭自动回测设置保留。Worker请求/回复使用显式带标签消息解析，保留同一节点引用；通用serde中间类型有兼容回退，数据状态仍保存共享节点，不把serde临时对象作为持久状态。
- 前端最初C盘worktree/E盘依赖产生错误`./E:/.../next-dev.js`、首页500。将本会话前端代码复制到E:/WorkSpace/work/wavequant-monorepo/.codex-runtime/rust-indexed-web/wavequant-web专属忽略运行目录，复用同盘依赖，使用Next开发服务器；PID56580/3003，首页200。修改仍在原worktree，main受跟踪源码未编辑；静态vendor在启动时生成，版本轮询沿用既有令牌，不主动刷新浏览器或触发回测。
- 后续本会话前端重启使用.codex-runtime/rust-startup/start-frontend-indexed.ps1，重新复制src/public和配置到专属运行目录；后端继续start-backend.ps1。浏览器刷新后读取新版协议。
- 新增回归源码未运行；无浏览器、回测、测试套件、测速、lint、格式或Git检查。v13历史71%提速及内容摘要不能作为本轮v14验证。完整规则等价、浏览器交互与v14性能均待用户安排验证；整体MONOREPO-362仍in-progress。


### 2026-10-24 精简前端趋势状态DTO
- 当前折线+一级结构计算结果改为普通JSON：symbol/timeframe/dataVersion、state及timing；state保留version/currentIndex/dynamicDataIndex/enabled/trendState/data，不返回trendData、行情、内部检查点或theory绘图对象。后端完整缓存保留原计算语义，结果缓存身份升级v5。
- 新增GET /api/trend-bars?data_version=<SHA256>，读取分析时使用的同版本、同范围因果复权行情快照；不重新请求SDK或计算趋势。快照沿用有界ArtifactCache，失效返回410/TREND_BARS_EXPIRED；精简缓存命中时会确认对应行情仍可读。使用ETag和private,max-age=300,immutable。
- 前端最终结果统一从独立接口读取行情并按索引取得坐标，仅做旧图表形状适配；不重跑趋势判定。非零路径及少量辅助引用端点按需返回，避免断线被连接或上层引用丢失。交易和二/三级保持停用，Python不修改。普通行情浏览/封存研究接口仍保留原协议，本轮变更针对当前结构计算结果及任务完成结果。
- contracts新增严格DTO和行情快照校验，并由运行时桥接统一使用；新增来源索引/版本错配/首次可知时钟/动态尾点/路径/空数据回归源码，未运行。开发阶段不运行测试、回测、浏览器、lint、格式或Git检查。编译重启结果另记；实际响应体积与页面交互待验证，MONOREPO-362保持in-progress。

- 启动结果：Rust API/quant启动编译最终成功1分22秒（build-trend-state-final.log；首轮1分31秒后补齐缓存边界和OpenAPI重新编译）。最终API PID46476/8010；前端PID22608/3003，启动日志Ready。contracts首次因worktree缺少node_modules失败，复用主目录已安装依赖后运行时产物及声明生成成功；前端专属运行目录显式绑定本worktree产物，避免使用main旧契约。未安装新依赖。
- 本轮未主动发起API计算、回测、浏览器、测试套件、lint、格式、Git检查或性能采样。已打开页面的普通请求可能在后端重编译期间出现ECONNREFUSED，服务已按启动脚本重新拉起。仅记录编译/启动，DTO实际响应体积、绘图等价和运行交互仍待验证，不复用上轮1.25MB作为本轮结论。


### 2026-10-24 用户授权Chrome前端测试精简DTO
- Chrome实际打开/research?page=workspace，海洋王2133根日线，测试新计算、缓存读取和刷新。发现并修复新增分支错误上报progress(100)：JobReporter只允许0–99，完成由注册表发布；改为99。修复前端投影缺少H/L标签导致undefined。修正后的API启动编译成功并重启PID33904/8010，前端PID35644/3003。
- 新计算HTTP200：响应113014字节（gzip9825字节），较上一版历史1254764字节减少90.99%；独立行情400943字节，压缩72219字节，后续读取命中浏览器缓存。趋势本体不含bars/theory/trendData。分析108.903ms、数据读取9.3127ms、后端1385.479ms、经3003完整请求1499.3ms；仅此单次热行情样本，不冒充多次性能基准。
- 点索引：折线1405/一级133（各包含1个动态尾点），二三级0；页面绘制132个已确认一级点，截图确认K线/折线/一级实线及发展尾部显示。刷新缓存请求约306ms，独立行情约50ms；最终页面Console无error/warn，Network无失败请求。测试资金从10.002万恢复10万。
- 测试中更改资金时，自动自选settings写回因启动参数--no-watchlist-refresh返回503（自选回测调度器未配置）；此为现有独立配置路径，未改动或启用自动调度。恢复原设置并刷新后无失败请求。前端还保留普通行情预览请求，不把本次113KB说成全部网络流量。
- 仅浏览器单股结构流程测试；未运行单元测试套件、lint、格式、Git检查或完整交易验收。证据.codex-runtime/rust-startup/browser-trend-state-verification.json，整体MONOREPO-362仍in-progress。


### 2026-10-24 买卖资格门禁改造
- 2026-10-24 买卖入口改造v16：交易执行与买点等级独立，默认不执行交易回测、买点[false,false,true,false]。按最新要求仅开启级别计算资格一次，其他级别始终false并清除旧资格；先结构后汇总资格。各级买点历史隔离，卖出优先，同次账户最多一个买入；无资格撤销待买并全额清仓，保留即时全额退出及实际成交约束。分钟入口与成交前检查同步门禁。前端新增开关，共享契约/参数/缓存/资格区间展示/文档同步；三级保持关闭，自动队列只算结构，Python未改。新增回归源码未运行；本轮不测试、不回测、不浏览器、不lint/格式/额外类型检查，启动编译另记；未提交合并。

- 最终启动记录：必要共享契约构建、Next生产启动及Rust stable同版本启动编译完成；后端PID67220/8010，前端PID72388/3003，自动回测关闭。补齐跨级分钟确认输入及本级资格上下文隔离、成交前资格复核、退出与买入互斥、待执行清仓禁止加仓。没有运行测试套件/业务回测/浏览器/E2E或独立验证；新增回归源码待提交时执行，不宣称交易结果已验收。未提交或合并。


### 2026-10-24 开启三级结构
- 2026-10-24 开启三级结构v17：STRUCTURE_MAX_LEVEL=3，数组与逐日入口共用趋势函数，三级读取二级PointId及二级数组前趋势起点偏移。更新缓存版本、enabled四级状态、进度及结构模式文案，三级买点可手动选择但默认false，默认买点仍[false,false,true,false]，交易及自动队列策略不变。同步共享契约、配置恢复及Mermaid；Python未改。开发阶段不运行测试、回测、浏览器或检查，必要启动编译另记；未提交合并。

- 三级结构启动完成：Rust必要启动编译完成，后端PID56240/8010；前端共享契约及生产启动完成，端口3003。自动回测保持关闭。仅启动编译与重启，未运行回测、浏览器、测试或其他业务验证，未提交合并。


- 2026-10-24 修复无账户模式跳过资格及买点：v18按已开启级别逐日运行共用确认/资格/买点逻辑，账户保持为空；精简DTO新增索引化observations，前端恢复区间和signal标识，保留真实确认时钟与结构低点。结果缓存v7隔离旧空结果；Python未修改。新增前端回归用例源码，测试/业务回测/浏览器均待验证；必要启动编译另记，未提交合并。

- 2026-10-24 启动记录：修正启动编译发现的usize日期映射问题，并停止本worktree旧进程解除Windows可执行文件锁；Rust必要启动编译完成，后端PID58276/8010，前端PID71784/3003。自动回测关闭。未运行测试、业务回测、浏览器或性能验证；资格及买点实际股票结果待验证，未提交合并。


- 2026-10-25 用户强制恢复周六20:42开始、20:48完成的版本：基于本会话原始编辑记录逆向重建，分析器VERSION14，STRUCTURE_ONLY=true，仅折线/一级数组路径，撤销二三级启用、交易门禁UI、历史资格观察及新增性能探针；Mermaid用该时段原始命令重建。保留随后用户明确要求的旧Python工程零源码改动边界（SDK桥接仍归属Rust），以及备份/审计记录。因此是基于编辑记录的恢复，未持有该时点Git提交或全目录哈希，不宣称逐字节快照一致。必要启动编译与重启单列，不运行回测、浏览器或测试。


- 2026-10-25 实际提交准备：当前恢复版仅折线及一级，完整Rust迁移继续in-progress。用户授权修复提交检查发现的问题：进度文案采用后端已处理计数覆盖旧文本；自选预览测试隔离额外旧数据迁移；补齐周期缓存TS声明，不改量化规则。Rust索引/行情/偏移定向检查10项与前端DTO/索引/状态/自选定向检查44项通过，前端产品源码及测试严格类型检查通过（隔离映射本worktree共享产物）。原始全量typecheck及lint因依赖链接指向旧产物、缺少工具配置依赖与ESLint构建器无法完成；用户明确允许记录限制后提交合并推送。不运行浏览器/E2E、回测或全套测试。暂存源敏感模式扫描未发现命中，旧Python项目源码及pyproject无差异。当前分支及main均获准推送，无强推。
