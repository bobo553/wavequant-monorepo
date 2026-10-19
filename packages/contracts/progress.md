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
