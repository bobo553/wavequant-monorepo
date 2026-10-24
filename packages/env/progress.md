# Progress

## Current State

2026-10-24 MONOREPO-362：按用户要求将复权后日线改为Redis market-kline-v2持久缓存，数据键/LRU/大小账本无TTL；按真实交易日历区分盘中60秒逻辑检查、16:10后每60秒最多16项串行收盘对账，启动后从Redis索引恢复。身份包含来源/固定Sina上游/股票/归一化起点/1d/复权规则/Python环境，不含末日、中国日期或二进制引擎；同一快照向历史请求提供可见前缀并重算前缀版本。容量默认512MiB有效载荷、2048身份、单项16MiB，Lua原子LRU和随机令牌有限租期锁、失去所有权不覆盖，后台不加热冷数据且不复活已淘汰身份；因子/历史修订完整重算并替换，旧已知日期缺失拒绝覆盖，上游缺收盘/停牌不造K线，五分钟重试。TDX继续本地文件读取，不混入在线缓存；分钟覆盖沿原流程。Redis连接6380/0、前缀wavequant:rust:local；原Docker命名卷保留，Redis服务独立重建，AOF everysec、1GiB maxmemory/noeviction，避免全局淘汰锁，MySQL未改。环境变量已同步@repo/env/.env.example，设计见API-rs/docs/kline-cache.md。Python源码、复权公式、趋势规则未改。最终启动构建build-redis-kline-active-bound.log成功1分17秒，API66376/8010、前端3003；自然页面启动日志kline_cache_redis_saved记录海洋王811112字节写入，非主动回测/行为探针。未运行测试套件、专项类型/lint/格式、浏览器、性能请求或Git检查；新增日历/尾部/历史版本/LRU回归源码未执行，真实收盘补齐、跨实例锁、容量/故障行为仍待验证。未提交合并，完整迁移保持in-progress。


`@repo/env` 集中加载和校验各应用环境变量。

2026-10-24 MONOREPO-362：新增 `wavequantDataSchema` / `wavequantDataEnv` 和 `.env.example` 中的 `WAVEQUANT_AKSHARE_PYTHON`。Rust 使用指定隔离环境运行 AKShare 数据子进程，并在使用处要求可执行文件绝对路径。本轮未运行 TypeScript 检查；下方 Verification 为此前历史记录，不能代表本次变更已验证。

## Completed

- 纳入 workspace 级进度与 Harness 管理。
- H5 应用使用 `h5Env`、`h5Schema` 和 `H5_PORT`，与 workspace 命名保持一致。

## Verification

- `pnpm verify`：lint、类型检查和包构建通过。
- 环境边界改为 `h5Env`、`h5Schema` 与 `H5_PORT` 后，全仓类型检查和构建通过。

## Risks and Next Steps

- 新变量需同步 `.env.example` 并避免跨应用误校验。
