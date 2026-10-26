# 国芳集团 Rust / Python 资格区间对照

核对日期：2026-10-25。最新 v26 已再次复现资格差异，详见下节；后半部分为早前 v25 基线。差异不限于图表显示。

## 2026-10-25 晚间复核：2024-02-08 低点与当前 Rust v26

本轮由用户明确要求启动旧 Python 服务、对照国芳集团资格区间和代码。只对这只股票计算结构与资格，未运行账户交易，未修改 Python 或 Rust 业务源码，未运行浏览器。维护者为 Rust 资格迁移开发者。

### 服务与输入

- 旧 Python API：`http://127.0.0.1:18765`，启动进程 PID 22128，保留会话 24540；原 8765 端口绑定遭 Windows 10013 拒绝，改用独立端口，没有关闭其他服务或修改系统端口设置。
- 启动旧 `wavequant_api.cli`，使用 API 专属既有虚拟环境；PYTHONPATH 显式指向本 worktree 的 API/Core 原源码，未安装或修改依赖。
- 结果目录为 `.codex-runtime/py-compare-20240208/results`，仅复制原封存 operations.sqlite 和 runs；未复制原自选数据库。SQL/Redis 配置为空，健康接口返回 HTTP 200/status=disabled；任务接口确认活动回测 0，自选刷新 waiting。没有触发全自选或全市场计算。
- Rust API 仍为 8010，返回实际 `state.version=26`。当前页面为 lecture_v3、二级资格、volume_filter=false、普通趋势完成及 N 达标趋势关闭、交易停用。
- 共同离线输入仍为 2135 根、2018-01-02 至 2026-10-23，dataVersion=`32c0d18c7d7311c67a43c49aa0143e5a17bb4d952724b6c045ee80021ac82bef`。用户提供的 2024-02-08 OHLC/成交量与该输入一致。
- Python 在独立禁网进程重新执行 market_calculations/calculate_trend_line(3)/trading_intervals(2)，strict_n_attack_quality=false；本次耗时 183.789 秒，七段区间及全部级别区间与早前 Python 记录完全一致。此耗时含历史/诊断输出，不作为 API 分阶段性能比较。
- Rust 使用现有 v26 诊断二进制的真实数组路径，固定同一份输入；另按当前页面真实参数读取实际单股 API。离线和页面的七条区间逐字段完全一致。
- 页面新行情 dataVersion=`6598433ce642f65cf7771d1b3559fa738e3b44c0c5bcfbe370217cd84d18d760`；相较旧冻结输入有 206 个 OHLC 尾位差异，最大绝对差 `7.105427357601002e-15`，日期、量、复权因子均一致。因此不把两份行情声称为逐位相同；Python/Rust 离线比较使用同一份冻结输入，页面结果另行比对。

### 用户指出这根 K 线的实际角色

2024-02-08（行情下标 1484）是外层回调低点，同时是内部正 N 起点；不是本轮空翻多首次确认日，也不是旧 Python 正式开启资格的当日。原始收盘 3.62 元、复权收盘 4.728451973326568 元、复权最低 4.219033114321772 元、成交量 30,114,838 股，与用户描述对应。

| 步骤 | 日期/下标 | 旧 Python 真实证据 |
| --- | --- | --- |
| 本轮二级攻击高点 | 2023-08-01 / 1354 | H=11.024346589744814；对应空翻多于 1355（2023-08-02）已发布 |
| N 起点 nA / 外层最低 L | 2024-02-08 / 1484 | L=4.219033114321772 |
| N 颈线 nB | 2024-02-21 / 1487 | high=5.42073914069206，close=5.2248088103056 |
| N 回调 nC | 2024-02-22 / 1488 | low=5.172560722202544 |
| N 攻击成立 | 2024-02-23 / 1489 | high=5.616669471078519，close=5.577483405001227 |
| 轧空及交替结束确认 | 2024-02-27 / 1491 | close=5.956282043748383；positive_n_and_squeeze_after_qualified_b |
| 二级资格结束 | 2024-03-28 / 1513 | 一级关键低点 1508 破位，source_bear_trend_confirmed |

这次外层回撤比例为 0.9434396258603863，属于深回撤 N 轧空确认：price_path=false、time_path=false、deep_price_path=true。该规则用后续 N 攻击和轧空判断结束，不要求 2 月 8 日这根 N 起点 K 线倍量；其约 1.01 倍量不能解释缺失。

旧 Python 图层会在确认到达后，从结构低点 2024-02-08 回画红区，正式可交易日仍是 2024-02-27。比较计算正确性用正式确认日，不能把回画低点当作当时已经获得资格。

### 当前 v26 的全段对照

所有结束日按左闭右开处理。同波段列在同一行方便比较，不表示每条 Rust 记录都与 Python 存在一对一对应关系。

| Python 正式资格 | 当前 Rust v26 | 问题 |
| --- | --- | --- |
| 2023-11-01 → 2023-12-22 | 无 | 整段遗漏 |
| 2024-02-27 → 2024-03-28 | 无 | 用户指出的低点之后第一段遗漏 |
| 该时段无资格 | 2024-04-08 → 2024-04-17 | Rust 额外开启 |
| 2024-04-26 → 2024-05-22 | 2024-04-26 → 当日 | 开启后被旧破位立即关闭，图上没有有效面积 |
| 2024-09-06 → 2024-12-26 | 2024-09-27 → 2025-01-10 | 开始晚、结束也晚 |
| 2025-02-27 → 2025-03-26 | 2025-02-27 → 2025-02-28 | 过早结束 |
| 2025-04-03 → 2025-07-04 | 2025-04-22 → 04-23；2025-05-29 → 05-30 | 整段变成两段很短的缺口资格 |
| 2026-07-23 → 2026-09-15 | 该段无；另于 2026-10-08 开启且未关闭 | 开始时钟及来源修订退出不同 |

Python 7 段非零区间；Rust 7 条记录，其中 1 条零长度、6 条非零（含末段未结束）。逐日资格共 **231 个交易日不同**：Python 有而 Rust 无 202 日，Rust 有而 Python 无 29 日。只看 2024-02-08 至 2024-05-22 就有 44 日不同。这个数量属于 v26，不沿用早前 v25 的 175 日结论。

### 源码和运行证据对应的原因

1. **用最终点的封闭时钟代替当时翻多时钟。** 当前实际 DTO 中二级高点 1354/2023-08-01 的 knownIndex=1508/2024-03-21；二级低点 1484/2024-02-08 的 knownIndex=1780/2025-05-09。analyzer::finish_level_array 先生成最终层级，finish_qualification_arrays 再把最终 frame 转成标量。structure::record 和 context 均用点的 known_index 发布翻转事件与六态时钟，资格 seed 要求种子已知，visible 也按 known_index 截断。旧 Python 1355 日已经有本轮空翻多，1491 日已用 1484 低点确认 N；最终闭合点的较晚时钟无法重建这些历史事实。这直接解释早期两段不能正常进入 Rust 资格确认，并非前端漏画。
2. **最终下级关键位无法替代历史当时关键位。** 旧 Python 的 1695 日退出依赖当时一级 1681/1685；1819 日退出依赖当时 1810/1813。Rust 资格消费最终下级点集，这些历史版本已被替换，导致某些段退出延后，或错用其他低点。早前基线已有具体价格与点位证据，下文保留。
3. **退出是每日持续条件，不是一次事件。** eligibility::last_rise_broken 每天重新判 low < 旧 low 且未创新高，旧破位持续为真。Python 在 1525 日已经发布过的下级破位，Rust 在 1532 日新确认后仍拿它关窗，产生 2024-04-26 的零长度资格。需要区分旧事件与新事件，不能靠增加区间长度或前端补色修复。
4. **当前单 N 与旧 N 链不完全等价。** 旧 confirm_bull_alternation 除 N + regime 外还处理 N 盘整缺口、N 轧空下末跌高量价突破、倍量 N 整段回撤、放量反转及恢复；当前 confirm_single_n 仅复用 candidate_proof，外围保留独立量价、实体缺口、普通回撤。单候选选择是用户授权的有意变化，但“保留三条外围通道”不代表这些旧 N 子路径全部保留；这部分差异与历史输入问题必须分开处理，本轮没有逐项消融，不能把全部 231 日归因于一个原因。
5. **来源修订及恢复门槛未完整迁移。** Python 2112 日以 source_evidence_revised 关闭末段；当前 Rust Active 没有该历史来源修订事件消费者。Python SameLevelRecovery 冻结高点解锁/等待新翻多，与 Rust 只有 after 的状态也不相同。

对应入口：[Rust analyzer](../../apps/servers/wavequant-quant-rs/src/analysis/analyzer.rs)、[结构六态时钟](../../apps/servers/wavequant-quant-rs/src/analysis/kernel/structure.rs)、[资格消费者](../../apps/servers/wavequant-quant-rs/src/analysis/kernel/eligibility.rs)、[当前单N证明](../../apps/servers/wavequant-quant-rs/src/analysis/trading/n_alternation.rs)、[Python确认](../../packages/wavequant-core/src/wavequant/domain/market_structure/bull_alternation_confirmation.py)、[Python生命周期](../../packages/wavequant-core/src/wavequant/domain/market_structure/trend_lifecycle.py)、[Python资格窗口](../../packages/wavequant-core/src/wavequant/domain/strategies/trading_intervals.py)。

### 后续修复应遵守的边界

保留用户要求的单 N、索引、共用三级趋势函数和本级开关。需要让共用结构计算保留/发布当日真正可知的轻量历史事实，分别表达攻击确认和拐点闭合；资格按这些事实增量消费。当日直接下级关键位需要版本化保存，退出事件唯一消费，并补齐来源修订和恢复门槛。不能只提前修改最终点 knownIndex、回填图表红区、删除必要的退出条件，或恢复每天遍历 120 日组合。旧 N 子路径是否保留及如何增量表达需另行明确，不能把它们暗中并入单 N 规则。

本轮完成服务启动和差异定位，**尚未改动业务算法或修复以上差异**，不将本功能标记完成，不提交或合并 main。

### 本轮证据位置

`.codex-runtime/py-compare-20240208/`：`guofang-python-days.jsonl`、`guofang-python-qualification.json`、`guofang-v26-input.json`、`guofang-v26-comparison.json`、`current-rust-v26.json`、`current-rust-bars.json`、`current-rust-level2.json`、`comparison-summary.json`、`current-context.json`、`comparison.log`、`python-18765.stderr.log`。原先证据未覆盖；本轮没有测试套件、浏览器、额外股票或账户验证。

---

以下保留早前 v25 基线，不能当作当前 v26 的最新测量。

## 输入与执行边界

- 股票：601086 国芳集团，日线；2018-01-02 至 2026-10-23，共 2135 根 K 线。
- 直接使用已保存的同一份因果复权行情，未重新取数或复权；行情版本 `32c0d18c7d7311c67a43c49aa0143e5a17bb4d952724b6c045ee80021ac82bef`。
- 旧 Python 使用本任务 worktree 的未修改源码；调用 `market_calculations → calculate_trend_line(3) → trading_intervals(2)`，不运行账户交易。
- 讲义 V3 资格参数为 `AlternationPolicy(120,60,20,0.5,False,True)`；普通趋势完成、N 目标确认均关闭。
- Rust 对照为此前真实 v25 结果，本轮没有重新发起 Rust 股票任务。Python 使用独立进程并禁止网络。
- 首次使用默认 strict_n_attack_quality=True 的尝试在途中终止；该未完成尝试不计入证据。最终完整运行使用实际讲义参数 False。
- 最终完整运行成功，记录包含 2135 个逐日资格状态及最终区间。218.870 秒包含三级计算、历史读取和诊断导出，不能直接与 Rust API 的纯分析计时比较。

## 实际区间

以下比较的是正式资格开始日和结束日，统一使用左闭右开 `[开始,结束)`；不以旧图表向前回画的红底作为资格开始。

| Python 资格 | Rust v25 资格 | 差异 |
| --- | --- | --- |
| 2023-11-01 → 2023-12-22 | 无 | 漏掉 |
| 2024-02-27 → 2024-03-28 | 无 | 漏掉 |
| 2024-04-26 → 2024-05-22 | 2024-04-26 当日关闭 | 旧破位重复关闭新资格 |
| 2024-09-06 → 2024-12-26 | 2024-09-06 → 2025-01-10 | 结束偏晚 |
| 2025-02-27 → 2025-03-26 | 2025-02-27 → 2025-02-28 | 结束偏早 |
| 2025-04-03 → 2025-07-04 | 2025-04-11 → 2025-07-30 | 开始和结束均偏晚 |
| 2026-07-23 → 2026-09-15 | 无对应有效段；2026-10-08 另行开启且未结束 | 确认时钟与来源修订处理不同 |

Python 7 段；Rust 5 条记录，其中 1 条零长度，因此只有 4 段非零区间。逐日有效资格有 **175 个交易日**不同；首次为 2023-11-01，末日 2026-10-23 Python=false、Rust=true。

## 已由该股票证据证实的原因

1. **最终数组不能替代历史当日输入。** Rust `analyzer.rs:318` 在四级全部计算完成后才启动资格，`eligibility.rs:92` 用最终点的 `known_index` 截取历史。Python 在 1355 日（2023-08-02）已经发布二级空翻多；Rust 最终二级高点 1354 的 knownIndex 是 1508（2024-03-21），资格输入只读取最终已确认点，前两段因此发生在 Rust 入口可见之前。确认方向的时钟与最终拐点封闭时钟不能合并。
2. **历史退出关键点被最终结构替换。** Python 在 1695 日用一级低点 1681（7.199364）与高点 1685 识别下级退出；Rust 最终一级数组没有低点 1681，用到的是更早、更低的 1657（5.874244），退出延后到 1705。类似地，Python 1819 日用 1810/1813 退出，这两个点也不存在于 Rust 最终一级数组，Rust 延后到 1837。
3. **同一次破位被重复消费。** Python 在 1525 日（2024-04-17）已发布一次下级关键低点破位，1532 日（2024-04-26）重新确认资格时没有新的该退出事件。Rust `last_rise_broken` 每天判断仍低于旧低点，1532 日条件仍为真，导致资格当日关闭。
4. **缺少来源修订结束。** Python 最后一段于 2112 日（2026-09-15）以 `source_evidence_revised` 关闭；Rust 新资格消费者没有该历史修订输入及结束分支。Python 2074 日已使用低点 2056 确认资格，Rust 同一低点的最终 knownIndex 为 2123，进一步造成晚开。

## 另有源码差异，未逐项隔离量化影响

- 下级正式转空、空头结构破位、放量假突破失败事件未完整接入新 Rust 资格消费者。
- Python `SameLevelRecovery` 的冻结高点解锁与新翻多门槛尚未完整迁移；Rust 仅保存关闭日 after。
- 缺口失效与独立确认的处理顺序不一致。
- 资格量价规则所用的折线末跌高/低：Rust 复用 `six_state_indices`，Python 使用 `_PolylineStructure`，两者起点冻结等规则不同。
- 严格 2/3 边界的 Decimal 与 float 比较存在精度差异可能，未证明影响本样本。

## 图表显示差异

旧 Python audit 同时提供真实确认日和 start_low_time，旧红底在确认到达后回画到结构低点；Rust `trend-state.js:17` 设置 start_basis=confirmation，从确认日起画，并固定不包含结束日。旧 Python 对 source_bear_trend_confirmed 等部分结束原因允许红底包含结束日。蓝色边界才是两版真实资格时钟的直接对照。显示差异需要单独统一，不能用它解释上述 175 日计算差异。

## 修复方向（尚未实施）

保留用户要求的本级开关 → 空翻多 → 空多交替结束确认 → 有效资格 → 结束判断流程。结构计算应发布轻量、带真实可知日的版本变化和事件；资格按同日事实顺序增量消费，并保存历史当时的直接下级关键位。继续使用索引/不可变记录与偏移，不恢复整段行情复制或递归证据序列化。退出事件须唯一消费，补齐来源修订及恢复门槛，再分别统一图表投影。

此轮仅检查与获准的单股对照，没有修改 Rust/Python 业务规则，没有为单股添加特判，没有将当前差异版本合并 main。诊断基线为特性分支提交 `63f528c`。后续单N v26调整不会自动消除上述历史输入及退出差异，继续保留为待完成项。

## 可复核证据

本 worktree `.codex-runtime/rust-startup/`：

- `compare-guofang-qualification.py`：离线计算脚本。
- `guofang-python-days.jsonl`：2135 日实际资格与生命周期事件。
- `guofang-python-qualification.json`：完整 Python 区间、历史事件、最终结构。
- `guofang-v24-bars.json`：共同输入；与 v25 的 dataVersion 相同。
- `guofang-v25-result.json`：实际 Rust v25 响应。
- `summarize-guofang-qualification.py` 与 `guofang-qualification-comparison.json`：逐日对照及源码摘要。

全市场等价未验证；此单股已足以证实共用实现不等价。之前单元回归通过不代表 Python 逐日语义一致。
