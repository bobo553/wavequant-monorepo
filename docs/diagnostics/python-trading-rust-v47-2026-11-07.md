# Python 买卖调度接入 Rust（v47）

用户范围：买卖信号与模拟账户成交；Python 源码保留，不调用 Python 执行量化策略。当前为开发阶段，回归与同输入对照待提交时或单独授权后执行。

## 职责与源码映射

| Python 参考 | Rust 实现 | 本轮处理 |
| --- | --- | --- |
| `domain/strategies/integrated_strategy.py::consume_signal_day` | `analysis/trading/dispatch.rs`、`day.rs` | 提取 DecisionScope 与 SignalHistory；结束日扫描但不可买；历史已发买入信号时继续观察退出；卖点优先 |
| `evaluate_buy`、候选仲裁 | `trading/arbiter.rs`、`entries.rs`、`hierarchy.rs`、`day.rs` | 复用现有 Rust 量价、N、层级、目标和风险收益模块；所有补充入口统一受日调度许可约束；拒绝不标记买入已发出 |
| `evaluate_sell` | `day.rs::strategy_exits`、`trading/risk.rs` | 复用 Rust 退出候选及实际持仓优先级；无扫描资格且无买入历史时不生成日策略退出；EXIT 不是平仓回执 |
| `application/analytics/backtest.py` | `analysis/system.rs`、`execution/strategy_backtest.rs` | 接通分钟/日线决策与 Decimal 账户执行；保持资金、手数、T+1、费用、成交时钟及拒单记录；冻结信号同日 EXIT 压过 LONG |
| 日线信号入口 | `execution/stream.rs` | 显式交易模式，统一周期为 `1d`；修正原 `daily` 导致交易不适用 |

## 请求与结果

`AnalyzerConfig` 新增 `mode`，默认 `structure`；`SystemConfig` 默认 `trading`。股票回测、封存回测、研究装配、买点扫描及实时信号入口显式选择交易模式，指数和行情周期查询使用结构模式。结构模式不能带冻结交易信号，避免静默丢弃交易输入。

API 股票结果返回同次计算的信号、订单、成交、持仓、权益与趋势；预览读取同一个逐日分析器。图表不重新识别趋势。版本 47 及回测缓存身份 `native-api-stock-result-v6-trading` 隔离旧空账户缓存。不会自动发起回测或重新计算自选列表。

既有结构数组资格与逐日交易资格有不同的数据组织：前者使用索引数组和 `kernel/eligibility`，后者使用 `TradingEligibility`。本轮没有重写趋势核心，也没有把数组模式的历史资格对照当作交易模式验收。确认日低点严格破位、倍量阴线实体低于前日且长上影这两项已知现行约束提取为共享函数，使逐日交易同样遵守；同输入的完整资格日期、N 生命周期、买卖点与成交结果仍待对照。

## 验证与交接

- 新增 `tests/python_trade_migration.rs`，覆盖资格结束日、历史买入信号延续、退出优先、冻结信号次日撮合及纯结构无交易的边界，尚未执行。
- 开发期间未运行测试、lint、独立类型检查、格式化、回测、HTTP 探针或浏览器验证。启动编译和重启单独记录在 workspace progress，不作为交易逻辑正确性的证据。
- 仍需在获准验证时对冻结行情比较 Python/Rust 的资格、信号、执行时钟、数量、费用与权益，分别记录现行 Rust 规则差异及移植缺陷，不放宽断言掩盖差异。
- Python 策略及账户代码未修改。现有行情 SDK 数据桥接不在本次量化逻辑迁移范围内。

流程图统一维护在 [趋势与交易流程](../trend-analysis-flow.md)。

启动记录：本轮必要 Rust 编译已完成（7m08s），仅保留既有 MemoryPoint::read 告警；当前 E 盘 target/debug 的 API PID28216/8010 与前端 PID15592/3003 已启动，原数据目录和禁用自动回测设置保持。Docker 因运行时套接字曾启动失败，未清理或移动文件；用户告知已启动后停止排障，恢复此前在途停止命令造成的中断，随后 Docker 与原 Redis 容器启动完成；未改配置和容器数据。启动记录不替代策略验证。
