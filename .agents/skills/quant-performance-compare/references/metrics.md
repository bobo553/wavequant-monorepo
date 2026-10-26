# 指标口径与缓存边界

`quant_performance_probe` 直接调用当前数组路径，计算前在内存准备 RuleCandle；`analysis_ms` 从 TrendAnalyzer 初始化开始，到四级、资格和 IndexedAnalysis 导出结束。它不包含探针进程启动、输入读取/解析、前端 VO 投影、编码、缓存读写。`process_wall_ms` 才包含完整探针进程生命周期。

| 字段 | 含义 |
| --- | --- |
| init/polyline/level1/level2/level3/qualification/output_ms | 分阶段墙钟，不把嵌套 phases_ms 再次相加 |
| phases_ms.levelN.n_memory | 新版本每级 N 记忆簿耗时；levelN.qualification 当前包含记忆簿及资格消费者 |
| projection_ms | 生产 trend_state::response，包括前端状态与独立行情快照映射 |
| encode_json_ms / encode_wire_ms | 内部 IndexedAnalysis 普通 JSON / 节点表编码 |
| cache_write_ms / cache_read_ms | 每样本独立 SQLite 的首次建表写入 / 随后命中读取，含生产节点表、zlib及校验；不等于线上热点库延迟 |
| peak_rss_bytes | 整个探针进程峰值工作集，含编码、投影及缓存；不等于量化函数净分配 |
| indexed.json / indexed.wire.json | 内部索引结果，含新 N 记忆；不是完整 Worker Envelope/SystemResult |
| view.json / view.wire.json | 生产前端 VO 普通 JSON / 节点表；timing 固定零，避免测量噪声改变字节数 |
| snapshot.wire.json | 同源独立复权行情快照；不应因为 N 规则改变而变化 |
| memory.json | 内部 N 记忆，旧版无字段时文件以 null 作哨兵，但统计为 0B，避免把 4B 占位符算作旧记忆；不把它当成前端数据 |
| backtest cache payload | 实际 ArtifactCache 压缩 BLOB；数据是 {view,pipeline:null} 的共同控制样本，不含真实进度流水线 |

探针源码相同不代表规则相同：资格等业务逻辑差异应列出区间起止、规则、条数及确定性。数据量用 B/KiB，耗时用 ms，RSS 可用 MiB；gzip/zlib 使用固定参数和可重复的 mtime，区分估算与实测。

## 缓存变化

- Redis 复权日线：`storage/cache.rs` 和 `application/adjusted_market_cache.rs`，key 绑定数据源/股票/start/日线/复权策略及 SDK 身份；算法版本不在行情 key 内。N 规则更新无需清空行情 Redis。无物理 TTL 仍需检查收盘新鲜度及容量，不把永久缓存误认为永不更新。
- 进程内：复权行情 64MiB/256项，当前默认数据源缓存；服务重启会变冷。N 记忆是算法计算结果，不属于该行情缓存。
- SQLite ArtifactCache：独立行情 `trend-bars` 按 dataVersion 复用；`backtest` 按引擎/配置/输入身份隔离。新版本不会命中旧结果；物理旧条目是否删除要查实际调用，不能只看到 clear_backtests_for_engine 函数定义就宣称已执行。默认容量 4GiB，按 created 淘汰，当前并非读命中更新顺序的严格 LRU。
- 正式缓存观测只读统计 namespace 条数、压缩 payload 总字节、文件/WAL 大小；SQLite 以只读连接打开。Redis 优先 ZCARD 与 sizes 元数据汇总，不 KEYS 全库、不转储行情或秘密。前后统计若遇后台更新需注明，不把自然更新算为本轮代码变化。

性能对照报告应分别写算法耗时、编码/缓存成本、数据量增加的来源及是否值得，附股票与日期覆盖范围。未经多股票或浏览器验证，不宣称全市场性能或页面实际首屏耗时。

段六态新增字段对照：structure_equal 比较包含新增列的完整级别对象；geometry_equal 仅去掉明确新增的 trendStates/trendStateKnownIndices 两列后比较全部原有字段，不能忽略坐标、路径、端点状态或可知时钟。n_memory_equal 独立比较完整首 N 记忆。新增列导致完整结构摘要不同，不等于原有趋势几何变化；报告同时保留两种结论。

v29共享N口径：`n_memory_total` 是统一共享表全阶段；`n_memory_shared` 包含共享准备及N推进，`levelN.n_memory` 主要是该级趋势绑定/候选查找，不能再把四级之和当作N总耗时。`levelN.qualification` 从v29起仅为资格消费者，v28该字段曾包含本级N，两版不能直接横比此子阶段。`nRecords` 与 `nMemory`（级别/趋势引用）共同组成完整N结果，体积和结果对照必须两者同时记录；资格仍读当日确认快照，不能用最终N表重建历史资格。
