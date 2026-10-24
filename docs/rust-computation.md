# Rust 计算 worker 与诊断缓存

本轮按用户要求只迁移代码，未运行构建、格式化、测试、运行探针、行情请求或回测；以下描述代码契约，不是互通或行为等价的验证结论。

## 周期和层级

`timeframe` 标识 K 线周期，与 `level` 分别保存。`level=0` 是技术折线，`1/2/3` 是一级、二级、三级趋势；四级使用同一周期的输入。日、周、月聚合属于行情入口，worker、缓存和诊断显示不会根据层级切换周期。

`WorkerRequest::Analyze` 保留 `AnalysisRequest.identity.timeframe`。当前 `System` 与 `SystemWithSteps` 是日线账户入口，协议明确标记 `1d`。`CalculationStepCache::new` 要求 identity 内有显式 timeframe，不根据产品编号或 level 推导。API 的日线 Recorder 只接收明确 `1d` 的身份。

## 子进程协议

量化 CLI 的隐藏 `internal-worker` 入口使用标准输入/输出的长度帧与 zlib，不调用 Python。每个请求包含协议版本、完整请求 SHA-256、周期和引擎 SHA-256。引擎指纹由构建脚本基于量化 crate 全部源码、构建脚本及依赖锁定信息生成，父子进程不匹配时拒绝执行。

- 进程数量限定 1～8，每个进程串行消费请求；待处理任务队列容量等于进程数，原始请求总预算 24 MiB，单请求不超过 8 MiB。
- 传输压缩帧不超过 8 MiB，解压结果不超过 64 MiB；读、写与结果通道都有界，子进程不读 stdin 也不会阻塞父进程的取消和超时处理。
- 外部取消以 100 毫秒间隔传递；执行期限默认 1800 秒，终止时关闭子进程。Windows 子进程及峰值内存采集辅助进程均隐藏窗口。
- 只有提交阶段明确的容量不足允许调用方在原串行入口执行。子进程崩溃、EOF、通道断开、规则指纹或结果身份不符都返回错误，不作为容量不足吞掉。
- `SystemWithSteps` 传输统一策略入口产生的逐日 `SystemStep`，包含当时完整 frames 与 trading 观察；不会根据百分比重建结构。明细无法编码或发送时返回失败，不能静默省略明细后声明完整。
- 峰值工作集读取 Linux `/proc/<pid>/status` 或 Windows `PeakWorkingSet64`；不可读取时返回未知。每个 PID 的观测表最多保留 32 项，同时保留所有已观察 worker 的峰值上界。

API 显式配置量化 CLI 可执行文件时采用该池；未配置或提交容量不足时通过同一个原生策略入口执行。API 和 CLI 之间没有运行期兼容探针，本轮未启动进程验证。

## 24 类诊断产物

`CalculationPlan` 保留用户请求的产物顺序，实际领域调度仍为同一根 K 线内折线 → 一级 → 二级 → 三级，并消费同一份 N、六态及账户判断；24 个编号不是 24 次独立算法，也不能按完成数量计算进度。

Recorder 接收真实 `SystemStep`，摘要记录每日观察；开启详细诊断后，趋势记录完整 KernelFrame 的变化版本，含 points、publication、events 及各项来源证书。事件先按原始证据、股票、周期及层级去重，再附加首次 observed_index，避免把持续存在的历史事件逐日重复计数。未收到的产物保留 not_recorded，不能伪造发生记录。

详细诊断块目标 512 KiB，趋势点采用自包含引用表，单块扩展大小、引用表规模及证据缓存均有限制。`EvidenceOwner` 创建的不可变证据持有独立 owner/node 证书；缓存中的 JSON 内容摘要、点引用和已知时点均在读取时检查。底层 ArtifactCache 容量为 4 GiB，诊断写入失败会保留 partial 并停止记录，不改变领域策略结果。

身份覆盖 schema、明确周期、完整请求配置、源 sessions 摘要、bar_count 与引擎指纹。复用证书还包含 step、trend_level、through_index 和块引用摘要：不同周期、不同输入版本、不同参数或引擎不能共用；不能把晚于调用方 as-of 的块当作当前可见结果；第 24 项账户结果不通过结构复用证书恢复。

API 在同日账户处理及源身份核对完成后封存最终结果。提前返回、取消或最终发布前失败会把未封存诊断留为 partial；记录不重新运行趋势或交易规则。缓存可供查看既有诊断产物，不绕过当前时点的正式领域计算。

## 四层结构前缀复用

`StructurePrefixCache` 与上述诊断缓存分开：它实现统一分析器的 `StructureCache` 接口，保存精确已知前缀的完整 `[KernelFrame; 4]`。键和校验体同时绑定引擎 SHA、分析器版本、股票及源版本、复权口径、timeframe、完整配置、through_index 和 prefix_digest；帧内容另有 SHA-256。不同周期或更晚前缀不能作为当前输入的缓存命中。

`TrendAnalyzer` 命中后恢复四层正式结构，跳过该精确前缀的结构计算（含生命周期和方向发布证书），继续当天资格、风险、信号及账户推进。恢复时由缓存适配器检查四层次序、有限价格和已知位置，分析器再核对实际 K 线极值及坐标。完整 publication 与 evidence 原样保存；不从展示几何反推结构。

原生 API 与 worker 的 System 两个入口均通过 `run_system_backtest_with_cache` 注入该接口，路径为 API 运行根目录下的 `.rust-structure-cache`。worker 请求显式携带可选根目录，并在结果 metadata 返回该实例实际命中、未命中、写入及跳过计数；分析器响应有 `structureCache` 计数。实例创建或磁盘读写失败均保留原计算路径。

结构缓存的磁盘压缩载荷预算为 4 GiB，单份四层结构 JSON 上限 32 MiB，包含校验体的解压上限 64 MiB，完整键编码上限 1 MiB，读锁等待为 500 毫秒。超过预算直接跳过缓存，不截断结构或更换规则。它不保存账户、未成交订单、持仓、交易状态机，也不通过较早缓存跳过中间交易日。

与之相对，`CalculationStepCache` 仍仅复用 24 类诊断产物的存储块和证书，减少重复编码/写盘；该诊断缓存本身不会跳过领域计算。
## 来源与待验证范围

对应 Python 源码为 `infrastructure/computation/trend_workers.py`、`process_memory.py`、`infrastructure/persistence/calculation_step_cache.py`、`calculation_chunk.py`。原生 Rust 接口位于 `infrastructure/computation/`、`persistence/calculation_*.rs`；API 适配位于 `application/calculation.rs` 与 `application/computation.rs`。

仍待用户允许后验证编译和真实进程互通、取消/超时、Windows 峰值内存获取、缓存损坏/淘汰及与 Python 的行为边界。超大单请求或明细帧会显式拒绝；当前 worker 没有通过磁盘共享大对象的传输协议。结构前缀缓存只恢复精确前缀的四层结构产品，不能恢复完整 TrendAnalyzer 交易状态或账户执行状态；日线账户入口不声称支持周/月撮合。