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

复用迁移前机器上的现有封存结果与通达信行情时，可显式指定数据目录：

```powershell
pnpm --filter wavequant-api python -- -m wavequant_api.cli --root "E:\WorkSpace\股票\results\operations_v1" --tdx-root "D:\TDX" --port 8765
```

API 直接依赖 `wavequant-core[tdx]`，完整安装会包含现场复权回测所需的 `pytdx` 与 `pandas`。

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
