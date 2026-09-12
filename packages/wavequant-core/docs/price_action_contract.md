# 价格行为基础接口

本模块只定义突破、跌破、抵抗及三笔观察顺序，作为以后 N 字、盘态、量能条件与交易规则的共同输入。统一入口是 `wavequant/domain/market_structure/price_action.py`，也可从 `wavequant.domain.market_structure.foundations` 导入这些新接口。

## 边界

- 不自动挑选颈线/波峰，不把滚动新高称为关键结构突破。
- 不识别 N 字、不分类六态、不读取量能、不生成买卖信号。
- 不推断主力、筹码成本或多空趋势已反转。
- 不预设抵抗成功/失败的阈值，不将第三笔出现等同于确认成功。

## 事先确定关键点：KeyLevel

`KeyLevel` 是不可修改的数据对象，记录股票、周期、支撑/压力类型、价格、来源和确认时间索引。

压力对应此前已形成的关键反弹高点；支撑对应此前最后一次有效上涨的起涨低点。如何选出这个点交给上层结构模块，基础层不替它猜测。`source` 必须说明来源。

`source_index <= confirmed_index < attack_index` 是硬约束。索引属于同一个按时间排序的序列。攻击当根才确认或更晚才确认的关键点不能用于该次攻击。对象不可修改，一次三笔事件使用同一个冻结关键点。

价格必须与 K 线采用相同的复权口径。调用方显式提供周期，模块检查与关键点一致；它不通过日期间隔猜测周期，也不能凭参数证明数据真的属于该周期。

## 突破与跌破：observe_attack

令 R 为压力，S 为支撑。每根都按已收盘数据处理，盘中字段代表从 OHLC 中观察到的盘中证据，不代表逐笔时间。

| 字段             | 突破               | 跌破               |
| ---------------- | ------------------ | ------------------ |
| extreme_beyond   | H>R                | L<S                |
| close_beyond     | C>R                | C<S                |
| intrabar_crossed | 前收<=R 且 H>R     | 前收>=S 且 L<S     |
| close_crossed    | 前收<=R 且 C>R     | 前收>=S 且 C<S     |
| gap_across       | 前收<=R 且 O>R     | 前收>=S 且 O<S     |
| touched          | 本根价格范围包含 R | 本根价格范围包含 S |

`beyond` 记录相对位置，`crossed` 记录从原侧越过的事件。前一根已经收在关键点外，当前继续上涨/下跌，不重复记为一次新攻击。返回原侧后，再越过可形成再攻击。这是用前收判定事件起始侧的显式工程约定，不声称知道棒内全部穿越次数。

等于参考点不满足严格越过。跳空可能完全没有触及参考价，但仍属于跨越；盘中越过又收回时，盘中证据为 True，收盘证据为 False。

基础层不统一选择盘中还是收盘。`observe_sequence` 的 `attack_basis` 是必填枚举：`AttackBasis.INTRABAR` 或 `AttackBasis.CLOSE`。

## 空头抵抗与多头抵抗：observe_resistance

向上攻击后的反方是空头，向下攻击后的反方是多头。以下按照用户列出的三个例子编码：

| 例子               | 空头抵抗      | 多头抵抗      |
| ------------------ | ------------- | ------------- |
| 反向开盘与实体组合 | O>前 C 且 C<O | O<前 C 且 C>O |
| 直接反向开盘       | O<前 C        | O>前 C        |
| 对应长影           | 上影长        | 下影长        |

高开/低开相对上一根收盘，阳线/阴线相对本根开盘。单独一根平开阴线不自动扩展成“开高走低阴线”；函数会保留阴线事实，但不因此认定该例子成立。直接低开后走出阳线，也仍保留低开抵抗证据。

上影长度 `H-max(O,C)`；下影长度 `min(O,C)-L`。函数总是返回影长和范围比例。

讲义没有给“长”的数值，因此默认 `shadow_policy=None`：

- 影长为零：`long_shadow=False`。
- 有影线但没有阈值：`long_shadow=None`，表示尚未量化。
- 明确传入 `ShadowPolicy(0.5)`：才按“影长/全棒范围 >=0.5”判断。这是调用方的参数，不是讲义默认值。
- 一字 K 的比例为 None，不产生 NaN/Infinity，也不记作长影。

综合字段 `detected` 保留三种值：

- True：至少一个列举模式成立，`reasons` 给出具体原因。
- False：当前规则下未检出这些模式，不能解读为市场绝无抵抗。
- None：已知模式未命中，但长影标准未配置，尚不能完整判断。

抵抗模式成立与抵抗成功是不同问题，本接口不输出成功或失败。

## 当笔、次笔、再次笔：observe_sequence

调用时提供截至当下已知的 K 线前缀。固定攻击索引 t，严格按同股票、同周期的 t、t+1、t+2 处理，不寻找未来某一根替代第三笔。

| 已有数据             | phase                 | 可用结果                       |
| -------------------- | --------------------- | ------------------------------ |
| t 不满足指定攻击口径 | no_attack             | 攻击观测，无后续抵抗事件       |
| 只有攻击根 t         | await_response        | attack                         |
| 已有回应根 t+1       | await_confirmation    | attack、response               |
| 已有第三根 t+2       | confirmation_observed | attack、response、confirmation |

这里“次笔”是序列中下一根可用棒，不是下一个自然日。周末不是额外 K 线；交易日缺失仍需数据质量模块处理，不能用这一规则掩盖漏数。

第三笔返回事实：收盘是否仍在关键点外、是否回到关键点另一侧、最高/最低是否超出攻击或回应极值、收盘是否超出这些极值、收盘是否相对回应继续同向。

`resistance_outcome='undefined_rule'` 明确保留成功/失败判据尚未定义。以后六态/交易模块可以用这些事实制定各自显式规则，但不得悄悄改动基础定义。更多未来棒不会重写这次三笔事件。

## 最小调用

完整可执行示例见 `examples/price_action_basics.py`，使用手工样例而非市场盈利证据。

```python
from wavequant.domain.market_structure.price_action import (
    KeyLevel, LevelKind, AttackBasis, observe_sequence,
)

key = KeyLevel(
    symbol="EXAMPLE", timeframe="1d", kind=LevelKind.RESISTANCE,
    price=10.0, source_index=0, confirmed_index=0,
    source="example_preidentified_rebound_high",
)

# bars 为同股票、同周期、同价格口径的有序 Bar 列表。
event = observe_sequence(
    bars, attack_index=1, level=key,
    timeframe="1d", attack_basis=AttackBasis.CLOSE,
)
```

在项目根目录运行：

```powershell
.venv\Scripts\python.exe -m examples.price_action_basics
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 与旧代码的关系

`foundations.resistance_evidence` 和 `three_bar_state` 是之前的讲义研究代理，保留宽泛阴阳线判断及 0.5 默认影线比例，以便旧结果可复核；它们不是新基础定义。新模块应使用 `price_action` 接口。旧 N 字策略、六态代理和基础统计尚未全部迁移，迁移时要另行测试，不能将新定义直接混入旧回测。

新增测试覆盖：突破/跌破对称、触及、跳空、盘中与收盘区别、重复攻击、再攻击、未来关键点拒绝、参数不可修改、三类抵抗、未量化长影、一字 K、三阶段时间顺序、未来不重写、股票/周期/价格校验及周末。
