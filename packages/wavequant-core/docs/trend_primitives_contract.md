# 折线趋势定义基座

这些基础函数对应二十个基础概念（原条目 19 包含轧空低与杀多高），供折线、图表和策略组合复用。输入使用同股票、同周期、同复权口径的有序 `Bar` 和已确认 `ReversalPoint`；函数没有 I/O 或交易副作用。实现位置：`wavequant.domain.market_structure`。图示分段和完整命名时序见 [图 008 组合契约](figure008_contract.md)。

## 相邻 K 线关系

统一模块 `candle_primitives` 的参数顺序均为 `(previous, current)`。`observe_bar_relations` 一次返回所有关系，批量折线生成优先使用它；各项也能单独调用。

| 定义   | 独立函数         | 公式                                  |
| ------ | ---------------- | ------------------------------------- |
| ① 缩头 | `shrinking_head` | 今高 < 昨高                           |
| ② 缩脚 | `shrinking_foot` | 今低 > 昨低                           |
| ③ 出头 | `extending_head` | 今高 > 昨高                           |
| ④ 落尾 | `falling_tail`   | 今低 < 昨低                           |
| ⑤ 日出 | `sunrise`        | 今高 > 昨高且今低 > 昨低且今收 > 昨高 |
| ⑥ 日落 | `sunset`         | 今高 < 昨高且今低 < 昨低且今收 < 昨低 |

这些关系可以同时成立，不是互斥状态；相等不满足严格比较。虚拟价公式为 `virtual_low = min(今低, 昨收)`、`virtual_high = max(今高, 昨收)`，包含跳空时未成交的价格间隙。

`polyline.BarRelations` 和 `polyline.observe_bar_relations` 保留原导入路径；旧 `foundations.bar_relations(current, previous)` 保留字典和 `lifting_foot` 字段，但内部共用新实现。

## 反转、关键点与趋势

`observe_structure` 产生冻结的 `StructureContext`，`trend_primitives` 消费该上下文和已确认点。

六个入口统一接受 `trend_level=1 | 2 | 3`，省略时保留一级兼容。`observe_structure` 和组合入口 `observe_trend_foundations` 同时接受 `source_level`，只能等于 `trend_level - 1`：一级从高低折线反转判断，二级从正式一级趋势判断，三级从正式二级趋势判断。上下文的级别与调用级别不匹配时明确拒绝，不借用另一级的关键位。

| 定义        | 入口                | 判定                                                 |
| ----------- | ------------------- | ---------------------------------------------------- |
| 7 末跌高    | `last_fall_high`    | 窗口最低已确认低点左侧最近负反转；可显式选择目标低点 |
| 8 末升低    | `last_rise_low`     | 窗口最高已确认高点左侧最近正反转；可显式选择目标高点 |
| 9 正反转    | `positive_reversal` | 已可知的低点转折                                     |
| 10 负反转   | `negative_reversal` | 已可知的高点转折                                     |
| 11 多头趋势 | `is_bull_trend`     | 所选窗口内每组同类高点和低点都严格升高               |
| 12 空头趋势 | `is_bear_trend`     | 所选窗口内每组同类低点和高点都严格降低               |

窗口极值并列时取最早者；缺少左侧相反转折返回 `None`。转折的绘图日期与确认日期分开；在确认日期前，转折不能充当已知锚点。未确认末端也不能代替反转点。趋势至少需要两个已确认高点和两个已确认低点，不足时返回 `None`，混合或相等时两个趋势谓词都为 `False`。

现有 `StructureContext.trend` 仍表示最近两组的局部方向；上述趋势入口使用 `window_trend`，对应定义中的“每一个”。调用方通过窗口边界指定分析波段。高低折线的母子、同棒路径和确认策略继续使用已有 `polyline` / `lecture_drawing`，不会由这些谓词重新生成。

### 三级共用归并与保留突破升级

`trend_levels.trend_reversals(points, trend_level=...)` 是三级共用的普通方向归并入口。两高两低严格同向且后续持稳点已经确认，才确认整波反转；更高高点后出现更高低点（HH→HL），或更低低点后出现更低高点（LL→LH），必须按实际发生顺序成立。`definition_evidence` 保存四个来源点，`direction_definition` 只表示多头或空头方向，不自动生成“翻空为多／翻多为空”的严格突破事件。

用户保留的独立通道由 `trend_level_upgrades.promote_trend_key_breaks(source, candidates, trend_level=...)` 实现：已确认下级高点严格突破此前已知本级高点，或下级低点严格跌破此前已知本级低点，即可升级。相等不升级；旧关键位须在攻击价格日之前已知。起点取旧关键位与攻击端点之间已确认来源的整波最低／最高，相等取最早者，不用更近的小回调缩短整波，也不等待攻击端点之后的回调。

`upgrade_evidence` 分别保存方向、起点、被突破关键位、真实攻击点、可知日期及 `origin`／`endpoint` 角色。上涨端点升级不表示其后的下降已经确认，低点镜像同理。普通反转、严格突破、可选 N 确认以及每段发布证书分别保存证据；相同坐标的不同事件不能相互吞掉。升级结构仍通过现有真实行情发布门禁，不绕过本级上涨／下降证书，也不自动产生交易信号。

下降升级的配对高点不能被上涨创新高延伸规则过滤。已有合法整波保留其原点；若此前仅有公开压力高点，则在本次突破可知日加入真实旧低点和配对高点作压力参考衔接，保留原有高点历史，不倒填参考点日期，也不授予中间上涨段许可证。一级下降测幅通道独立收集低点升级的下降证据：高点先前可能已有上涨证据，下降从低点破位自己的可知日才成立。

图表和逐日回测采用相同的递归来源：二级只读取一级 `strokes`，三级只读取二级 `strokes`。`candidate_strokes`／`structure_strokes` 以及显示用发展尾部不成为上一级来源。候选并集可能同时保留已升级端点及后来确认的同类整波极值，只有发布门禁整理后的正式路径才作高低交替标注。

`TrendPriceState(trend_level=...)` 与批量 `classify_trend_prices` 共用严格比较，单次追加及读取局部／全窗方向均为 O(1)。普通归并按来源顺序前进，突破升级以区间查询处理延迟可知证据，避免每个攻击点重复扫描整个窗口。所有可知日期覆盖来源和旧关键位的确认；绘图价格日不替代知识日。历史级别及部分端点可能因本次统一而变化，应以新算法重新计算历史结果。

## 翻向、交替与疑虑

先以冻结上下文调用 `observe_trend_transition`，显式选择 `AttackBasis.INTRABAR`（高/低越线）或 `AttackBasis.CLOSE`（收盘越线），再用 `observe_trend_definition_signals` 取得不可修改的命名字段：

| 定义             | 字段                                 | 依据                                                       |
| ---------------- | ------------------------------------ | ---------------------------------------------------------- |
| 13 翻空为多      | `flip_to_bull`                       | 向上严格突破冻结末跌高                                     |
| 14 空多交替      | `bear_to_bull_alternation`           | 翻多之后，首个已确认且实际发生的部分回档 < 上涨幅度 × 0.67 |
| 15 翻多为空      | `flip_to_bear`                       | 向下严格跌破冻结末升低                                     |
| 16 多空交替      | `bull_to_bear_alternation`           | 翻空之后，首个已确认且实际发生的部分反弹 < 下跌幅度 × 0.67 |
| 17 头部疑虑/成形 | `head_suspicion` / `head_formed`     | 拉回后反弹不过前高并再次转跌；疑虑之后严格破末升低才成形   |
| 18 底部疑虑/成形 | `bottom_suspicion` / `bottom_formed` | 反弹后回档不创新低并再次转涨；疑虑之后严格破末跌高才成形   |

字段记录截至观察时刻已发生的历史证据。后续失效不会擦除历史翻向/交替；当前状态及失效日期仍应读取原 `TrendTransition.stage` / `invalidated_index`。没有疑虑的直接破位可发生翻向，不自动产生头底成形。

“未突破／未跌破”允许恰好触及关键位；“不过前高／不再创新低”也允许相等。疑虑仍须在再次转向已经确认后才能输出，成形突破继续严格比较。

`retracement_evidence` 使用十进制计算。`shallow_countermove` 表示实际部分回撤严格 `< 0.67`，`weak_countermove` 表示严格 `< 0.33`；等于门槛或尚未回撤不成立。这里的 `0.67` 与 `0.33` 没有改成 `2/3` 与 `1/3`。实际 abc 三波与等浪证据由已有 `observe_abc` 单独返回；“通常三波”不作为所有交替的额外必需条件。

## 19 轧空低与杀多高

`n_defense.squeeze_low(NObservation)` 和 `n_defense.selloff_high(NObservation)` 只从已完成正 N / 倒 N 的攻击棒取虚低/虚高。未完成返回 `None`，已完成但方向错误则拒绝。调用方先使用现有 `observe_n` 确认结构及实虚突破，不能把任意新高/新低直接称为 N。

价格冻结在完成棒；后续失守不删除历史参考。当前有效性读取 N 观察的破位日期。分步 N 的 `completion.defense` 可能含前一攻击棒，是既有操作防守位；这里的十九项基础定义返回完成棒 `virtual_low` / `virtual_high`，明确区分两者，不改变分步 N 的既有策略行为。

量增由 `observe_control_bar` 的量能证据记录，不作为上述虚拟价的必要条件。进货、出货和主力成本不能由 OHLCV 直接确认。后续抵抗、守线与盘态使用现有主控/盘态模块继续组合。

## 最小调用

```python
from wavequant.domain.market_structure.candle_primitives import observe_bar_relations
from wavequant.domain.market_structure.trend_structure import observe_structure
from wavequant.domain.market_structure.trend_primitives import is_bull_trend, last_fall_high

relations = observe_bar_relations(bars[-2], bars[-1])
context = observe_structure(
    confirmed_points, symbol="EXAMPLE", timeframe="1d",
    window_start=0, asof_index=len(bars) - 1,
)
key = last_fall_high(context)
bull = is_bull_trend(context)
```

完整可执行示例见 `examples/trend_primitives.py`。这些入口没有新增盈亏优化或替换已验证的高层趋势确认规则。

## 组合调用

`trend_foundations.observe_trend_foundations` 将各基座组合成一次观察，保留单独入口供已有调用方复用：

```python
from wavequant.domain.market_structure.trend_foundations import observe_trend_foundations

snapshot = observe_trend_foundations(
    bars, confirmed_points, symbol="EXAMPLE", timeframe="1d",
    window_start=0, asof_index=len(bars) - 1,
    transition_context=frozen_context,  # 此前已知的多头或空头背景
    n_setup=known_n_setup,              # 可省略；没有 N 时不产生 N 防守位
)
```

默认按高／低严格越线，与图 008 的翻向定义一致；收盘口径显式选 `AttackBasis.CLOSE`。首次 K 棒没有昨日数据，相邻关系与虚拟价返回 `None`。缺少冻结背景时不推断翻向；缺少已完成 N 时不返回轧空低／杀多高。`examples/figure008_foundations.py` 给出完整可执行的图示分段调用。
