# WaveQuant API

WaveQuant 的本地只读 HTTP 适配器。它依赖 `@wavequant/core` 提供研究查询能力，并为 `wavequant-web` 提供同源 API 与静态资源；默认只绑定 `127.0.0.1`。

API 还提供可选的 SQL 与 Redis 基础设施：

- SQLAlchemy 统一实现研究运行元数据的创建、幂等更新、读取和最近列表，支持 `mysql+pymysql`、`postgresql+psycopg` 与本地测试用 `sqlite+pysqlite`。
- Redis 保存可重建的 JSON 缓存并提供有界分布式锁；`/api/catalog` 会在 Redis 可用时缓存，在 Redis 故障时回退到事实源。
- `/api/infrastructure/health` 只返回服务状态和数据库类型，不返回主机、用户名、密码或连接 URL。
- SQL/Redis 都不配置时，Dashboard 保持原来的本地行为。

## 本地运行

在 monorepo 根目录完成依赖初始化后执行：

```powershell
pnpm --filter wavequant-web build
pnpm --filter wavequant-api dashboard
```

默认打开 `http://127.0.0.1:8765`，进入从原 `E:\WorkSpace\股票\web` 迁移的研究工作台；同一页面也可通过 `/research` 访问。Next.js 全景市场看盘保留在 `/market`。API 只从 `apps/webs/wavequant-web/out` 建立静态文件清单，不提供目录浏览或工作区外文件。研究结果从 `packages/wavequant-core/results/operations_v1` 读取，可通过 `--root`、`--tdx-root`、`--port` 与 `--web-root` 覆盖。

通过 `wavequant-web dev` 联合启动时，开发启动器会加载本 workspace 的 `.env.infrastructure`、等待本地 Compose MySQL/Redis 健康、幂等建表，并同时托管 API、通达信信号 Worker 和 AkShare 结构分片 Worker。8765 保持为独立 API 进程；访问其根地址会以 `307` 临时重定向到 `http://127.0.0.1:3003/`，API 路由仍由 Next.js 同源代理。重定向目标必须通过 `--web-url` 显式配置，且只接受带端口的 loopback HTTP origin，避免开放重定向。

本地长驻 API 与 Worker 会监听 `wavequant-core` 和本 API 的 Python 源码。连续保存稳定约 2 秒后，启动包装器先结束其拥有的旧 Python 进程树，再启动新进程；策略版本一致性校验仍保留。生产和预发布环境不启用源码监听。一次性初始化、刷新、测试和构建命令不受影响。修改启动包装器本身后需手动重启一次开发命令，以加载新的包装器。

复用迁移前机器上的现有封存结果与通达信行情时，可显式指定数据目录：

```powershell
pnpm --filter wavequant-api python -- -m wavequant_api.cli --root "E:\WorkSpace\股票\results\operations_v1" --tdx-root "D:\TDX" --port 8765
```

API 直接依赖 `wavequant-core[akshare,tdx]`，完整安装会包含现场复权回测所需的 `pytdx`、`pandas`，以及在线行情浏览使用的 AkShare。AkShare 提供只读行情、讲义结构绘图和当前股票信号分析；不会逐股抓取在线全市场，也不进入封存回测或交易证据。可用 `--akshare-timeout 30` 调整单次调用上限，或用 `--disable-akshare` 显式关闭。历史日线先短时探测 `stock_zh_a_hist`；该上游不可用时优先回退到速度更快的官方 `stock_zh_a_daily`，最后才使用按年份请求的 `stock_zh_a_hist_tx`。响应会标明实际接口。

图表统一读取 `GET /api/market-timeframe?source=akshare&symbol=sh.600519&asof=YYYY-MM-DD&timeframe=1d`；原有 view/theory 路由保留兼容。周期支持 `1d`、`1w`、`1mo`、`3mo`、`1y`。周/月/季/年由服务器从规范日线按自然周期聚合，使用首开、最高、最低、末收、成交量求和，并以周期内最后一个实际交易日作为时间。统一接口把 K 线与同周期画线作为同一版本 bundle 返回，并支持 ETag 条件请求。正常行情由 Worker 提前生成；部署过渡期或历史回放缺少特定截止日时，API 会在服务器端计算并原子补写，浏览器仍只消费完整快照，不自行计算或混配画线。信号查询仍使用只读 `GET /api/structure-signals` 与 `GET /api/buy-signals`。

## MySQL 与 Redis

复制本地配置并替换两个密码占位符：

```powershell
Copy-Item apps/servers/wavequant-api/.env.infrastructure.example apps/servers/wavequant-api/.env.infrastructure
pnpm --filter wavequant-api infra:up
```

把同一文件中的连接 URL 注入当前终端后，显式初始化数据库并检查依赖：

```powershell
$env:WAVEQUANT_DATABASE_URL = "mysql+pymysql://wavequant:<password>@127.0.0.1:3306/wavequant?charset=utf8mb4"
$env:WAVEQUANT_REDIS_URL = "redis://127.0.0.1:6379/0"
pnpm --filter wavequant-api infra:init
pnpm --filter wavequant-api infra:index
pnpm --filter wavequant-api infra:check
pnpm --filter wavequant-api dashboard
```

`infra:init` 是显式、可重复执行的初始建表动作，服务导入或启动不会擅自修改数据库结构。`infra:index` 把已封存本地运行的紧凑目录幂等写入共享 SQL，CSV、行情和其他大型产物仍留在文件存储。本地容器的数据位于具名 Volume；`infra:down` 不删除 Volume。

### 买点与结构信号预计算

买点与结构信号采用独立后台读模型，不在浏览器请求中抓行情或运行 Core。首次部署或需要立即刷新通达信全市场时运行一次：

```powershell
pnpm --filter wavequant-api signals:refresh
```

服务器长期运行下面的独立 Worker。它默认每 300 秒检查一次通达信日线目录；没有变化时只比较轻量指纹，不重复计算，发现新交易日或文件版本变化后自动生成新快照：

```powershell
pnpm --filter wavequant-api signals:watch
```

生产环境应由 systemd、Windows 服务、Supervisor 或容器编排器同时托管 API 与 Worker。也可以由外部调度器在行情落库完成后调用 `signals:refresh`。可通过 `--structure-refresh-interval 60..86400` 调整检查间隔，通过 `--structure-run`、`--structure-variant`、`--structure-asof`、`--signal-scenario` 和 `--signal-start` 明确计算口径。AkShare 结构 Worker 默认遍历完整目录，每只股票完成后独立原子发布，因此中断后可直接复用已完成分片：

```powershell
pnpm --filter wavequant-api structures:watch:akshare
```

临时验证或定向补算时可重复传入 `--signal-symbol` 限定股票子集；AkShare 买点仍要求显式股票范围，避免意外启动高成本全市场买点计算。

每份快照同时绑定：

- 行情指纹：证券代码、名称、最新交易日以及日线文件的修改时间和大小；
- 算法指纹：`wavequant-core` 全部 Python 源码的内容摘要；
- 查询口径：运行、策略版本、数据源、股票范围、执行场景、回放日期和回测起点。

因此每日行情更新会产生新快照；部署包含算法或策略配置变更的代码并重启 Worker 后，也会因算法指纹不同主动重建。通达信全市场结果一次性原子发布；AkShare 按股票分片原子发布，市场查询聚合每只股票的最新完成版本，任何中断都不会暴露半份股票结果。结构结果写入 `wavequant_structure_signal_snapshots`，买点结果写入 `wavequant_buy_signal_snapshots`。Redis 分别使用 `signal:structure:v2:*` 与 `signal:buy:v1:*` 命名空间，且始终可以由 SQL 重建。

前端通过只读接口按最近 1/5/20 个交易日过滤同一份 20 日完整快照；结构接口还可按信号类型和趋势级别缩小范围。AkShare 结构查询不接收当前股票作为筛选范围，而是返回服务器已发布覆盖范围内所有匹配股票。若当天或当前算法版本尚无任何完成分片，接口返回带 Worker 操作提示的 `503`，不会悄悄回退到在线计算。

### K 线周期预计算

图表周期使用独立的 `wavequant_market_timeframe_snapshots` 读模型。主键 `snapshot_id` 由数据源、股票、请求日期、周期、`data_version` 与 `algorithm_version` 共同生成；查询索引使用 `(source, symbol, timeframe, requested_asof, algorithm_version, updated_at)`。`payload` 内同时保存 `view` 与 `theory`，避免蜡烛和结构线跨版本拼接。`resolved_asof` 明确最后一个实际交易日，`created_at/updated_at` 用于发布审计；Redis `market:timeframe:v1:<snapshot_id>` 只作为可重建热缓存。原始行情仍只存在行情仓库，不能把 JSON 读模型或 Redis 当事实表。

初始化数据库后，可一次刷新全目录并长期监听数据版本变化：

```powershell
pnpm --filter wavequant-api timeframes:refresh
pnpm --filter wavequant-api timeframes:watch
```

默认使用 AkShare；可传 `--timeframe-source tdx`。定向补算可重复传入 `--timeframe-symbol`，大目录可通过 `--timeframe-shard-count 4 --timeframe-shard-index 0..3` 分片。一次股票行情读取会派生五个周期；已存在相同数据与算法版本时直接跳过，行情修订或算法升级则发布新快照。

若采用推荐的 PostgreSQL，在 API 虚拟环境中安装 `postgres` extra，并使用显式 psycopg 3 URL：

```powershell
apps/servers/wavequant-api/.venv/Scripts/python.exe -m pip install -e "apps/servers/wavequant-api[postgres]"
$env:WAVEQUANT_DATABASE_URL = "postgresql+psycopg://wavequant:<password>@127.0.0.1:5432/wavequant"
```

## 数据职责

- `packages/wavequant-core` 的 SQLite 事件账本继续负责单机、离线、可封存的研究证据，不因接入共享服务而迁移或失效。
- MySQL/PostgreSQL 保存多进程或多人共享的运行索引与元数据；大行情、Parquet/CSV 和封存产物不塞入 JSON 字段。
- Redis 只保存缓存、锁和短期协调状态。即使开启 AOF，也不把 Redis 当交易记录或研究证据的唯一事实源。
- 生产环境优先考虑 PostgreSQL；MySQL 8.4 是完整支持的兼容选项，适合团队已有 MySQL 运维体系的情况。具体取舍见仓库的 WaveQuant 架构说明。

## 边界

- HTTP、Host/Origin 校验、响应安全 Header 和静态资源映射属于本 workspace。
- 数据库连接池、SQL 仓储、Redis 缓存和外部依赖健康检查属于本 workspace 的 Infrastructure 层。
- 策略、回测、行情读取和查询模型属于 `packages/wavequant-core`。
- 页面和浏览器测试属于 `apps/webs/wavequant-web`。

`GET /api/tdx-backtest` 支持可选 `net_reward_risk_filter=true|false`（缺省 `false`，不接受重复或非布尔字符串）；写入执行配置和回测缓存键，仅控制次开盘含费净盈亏比不足的拒单门槛，不改变信号或除权价格。
