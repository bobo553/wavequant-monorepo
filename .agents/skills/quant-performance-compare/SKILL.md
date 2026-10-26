---
name: quant-performance-compare
description: 对 WaveQuant Rust 量化修改做同输入前后性能对照，统计分阶段耗时、结果与传输体积、内存及缓存变化；适用于明确要求的性能统计与优化验收。
---

# Rust 量化性能与数据量对照

先遵守仓库 AGENTS.md 的开发验证授权。本技能不授予回测、真实浏览器、外部数据获取、缓存清空、提交或推送权限。用户要求前后统计时，优先离线冻结输入；浏览器和联网取数仅在当前授权范围内执行。

## 固定对照条件

- 修改前保存版本号、编译产物或从明确提交导出隔离源码；不切换或重置用户工作目录。隔离基线须保留 Cargo.toml、Cargo.lock、rust-toolchain.toml、crate build.rs 和 scripts/rust_sdk_worker.py；引擎哈希依赖这些文件，缺少文件时补齐同提交原件，不改 build.rs 绕过。隔离目录与原始测量放入 `.codex-runtime/`，不提交行情、二进制、SQLite 或采样产物。
- 两版采用同一 Rust toolchain、Cargo profile、features、同一探针源码和精确行情字节。记录原始 SHA、有效配置 SHA、引擎身份、股票、周期、复权口径及样本数。禁止拿旧报告绝对耗时作为本次基线。
- 使用 `apps/servers/wavequant-api-rs/examples/quant_performance_probe.rs`，此入口计算四级结构及既有资格，调用生产前端 VO 投影和 ArtifactCache 编码；不执行账户，不请求 SDK/HTTP。探针不隐式改策略参数，完整配置由冻结请求提供。
- 构建所需产物按任务授权执行。可以把同一探针复制到隔离旧源码；不要为了编译旧版改变业务逻辑。将两版 exe 冻结为不同文件后再采样，避免共享 target 覆盖基线。

## 执行与统计

```powershell
python -X utf8 .agents/skills/quant-performance-compare/scripts/compare.py --before-exe <旧探针exe> --after-exe <新探针exe> --input <冻结AnalysisRequest.json> --output .codex-runtime/<本轮目录>
```

默认每版预热一次，再交替采样五次；样本数可按当前授权调整，单次探针默认 120 秒超时后只终止该探针。脚本校验两版输入、有效配置及样本确定性，输出 `comparison.json` 与 `comparison.md`。仅需重新整理报告时可追加 --summarize-only 读取既有样本，脚本先核对 exe SHA，不重新计算股票。每个样本使用独立进程与独立缓存目录，保留 stderr，失败停止并记录真实错误，不自动扩大股票数量或重试完整回测。

若要统计运行服务缓存，使用 scripts/cache_snapshot.py 的 --sqlite、--redis-container、--redis-prefix 和 --output 参数只读采集；它只读取 SQLite namespace 汇总与 Redis LRU/sizes 元数据，不读取行情 payload。

需要解释阶段/字段、缓存口径或写诊断结论时读取 [metrics.md](references/metrics.md)。原始数据留 runtime，项目文档只写必要汇总、方法、局限和证据相对路径。

## 判断结果

- 先核对完整四级结构摘要、资格区间及 N 条数。新增段状态列时同时报告 geometry_equal（仅剔除明确新增两列后的原有字段对照）和 n_memory_equal；完整结构摘要包含新字段，不能用新增列导致摘要不同来声称几何改变。新增规则允许资格变化，但必须列出差异，不能用“更快”证明算法正确；结构改变时先定位原因。
- 同时报告增加与减少：`新-旧`、百分比、旧/新倍率；基线为零时百分比留空。中位数为主，附最小/最大；少量样本不作稳定尾延迟保证。
- 区分内部索引、实际前端 VO、独立行情、节点表与压缩后字节。gzip 估算不是浏览器实测流量，内部 IndexedAnalysis 也不是完整 Worker 报文。
- 缓存分别写明：复权 K 线 Redis、进程内缓存、SQLite 分析结果/行情快照、算法版本失效。只读采集真实缓存另获必要授权；不执行 FLUSH、批量 DEL、重建数据库或清空运行服务缓存。
- 生产缓存容量无法由一个样本直接推断；RSS 是整进程峰值，不能当作某函数的精确分配。结论必须标注是否含冷进程、序列化、压缩、文件读写、SDK、Worker 管道及 HTTP。

## 当前版本资格开关成本

用户明确要求比较资格新增成本时，使用 `qualification_cost_comparison` example；不修改生产调度。三组都计算四级趋势及六态：structure 仅在离线探针跳过 N/资格阶段，memory 运行首 N 维护并关闭全部买点资格，qualified 保留冻结输入的买点级别开关。关闭资格开关本身不会关闭四级 N 维护，不能把 memory 组称为纯结构。

```powershell
cargo +stable build --locked -p wavequant-api-rs --example qualification_cost_comparison
python -X utf8 .agents/skills/quant-performance-compare/scripts/qualification_cost.py --exe <冻结exe> --input <冻结请求> --output <新runtime目录>
```

默认每组3次预热、9次正式采样，每次独立进程，三组轮换且均衡每个位置。输出目录必须全新，超时只停止该探针。先核对全部四级含段状态的结果一致、memory/qualified的完整N记忆一致，各组资格输出确定；输入和引擎必须相同，只有资格开关及是否调用整个N/资格阶段不同。记录原始波动样本，不通过挑选最快记录得出结论。

`n_memory_ms` 是同一样本四级N维护计时之和；`qualification_and_mapping_ms` 是同一样本资格总阶段扣掉N维护，仍含共享映射/上下文，不能称为纯资格函数。跨组总中位数之差只是开关成本估计，不要求分项中位数可相加；保留最小最大，波动过大时说明限制。
