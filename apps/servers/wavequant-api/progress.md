# Progress

## Current State

- `MONOREPO-038` 已完成：API 透明返回 Core 生成的二级高点交替升级证据和最终因果可用日。
- `MONOREPO-037` 已完成：API 透明返回 Core 生成的二级发展路径、内部转折数和待决尾部点数。
- `MONOREPO-036` 已完成：API 透明返回 Core 生成的二级开放尾段末跌高换锚事件及其一级确认低点证据。
- `MONOREPO-035` 已完成：API 透明返回完整三级发展路径、内部转折数与待决尾部点数。
- `MONOREPO-034` 已完成：API 原样提供 Core 生成的发展中三级尾段，不在 HTTP 层重算结构规则。
- `MONOREPO-012` 已完成：本 workspace 具备可切换 SQL 持久化与 Redis 缓存基础设施。
- `MONOREPO-013` 已完成：静态资源白名单支持默认全景看盘页，并保留原研究工作台 `/research` 路由。
- `MONOREPO-014` 已完成：静态服务读取 Next.js `out` 清单，并继续将 `/research` 映射到兼容工作台。
- `MONOREPO-016` 已完成：默认入口承载原研究工作台，新增 `/market` 全景页别名并补齐 TDX 现场回测运行依赖。
- `MONOREPO-017` 已完成：API 支持不依赖静态构建的开发模式，并接受显式声明的本机 Next.js 代理来源。

## Completed

- API-only 开发模式支持受限的 loopback Web 根地址：访问 8765 根路由时以非缓存 `307` 跳转到 Next.js，外部主机、路径、查询和无显式端口的重定向目标均拒绝。

- HTTP Server 与 Dashboard 启动入口已从核心包移入本 workspace。
- API 仅依赖核心包，并从独立 Web workspace 读取静态资源。
- 新增 `/research`、`/market.css` 与 `/market.js` 的受限静态资源映射和 HTTP 契约测试。
- 支持 Next.js `_next` 构建资源、旧研究兼容资源与安全的静态文件清单，不提供目录遍历。
- 新增 SQLAlchemy 研究运行仓储，支持 MySQL、PostgreSQL 与 SQLite 测试方言的幂等写入和查询。
- 新增 Redis JSON 缓存、短期锁、故障降级和不泄露连接信息的健康检查。
- 新增回环绑定的 MySQL 8.4 + Redis 8 Compose 环境，以及显式建表和健康检查命令。
- 将 `wavequant-core[tdx]` 设为 API 必需依赖，保证安装 API 后即可读取通达信除权除息并运行现场复权回测。
- 新增 `--api-only` 与可重复的 `--allow-origin` 参数；GET 与买点扫描 POST 统一校验显式回环代理来源。
- MONOREPO-034 保持 API 为透明消费边界：`tertiary_trends.developing_strokes` 与正式三级结构一同返回，字段、因果日期和显示语义均来自 Core。
- MONOREPO-035 延续透明消费边界：`developing_point_count`、`nested_turn_count`、`pending_point_count` 及逐点角色由 Core 生成，HTTP 层不压缩或补点。
- MONOREPO-036 延续透明消费边界：二级换锚事件的旧低、跌破日、新高、确认低点、来源级别与因果可用日均由 Core 生成，HTTP 层不补点或重算趋势。
- MONOREPO-037 延续透明消费边界：`secondary_trends.developing_strokes` 的来源位置、角色、计数和因果日期均由 Core 生成，HTTP 层不压缩、不补点、不把它传入三级趋势。
- MONOREPO-038 延续透明消费边界：`alternation`、`provisional_reversal`、旧二级关键位和 `available_at` 均由 Core 生成，HTTP 层不复制升级算法。

## Verification

- API-only 根路由回归通过：`GET http://127.0.0.1:8765/` 返回 `307` 和受限的 `Location: http://127.0.0.1:3003/`；跟随跳转得到 `200 text/html`，`/api/catalog` 继续返回 `200`。
- 本次修复通过 API Ruff、严格 mypy 与 pytest（33 项通过，1 项外部环境跳过）。

- Ruff、严格入口类型检查、23 项 pytest 和 sdist/wheel 构建通过。
- API CLI 帮助、默认 Web workspace 页面、Lightweight Charts 与第三方声明资源集成测试通过。
- 根 `pnpm harness:check` 与 `pnpm verify` 通过。
- MONOREPO-012 workspace 验证已通过 Ruff、严格 mypy、31 项测试（另 1 项外部服务集成测试按配置跳过）和 Python 构建。
- MONOREPO-012 完成后的根 `pnpm verify` 已通过全部 workspace 的 Harness、lint、类型检查、单元测试和构建。
- MONOREPO-013 workspace 验证已通过 Ruff、严格 mypy、31 项测试（另 1 项外部服务集成测试按配置跳过）和 Python 构建。
- MONOREPO-014 workspace 验证已通过 Ruff、严格 mypy、31 项测试（另 1 项外部服务集成测试按配置跳过）和 Python 构建；Next.js 资源、研究兼容页及路径遍历保护契约通过。
- MONOREPO-016 使用源封存结果和 `D:\TDX` 验证 5,895 只带日线股票、贵州茅台 2018–2026 现场回测、买点扫描、幅度比较与同源静态页面；工作台浏览器专项全部通过。
- MONOREPO-017 workspace 验证通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部服务集成测试按配置跳过）和 Python 构建；Next `3003` 代理的扫描 POST 契约已覆盖。
- MONOREPO-034 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；真实接口在最新日与 `2015-07-15` 回放日均返回上海电力同一条因果可见的发展中三级尾段。
- MONOREPO-035 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；最新上海电力接口返回 14 个发展点、8 个已确认内部转折及 4 个待决尾部点。
- MONOREPO-036 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；上海电力真实接口在 `2026-07-16` 尚未换锚、于 `2026-07-17` 因果可见地换锚到 `2026-05-29 H27 22.35`。
- MONOREPO-037 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；中大力德真实接口返回 17 个正式二级点与独立的 30 点发展路径，三级正式点仍为 3 个。
- MONOREPO-038 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；中大力德真实接口在 `2023-02-06` 首次返回升级后的 `2022-08-03 H70 49.56` 及完整三段证据。

## Risks and Next Steps

- 服务当前保持原有 loopback-only 安全边界，不作为公网 API 部署。
- 本机 Docker daemon 当前未运行；Compose 配置已通过静态解析，真实 MySQL/Redis 集成测试需在 Docker 可用后通过环境变量显式启用。
