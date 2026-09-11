# Progress

## Current State

- `MONOREPO-012` 已完成：本 workspace 具备可切换 SQL 持久化与 Redis 缓存基础设施。

## Completed

- HTTP Server 与 Dashboard 启动入口已从核心包移入本 workspace。
- API 仅依赖核心包，并从独立 Web workspace 读取静态资源。
- 新增 SQLAlchemy 研究运行仓储，支持 MySQL、PostgreSQL 与 SQLite 测试方言的幂等写入和查询。
- 新增 Redis JSON 缓存、短期锁、故障降级和不泄露连接信息的健康检查。
- 新增回环绑定的 MySQL 8.4 + Redis 8 Compose 环境，以及显式建表和健康检查命令。

## Verification

- Ruff、严格入口类型检查、23 项 pytest 和 sdist/wheel 构建通过。
- API CLI 帮助、默认 Web workspace 页面、Lightweight Charts 与第三方声明资源集成测试通过。
- 根 `pnpm harness:check` 与 `pnpm verify` 通过。
- MONOREPO-012 workspace 验证已通过 Ruff、严格 mypy、31 项测试（另 1 项外部服务集成测试按配置跳过）和 Python 构建。
- MONOREPO-012 完成后的根 `pnpm verify` 已通过全部 workspace 的 Harness、lint、类型检查、单元测试和构建。

## Risks and Next Steps

- 服务当前保持原有 loopback-only 安全边界，不作为公网 API 部署。
- 本机 Docker daemon 当前未运行；Compose 配置已通过静态解析，真实 MySQL/Redis 集成测试需在 Docker 可用后通过环境变量显式启用。
