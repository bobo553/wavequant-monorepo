# Progress

## Current State

- `MONOREPO-011` 已完成：WaveQuant 已拆分为 Web、API 与核心包。
- 本 workspace 只保留 Python 领域、研究、回测、数据、运维与研究 CLI，不再启动 HTTP Server 或读取 Web 静态资源。

## Completed

- 已将源项目当前未忽略工作树复制到 `apps/tools/wavequant`，未复制源 Git 历史和本地生成数据。
- Python 包目录已迁移到 `src/wavequant`，Web 依赖改由 pnpm workspace 管理。
- 已统一项目根路径解析，移除脚本中的临时 `sys.path` 注入，并保持 CLI、研究配置和浏览器静态资源路径可用。
- 已增加 Ruff、mypy、pytest、Python 构建、Web 语法检查和跨平台 Python 启动脚本。
- 根 Harness 已支持发现 Python workspace，CI 已安装锁定的 Python 开发依赖并验证 WaveQuant。
- HTTP Server 和 Dashboard 启动入口已迁入 `apps/servers/wavequant-api`，浏览器资源与 Web 测试已迁入 `apps/webs/wavequant-web`。
- Python 分发名改为 `wavequant-core`，兼容保留 `wavequant` 导入包名和 `wavequant` CLI。

## Verification

- 迁移前：`python -m unittest discover -s tests -v`，505 项通过。
- 迁移前：`node --test web/tests/*.test.mjs`，37 项通过。
- 迁移后：`pnpm lint`、`pnpm typecheck`、`pnpm test:unit` 与 `pnpm build` 均通过；其中 Python 505 项、Web 37 项测试通过。
- CLI 冒烟：`pnpm python -- -m wavequant.cli --help` 通过，本地 Lightweight Charts SDK 路径检查通过。
- 全仓：`pnpm harness:check` 与 `pnpm verify` 通过。
- 拆分后核心门禁：Ruff 与渐进式严格 mypy 通过，483 项核心测试通过，sdist/wheel 构建通过。
- 根 `pnpm harness:check` 与 `pnpm verify` 在拆分后通过。

## Risks and Next Steps

- 依赖真实通达信目录和历史结果的浏览器 E2E 仍需在具备本地数据时运行。
- 既有 Python 模块保留历史代码风格；Ruff 先执行 Pyflakes 级检查，mypy 对新增路径模块启用严格模式，后续可按变更范围逐步扩大严格检查。
