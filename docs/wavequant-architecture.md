# WaveQuant Workspace Architecture

## Decision

WaveQuant 使用三个可独立验证的 workspace：

```text
apps/webs/wavequant-web
        │ HTTP /api/*
        ▼
apps/servers/wavequant-api
        ├──── Python public imports ────▶ packages/wavequant-core
        ├──── durable shared metadata ─▶ MySQL / PostgreSQL (optional)
        └──── cache and coordination ──▶ Redis (optional)
```

## Responsibilities

- `wavequant-web` 拥有页面、浏览器交互、Lightweight Charts 依赖、静态构建和浏览器测试。
- `wavequant-api` 拥有 HTTP 协议、Host/Origin 校验、安全响应 Header、静态资源映射、进程入口，以及 SQL/Redis Infrastructure 适配器。
- `wavequant-core` 拥有策略、行情读取、回测、研究、运维、查询模型和研究 CLI，不依赖 HTTP 或 Web 资源。

## Data storage decision

| 数据类型                       | 默认存储                   | 原因                                             |
| ------------------------------ | -------------------------- | ------------------------------------------------ |
| 单机事件账本、封存研究证据     | SQLite + 文件产物          | 本地快、零服务依赖、便于复制和确定性复现         |
| 共享运行索引、状态和研究元数据 | PostgreSQL（推荐）或 MySQL | 事务、并发访问、查询、备份与权限由共享数据库负责 |
| 行情与大型回测产物             | Parquet/CSV/对象存储路径   | 避免把大时序数据和二进制产物塞入行式事务库       |
| 查询缓存、分布式锁、短期协调   | Redis                      | 低延迟且数据可从事实源重建                       |

生产默认推荐 PostgreSQL。WaveQuant 的研究元数据经常包含可查询的半结构化配置，PostgreSQL `jsonb` 可直接使用 GIN 索引；行情和运行历史扩大后也可采用按时间范围的声明式分区。MySQL 8.4 同样具备原生 JSON、窗口函数和 InnoDB 分区，若团队已有成熟 MySQL 备份、监控和高可用体系，继续使用 MySQL 通常比更换数据库更划算；但 MySQL 的 JSON 索引通常需要抽取生成列，研究型查询的演进成本略高。

SQL 适配器不在 import 或服务启动时自动建表；运维方必须显式运行 `pnpm --filter wavequant-api infra:init`。Redis 读取或写入失败不会阻断 Dashboard 的事实源查询，健康端点则明确报告 `degraded`。

## Trade-offs

- 保留现有 Python + 原生浏览器 JavaScript 技术栈，避免仅为目录拆分引入 Next.js/NestJS 行为重写。
- 本地模式仍由 API 提供同源页面与接口，避免 CORS 和额外网络部署；Web 同时可以生成独立 `dist` 静态产物。
- API 暂时保持 loopback-only，不宣称具备公网部署所需的认证、授权、限流和可观测能力。
- SQLAlchemy 隔离数据库驱动差异：MySQL 使用 PyMySQL，PostgreSQL 使用 psycopg 3；当前初始表结构保持两者可移植，PostgreSQL 下 JSON 字段自动采用 JSONB。
- Redis 是性能与协调层，不是权威数据层；本地 Compose 开启 AOF 只改善开发重启体验，不改变这一职责边界。

## Migration and rollback

原 `apps/tools/wavequant` 的受控源码按职责移动到三个 workspace，研究分发继续保留 `wavequant` 导入包名。若需回滚，可在 Git 中恢复目录移动；本地数据与结果始终不参与迁移或版本控制。

## Verification

三个 workspace 分别执行 lint、typecheck、test:unit 和 build；随后执行根 Harness 与 `pnpm verify`。API 单元测试覆盖 HTTP 契约、安全边界、SQL 仓储、Redis 缓存与降级；设置 `WAVEQUANT_TEST_DATABASE_URL` 和 `WAVEQUANT_TEST_REDIS_URL` 后可运行真实服务集成冒烟测试。Web E2E 仅在具备封存研究结果与本地 TDX 数据时运行。
