# 通达信主控波浪量化研究

可运行的本地日线研究核心：通达信导入 → 数据检查 → 因果信号 → 共享资金组合回测 → 时间切分/基准/消融/成本压力 → HTML 和明细报告。**这是研究工具，不连接券商，不下单，不保证盈利。**

本项目位于 monorepo 的 `packages/wavequant-core`，只负责领域、研究、回测、数据与运维能力，不启动 HTTP Server，也不读取 Web 静态资源。HTTP 适配器位于 `apps/servers/wavequant-api`，浏览器界面位于 `apps/webs/wavequant-web`。

源码先按 `domain → application → infrastructure/interfaces` 划分依赖方向，再按市场结构、市场状态、策略、研究、交易、运维、行情和持久化等功能子域组织。完整目录职责、规范入口和关键领域不变量见 [分层架构说明](docs/architecture.md)。仓库代码统一使用最细功能模块；已移除扁平子模块入口，稳定的包级公共 API 仍可从 `wavequant` 导入。

## TradingView 可视化工作台

```powershell
# 从 monorepo 根目录启动 Web + API 工作台
pnpm --filter wavequant-api dashboard
```

打开 `http://127.0.0.1:8765`：K 线与理论结构、历史回放、成交定位、净值／回撤／仓位、订单／信号和只读运行状态。API 使用本包提供的只读查询服务，Web 使用本地 TradingView Lightweight Charts 5.2.1；严格版与代理版分开，回放不返回未来退出盈亏。AkShare 当前股票可复用相同策略与结构算法生成只读信号证据，但不模拟订单、成交或在线全市场扫描。详见 [可视化契约与验收说明](docs/visualization.md)。

## v0.4：统一运行、故障验收和恢复

```powershell
.venv\Scripts\python.exe -m wavequant.interfaces.cli system-run --config configs/operations.json --run-id acceptance_20260908
.venv\Scripts\python.exe -m wavequant.interfaces.cli system-status --root results/operations_v1
```

一个入口执行：冻结输入／源码 → 全量测试 → 数据状态与新鲜度审计 → 原策略严格版／日线代理分别滚动及成本／容量诊断 → 纸面故障验收 → FIFO 账户归因守恒 → 备份恢复演练 → 产物封存。

新增 OS 排他锁、不可复用失败 ID、成功 ID 校验复用、本地告警去重与确认、可验证多文件／SQLite 备份恢复。补齐撤单投递、部分成交后拒单和独立场所公司行动记账。没有改变轧空策略规则，也没有接入真实订单。

配置、全部命令、运行边界和尚未具备的实盘条件见 [运行契约](docs/operations_contract.md)；审查记录见 [operations_review.md](docs/operations_review.md)。没有官方日历时数据新鲜度为 UNKNOWN；工程通过不等于策略有效。原十股与 v5 扩展样本报告保持不变。

## v5：可证伪的策略改进研究

```powershell
.venv\Scripts\python.exe -m wavequant.interfaces.cli strategy-evidence --output-dir results/squeeze_evidence_v5_20260908
```

协议 `configs/squeeze_evidence_v5.json` 冻结四个候选：原代理版对照、252 日结构上下文、1.0 相对量能、1.0 费用后盈亏比。后三者一次只改变一个经验参数，均保留用户的完整翻多／交替／多头趋势／轧空链条；不自动替换 v4 默认策略。严格讲义折线另作参考。

从通达信既有主板日线按首条记录日期和代码哈希固定抽取 64 只（包含原十股），先记录名单再读收益；导入失败的股票显式隔离且不补选。这个样本仍不是无幸存偏差的历史股票池。

输出开发／验证／诊断、2 倍成本、训练段独立选择与 CASH 后备、联合日期块重采样的本组多重试验诊断、相对原策略增量检验、因果前缀核验和拒绝原因。**通过统计检查也不能越过缺少历史证券资料、独立样本、真实次级路径的门禁。** 详见 `docs/strategy_evidence_contract.md`。

## v0.3：研究与纸面执行基础设施

增加 SQLite 事件账本、可重放信号、原始股数／现金订单服务、独立模拟成交场所、组合风险门禁、部分成交／撤单／重复回报／重启恢复、对账停机、备份校验、当时可知证券状态接口、数据清单与实验登记，以及固定规则滚动／成本／容量诊断。原 v4 轧空策略与旧报告不变。

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m wavequant.interfaces.cli platform-audit --output-dir results/platform_v1_20260908
.venv\Scripts\python.exe -m wavequant.interfaces.cli paper-demo --output-dir results/paper_demo
.venv\Scripts\python.exe -m wavequant.interfaces.cli journal-check results/paper_demo/account.sqlite
```

`platform-audit` 先跑完整测试，再审计现有通达信 CSV、验证纸面执行并运行原参数的历史诊断；输出目录不得已存在内容。可传入 `--security-master` 和 `--calendar` JSON，格式及运行边界见 `docs/platform_contract.md`。`--engineering-only` 只跳过历史收益诊断，不跳过真实数据覆盖审计。

**不是实盘系统认证。** 当前缺少真实历史 ST／退市股票池、官方日历输入、次级行情、独立人工事件标注、新锁箱及真实券商连接。旧日线回测仍使用独立的近似成交模型；新纸面层与其共享理论信号，不宣称两者成交逐笔一致。教学演示不使用生产入场策略，不作为盈利证据。CI 配置已提供，只有实际执行后的结果才算验收。

## 主控理论系统集成与回测

新增 `integrated_strategy.py` 接通折线、结构关键位、N、主控棒量能、三分力度、六态、洗盘和可选次级扭转；共用只做多资金与风险引擎。严格折线和日线确认拐点代理分开报告，不把缺少次级数据的路径猜成已确认。

当前默认规则：翻空为多 → 空多交替 → 确认多头趋势 → 后续新 N → **只买轧空／强轧空，不买盘坚**。完整时序和撤销条件见 `docs/transitioned_squeeze_contract.md`；执行与历史规则见 `docs/system_strategy_contract.md`。

当前 v4 保留 v3 的发单前价格预检查，并补充原文“已确认轧空、回压不破轧空低后恢复上涨”的独立入场路径，不能只靠历史标签入场。真正发单后仍在次日开盘复核费用后盈亏比与整手风险。完整定义与零成交排查见 `docs/squeeze_zero_fill_diagnosis.md`。零手原因、盈亏比数值、买入／平仓数及零成交状态均单独输出。

运行 `.venv\Scripts\python.exe -m wavequant.interfaces.cli system-research`，默认协议 `configs/system_squeeze_v4.json`，读取 `data/tdx_system_20260908.csv`，输出到 `results/system_squeeze_v4_20260908`。已有输出不会覆盖，复跑请换 `--output-dir`。原 v1/v2/v3 协议与结果保留，新回测含 v2/v3 对照。这是已看过历史的探索诊断，不是新锁箱或自动挑选高收益策略。

## 统一价格行为基础

突破、跌破、抵抗和三笔顺序的独立底层定义位于 `wavequant/domain/market_structure/price_action.py`，说明见 `docs/price_action_contract.md`。关键点必须事先确认并显式传入；分别记录盘中/收盘越过；长影默认不设阈值；第三笔只记录确认事实，不替上层判断抵抗成功或趋势反转。

可运行示例：`.venv\Scripts\python.exe -m examples.price_action_basics`。后续 N 字、六态、量能和交易模块可消费这些不可修改的观测结果；旧研究代理保留兼容行为，尚未全部迁移。

## N 形理论层

`wavequant/domain/market_structure/n_shape.py` 在已确认 A/B/C 拐点与价格行为基础层上，记录正倒 N 的同棒实虚完成、冻结轧空低/杀多高、等浪/1P/2T 以及首次达到时间。箱体锚点和达到口径必须显式选择；没有自动拐点选择、量能过滤或买卖指令，也未编造五顶十满公式。

完整定义和边界见 `docs/n_shape_contract.md`。运行示例：`.venv\Scripts\python.exe -m examples.n_shape_theory`。本轮价格 N 的事件跟踪与指标/均线的几何测幅接口分开，不能将指标值伪装成价格 OHLC。

## 六大盘态层

`wavequant/domain/market_state/market_regime.py` 消费已完成 N 字，区分抵抗出现、局部成功、失败、未知及强／普通／盘整六态。至少第三笔才确认，允许多棒回调；逐棒证据和历史确认分开，波段失效不能自动变成反向盘态。

定义、工程假设和边界见 `docs/market_regime_contract.md`。演示：`.venv\Scripts\python.exe -m examples.six_market_regimes`。长影阈值与波段边界必须显式选择；本层无量能或买卖过滤，旧策略、旧三棒代理与既有报告保持不变。

## 高低折线与趋势结构层

`wavequant/domain/market_structure/polyline.py` 定义六个基本术语、带确认时间的正负反转、普通高低连接与显式子母教学路径。缺少内外包高低先后证据时暂停；未确认端点不作为 N 锚点。

`wavequant/domain/market_structure/trend_structure.py` 定义显式窗口内的末跌高／末升低、局部与全窗口多空趋势、头底疑虑、冻结关键位翻转、严格 67% 交替及独立 abc 等浪核验。完整定义见 `docs/polyline_trend_contract.md`；运行 `.venv\Scripts\python.exe -m examples.polyline_trend_theory` 可核验折线 → N → 六态及多空交替样例。

## 主控 K 棒与波段洗盘观察层

`wavequant/domain/market_state/control_bar.py` 复用 N 完成棒的轧空低／杀多高，记录攻击量、次笔抵抗、防守盘中越过／收盘失守和收回，不把虚拟价当作真实主力成本。

`wavequant/domain/market_state/washout.py` 实现图 009 严格时序：首次 N → 1P／2T 之间 → 后续颈线／1P 之间 → 独立新 N，并提供头部镜像。两个方向都不生成订单，头部只作为持多风险观察，70% 概率保持未知。定义见 `docs/control_washout_contract.md`；运行 `.venv\Scripts\python.exe -m examples.control_washout_theory`。

## 多空力道与盘势扭转层

`wavequant/domain/market_state/wave_strength.py` 定义反弹／回档的三分与六分比例，分开反向力度和原趋势承压，保留原文未定义区间、严格边界、图 011 的互补坐标及分数／小数版本。

`wavequant/domain/market_state/market_turn.py` 连接已确认折线、冻结六态背景和末跌高／末升低：疑虑 → 第二反向幅度组合 → 最后两高／两低的次级斜线新越线。保留负扭转“或跌破末升低”分支，不从未知次级数据推断确认。定义见 `docs/wave_strength_turn_contract.md`；运行 `.venv\Scripts\python.exe -m examples.wave_strength_turn_theory`。

## 基础讲义版

根据用户提供的 35 页《主控战略N形理论》补齐了基础教材 `docs/n_foundations.md`，并增加独立的 `wavequant/domain/market_structure/foundations.py`：虚拟高低点、实/虚双锚点突破、六个 K 线术语、三棒六态代理、等浪/1P/2T、已确认拐点、力度分层和正负扭转证据。

旧策略和历史回测协议未修改。基础规则核验可执行：

```powershell
.venv\Scripts\python.exe -m wavequant.interfaces.cli foundation-audit data/tdx_rerun_20260908_01.csv --output-dir results/foundations_lecture
```

该命令先运行测试，再生成每股日线标注和汇总；它不生成交易收益。母子线盘中顺序、自动颈线选择和主力身份推断不在本基础接口的能力范围。

## Notion 笔记版（2026-09-08）

25 页正文已全部阅读（包括「主控 → 波浪1」），已查看唯一随包图片；120 个外链图片/公式尚未完成视觉核验。逐页映射、定义冲突、实现与暂缓项见 `docs/notion_review.md`。

使用已有通达信快照，一条命令跑自动测试和全部 12 个固定对照组：

```powershell
.venv\Scripts\python.exe -m wavequant.interfaces.cli notion-research
```

输出 `results/notion_v2/report.html`、`report.md`、完整参数/源码哈希/逐笔/净值及 `notes_inventory.json`。可用 `--notes-dir` 指定导出目录，`--output-dir` 换目录保留不同运行；本命令不联网、不重新下载行情、不覆盖旧版 `results/tdx`。

已实现因果 N 字结构、突破后抵抗失败、固定潮汐/瞬爆目标、量能三取二、MA66、已完成周线 13/26 和费用后盈亏比对照。**历史诊断期严格 N 基础组 +0.52%，但只有 1 笔交易；所有新组未达训练交易数门槛，无胜出策略。** 不将少交易、低仓位造成的低回撤包装成好策略。

`configs/notion_protocol.json` 固定本次日期和候选；由于旧研究已看过这些历史，这不是新的锁箱。未来数据须另行冻结协议评估；本命令拒绝将 2026-09-09 起的数据混入旧诊断，不能靠修改旧协议来声称前瞻。

## 一键运行（Windows）

在项目目录执行：

```powershell
.\run.ps1 -TdxRoot D:\TDX
```

首次需要 Python 3.11+；脚本创建本项目 `.venv`，缺少依赖时安装 `.[tdx]`，随后自动执行测试、只读导入和研究。已有依赖和数据时不需要联网。若系统禁止运行 PowerShell 脚本，无须修改安全策略，直接执行：

```powershell
.venv\Scripts\python.exe -m wavequant.interfaces.cli run-tdx --tdx-root D:\TDX --output-dir results\tdx
```

新环境手动安装：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[akshare,dev,tdx]"
```

`requirements-lock.txt` 记录本次 Windows/Python 3.14 的确切依赖版本；跨 Python 版本优先使用上面的最小依赖安装。`akshare` extra 仅供 HTTP 适配器按需读取在线 A 股目录与不复权日线，Core 导入时不会联网。每次运行写入指定结果目录，覆盖同名生成报告，不修改通达信文件。需要保留旧实验，请换一个 `--output-dir`，导入快照也可用 `--csv data/tdx_日期.csv` 分开保存。

## 看哪里

- `results/tdx/report.html`：双击打开的静态中文报告和测试期净值曲线，无外部脚本。
- `results/tdx/report.md`、`report.json`：可读报告和完整机器可读结果。
- `results/tdx/test_log.txt`：这次流水线实际运行的自动测试日志；测试失败则停止研究。
- `results/tdx/data_quality.json`：覆盖、缺失交易日及已有独立缓存的重叠 OHLC 核验。
- `results/tdx/training_grid.json`、`protocol.json`：所有训练候选和预先声明的筛选方法。
- 各策略/区间目录：`equity.csv`、`trades.csv`、`orders.csv`、`open_positions.json`、`metrics.json`。
- `data/tdx_daily.metadata.json`：原始 `.day` 与 `gbbq` 哈希、实际首末日期、价格口径及限制。

报告分别标注工程测试与策略研究门槛。**即使测试期为正，也可能因验证期、成本敏感性或风险收益表现未达标而判定未通过。** 负结果不会被隐藏。

## 其他命令

```powershell
# 只导入（默认十只成熟沪深主板样本；显式指定可更换，禁止事后按收益选股）
.venv\Scripts\python.exe -m wavequant.interfaces.cli import-tdx --tdx-root D:\TDX --csv data\tdx_daily.csv

# 只校验 / 只运行既有快照，不重新导入
.venv\Scripts\python.exe -m wavequant.interfaces.cli validate data\tdx_daily.csv
.venv\Scripts\python.exe -m wavequant.interfaces.cli research data\tdx_daily.csv --output-dir results\snapshot

# 单参数全样本回测：只能用于探索，不能叫样本外结果
.venv\Scripts\python.exe -m wavequant.interfaces.cli backtest data\tdx_daily.csv --config configs\mvp.json

# 无第三方依赖的离线模拟冒烟测试，不是真实市场收益
python -m wavequant.interfaces.cli demo
python -m unittest discover -s tests -v
```

`run-tdx` / `import-tdx` 支持 `--start`、`--end`、`--symbols sh.600036 sz.000651 ...`。更换起始期后，研究协议的三个区间也须有数据覆盖。研究命令使用 `configs/research_protocol.json`，可用 `--protocol` 指定另一个预先声明的实验。

## 数据口径

行情浏览、讲义折线和多级趋势统一经过 `MarketDataRepository`。AkShare 与通达信分别实现同一个适配器端口，第三方字段先校验并转换成统一 `Bar`，领域算法不判断数据源。默认优先 AkShare；主源不可用或截至所选日期数据滞后时，仓库按证券代码和交易日从备用源补齐，重复交易日始终保留主源 OHLCV，禁止备用源静默覆盖。Core 继续输出唯一的规范日线；API 展示服务可从这份日线生成 `1w`、`1mo`、`3mo`、`1y` 视图并调用相同结构算法，不让图表周期改动误触发全市场日线信号快照重建。Core 响应的 `data_source`、`resolved_source`、`providers`、`supplemented_bars` 与 `data_version` 便于 API 继续保留来源证据。

目录采用同样的合并规则：主源缺少股票名称、可用状态或整只证券时，备用目录补充缺失字段/证券并标记来源。适配器只负责采集，图表序列化、讲义折线、一级/二级/三级趋势计算均由统一仓库执行；新增数据源只需实现目录、日线加载和安全元数据三个接口。

读取 `D:\TDX\vipdoc\sh\lday\shXXXXXX.day`、深市对应文件，以及 `T0002\hq_cache\gbbq` 除权事件。日线是 32 字节小端记录；OHLC 转元，成交量按股。解码除权文件的首次运行约需半分钟，之后以文件 SHA-256 缓存。

价格使用**按除权发生日向后累乘的因果复权等价序列**：新发生的除权不会改写此前价格。模型数量是“复权等价份额”，通过 `adjustment_factor` 换算当日原始股数，按 100 股整手限制入场和按历史成交量限制容量。这不是券商真实现金/股数台账；股息再投资、红利税、配股支付和碎股等均为近似。

CSV 字段：`timestamp,symbol,open,high,low,close,volume,buyable,sellable,adjustment_factor`。后三项可省略，但真实数据应显式给出。加载器拒绝非有限数、重复日期、不一致 OHLC、负量和混合时区。研究模式只接受每只股票每日最多一根 K 线。

默认篮子：600036、601318、600519、600900、600104、000651、000333、000858、600276、002415。**这是固定便利样本，不是全市场、历史指数成分股或股票推荐**。`.day` 不含完整历史 ST/退市状态，当前导入器只适用于已上市的非 ST 沪深主板假设。IPO、科创板、创业板、北交所、ETF 不在支持范围；异常拆合股类别直接拒绝。未自动填补缺失日线。

## 信号与执行

当前实现笔记思想中的一个可检验子集，而非自动数浪：

```text
历史滚动高点突破 + 收盘位置 + 相对成交量
→ 后续 K 线回撤不超过 q
→ 再次收盘超过此前已知峰值
→ 下一根开盘按资金/风险/容量约束尝试买入
```

反向规则只发出退出信号，不做空。突破基准排除当前 K 线，完整热身后才产生信号；RVOL 分母只用过去同一时间槽成交量的中位数。回撤按“此前已知峰值到当前低点”逐步更新最大值，不使用未来确认的 ZigZag 波峰。

默认执行假设：100 万初始资金，最多 5 仓，单股入场市值不超过当时净值 20%，按止损距离预算净值 1%，不融资，现金利息为 0；容量上限为过去 20 根平均成交股数的 1%。这是入场约束，价格上涨后不强制再平衡到 20%。

- 收盘信号仅在下一根开盘尝试入场，开盘穿越失效位、跳空过大、不可买或容量不足则拒绝。缺行情的入场信号按全市场日历过期。
- 同开盘先处理退出，再按触发时 RVOL 降序、股票代码排序处理入场；所有股票共用现金。
- T+1 限制，开盘涨跌停拒单；退出若不可成交或超过历史容量，整单延迟，不模拟部分成交/队列。
- OHLC 观察到止损/止盈后下一根开盘退出，不以止损/目标价格保证成交，也不宣称这个假设必然更保守。
- 当前收盘才确认的移动止损不追溯用于当天最低价。期末不强制平仓，未平仓按末次市值计入净值。
- 默认每边佣金 3 bps（最低 5 元），每边滑点 5 bps；历史过户费和卖出印花税按变更日期计。佣金假设视为已含券商侧规费，不再重复加收。

## 研究设计与判定

默认训练 2018–2021、验证 2022–2023、测试 2024–最新共同日期。12 组 `q × RVOL` 只根据训练期 Sharpe 选择，至少 20 笔已平仓交易，否则回退默认参数。所有候选即使亏损也完整保存。“训练最优”不意味着训练盈利。

对照包含默认波浪规则、简单突破、去掉成交量、去掉盘态过滤、训练选定参数、佣金与滑点翻倍；税率不随压力场景翻倍。相同股票篮子的等权持有是**无费用、满仓毛收益参考**，风险暴露不同，不能直接据此声称 alpha。报告显示平均仓位。

每段独立初始现金与空仓，保留之前的指标热身数据，不从前段带入未执行订单或持仓。年度切片也独立重置，仅诊断稳定性。Sharpe 由逐日账户净值计算（252 日，无风险利率 0），回撤包含未实现盈亏，绝不把多股交易收益简单连乘成组合收益。

预设研究门槛：验证和测试均为正、测试至少 30 笔、Sharpe ≥ 0.5、最大回撤 ≤ 20%、成本压力后仍为正。20 日分块 Bootstrap 只提供探索性区间，不校正多次试验/参数选择偏差。

这属于历史时间外检验，不是真正从未看过的前瞻锁箱。看过结果后改变规则，必须登记为新实验并另留未来样本；不要反复用同一测试期挑最好看的版本。

## 尚未完成的实盘前置工作

1. 补齐 point-in-time 股票池、ST、停牌、退市、交易日历和完整公司行为，扩展更广历史样本。
2. 完成外链图片视觉核验，并根据 `docs/notion_review.md` 补齐尚未实现的独立假设；正文已全部阅读，但不声称所有战法已实现。
3. 检验信号是否有独立增量。若关闭盘态过滤产生完全相同信号，应承认冗余；不要只按某一测试期里更好看的消融选版本。
4. 新样本滚动验证、多重检验控制、行业/风格暴露及真实成本标定；再进行模拟盘前瞻观察。
5. 已有本地纸面风控、停机与对账；实际下单仍需券商适配、权限、真实账户及合规验收。本项目没有真实订单路由。

当前交付标准是**数据到报告完整可复现的研究闭环**，不是自动盈利或可直接实盘。
