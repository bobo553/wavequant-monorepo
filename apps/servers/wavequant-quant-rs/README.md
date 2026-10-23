# WaveQuant Rust Quant

独立量化批处理工程，包含可复用 library 和 CLI binary。Owner：WaveQuant 项目维护者。
当前只提供行情输入检查骨架，不包含趋势迁移、策略、回测收益、账户或下单实现。

## 目录与依赖

- src/modules/market_data/domain.rs：纯 Rust OHLCV 不变量，不依赖 Serde、HTTP 或存储。
- src/modules/market_data/application.rs：行情检查用例与 BarSource 输入端口。
- src/modules/market_data/mod.rs：模块公开入口，其他模块不得访问私有实现。
- src/infrastructure/json_bars.rs：有界 JSON 文件读取与协议对象映射。
- src/presentation/cli.rs：命令行参数与报告输出。
- src/main.rs：进程入口。
- tests/：领域与用例边界回归源码。
- examples/：虚构的输入样例，不是实股验证证据。

新增策略、回测、组合、风险能力时分别进入 src/modules/<能力>，
按实际需要增加 domain、application、infrastructure、presentation。
核心规则保持纯函数或显式状态机，时间、随机和外部数据通过端口传入；
只有存在稳定跨应用复用时才将领域库提取至 packages。

## 用法

在仓库根目录执行：

```powershell
cargo run --locked -p wavequant-quant-rs -- --help
cargo run --locked -p wavequant-quant-rs -- inspect apps/servers/wavequant-quant-rs/examples/bars.json
```

输入为单一标的、周期、复权口径的 JSON 数组，最大 16 MiB。
timestamp_ms 是 UTC Unix 毫秒正整数，必须严格递增，重复和逆序均拒绝，不自动排序。
open_ticks/high_ticks/low_ticks/close_ticks 为同一最小报价单位的正整数；
单位与价格复权由调用方统一。volume 是非负成交量，允许停牌零量。
缺字段、未知字段、非法 OHLC、空数组均拒绝。
整数通过 Rust JSON 文件读取，不经 JavaScript 浮点转换；未来跨端协议须采用精确字符串或约束安全整数。
输出只含记录数与首尾时间戳，不生成信号、收益或交易结论。

任务是一次性本地进程，成功退出 0，输入/IO 失败非零退出。文件路径只在本地 CLI 使用。
CPU 计算后续在此进程中运行，不能直接放入 API 的 Tokio HTTP 执行路径；
接入任务调度时另行定义有界并发、取消、幂等、持久化与恢复。

## 迁移约束

Python Core 仍是现有算法事实来源。逐个迁移用例并使用相同输入、时点、规则版本做差分。
一级、二级、三级趋势必须共用同一核心函数，通过 level 选择来源与结果；
来源固定为技术折线 → 一级 → 二级。禁止按级别复制判定公式或在展示端补算。
未来的复用缓存身份必须包含数据版本、规则版本、参数、级别和对应时点。
交易资格、结构、确认状态各自保留；不能用没有资格替代趋势结构判断。

2026-10-23 已完成格式、Clippy编译与workspace测试；样例命令和生产运行未验证，详情见 progress.md。
