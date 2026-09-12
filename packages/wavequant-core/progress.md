# Progress

## Current State

- `MONOREPO-011` 已完成：WaveQuant 已拆分为 Web、API 与核心包。
- 本 workspace 只保留 Python 领域、研究、回测、数据、运维与研究 CLI，不再启动 HTTP Server 或读取 Web 静态资源。
- `MONOREPO-020` 已完成：四层实现已细分为 13 个功能子域，仓库消费者已统一到唯一规范导入路径，扁平兼容模块已经移除。

## Completed

- 已将源项目当前未忽略工作树复制到 `apps/tools/wavequant`，未复制源 Git 历史和本地生成数据。
- Python 包目录已迁移到 `src/wavequant`，Web 依赖改由 pnpm workspace 管理。
- 已统一项目根路径解析，移除脚本中的临时 `sys.path` 注入，并保持 CLI、研究配置和浏览器静态资源路径可用。
- 已增加 Ruff、mypy、pytest、Python 构建、Web 语法检查和跨平台 Python 启动脚本。
- 根 Harness 已支持发现 Python workspace，CI 已安装锁定的 Python 开发依赖并验证 WaveQuant。
- HTTP Server 和 Dashboard 启动入口已迁入 `apps/servers/wavequant-api`，浏览器资源与 Web 测试已迁入 `apps/webs/wavequant-web`。
- Python 分发名为 `wavequant-core`，导入包名保留为 `wavequant`，标准 console script 保留为 `wavequant`。
- MONOREPO-016 重新核对源当前工作树：67 个源 Python 模块全部映射，目标额外的 `project_paths.py` 仅处理 `src` 布局路径；源 505 项契约拆分为 Core 483 项与 API HTTP/可视化契约。
- 将价格行为、折线、多级趋势、N 形、六态、洗盘、扭转、分级入场和集成策略迁入纯 `domain` 层；将回测、研究、验证和证据聚合迁入 `application` 层。
- 将文件、SQLite 事件、缓存、通达信、数据清单、证券主数据和项目路径迁入 `infrastructure` 层；图表几何进入 `interfaces` 层。
- 仓库内普通导入、延迟导入、测试 patch 字符串和 API 消费方已迁到最细功能路径；架构测试禁止领域层反向依赖外层。
- 为趋势结构、末跌高/末升低、分级趋势状态机和筛选漏斗补充了因果日期、冻结关键逻辑、严格突破与未知状态等关键注释；完整分层边界记录在 `docs/architecture.md`。
- 领域层细分为 `models`、`market_structure`、`market_state`、`strategies`；应用层细分为 `analytics`、`trading`、`governance`；基础设施细分为 `market_data`、`persistence`、`filesystem`；接口层细分为 `charts`、`research_tools`、`screening` 与 CLI。

## Verification

- 迁移前：`python -m unittest discover -s tests -v`，505 项通过。
- 迁移前：`node --test web/tests/*.test.mjs`，37 项通过。
- 迁移后：`pnpm lint`、`pnpm typecheck`、`pnpm test:unit` 与 `pnpm build` 均通过；其中 Python 505 项、Web 37 项测试通过。
- CLI 冒烟：`pnpm python -- -m wavequant.interfaces.cli --help` 通过，本地 Lightweight Charts SDK 路径检查通过。
- 全仓：`pnpm harness:check` 与 `pnpm verify` 通过。
- 拆分后核心门禁：Ruff 与渐进式严格 mypy 通过，483 项核心测试通过，sdist/wheel 构建通过。
- 根 `pnpm harness:check` 与 `pnpm verify` 在拆分后通过。
- 使用迁移前封存结果和 `D:\TDX` 的真实浏览器验收已覆盖行情、策略、回测、扫描、幅度比较、绩效、订单与多级趋势线。
- 分层重构基线：Core Ruff 与原 483 项测试通过。
- 分层重构后：Core Ruff、渐进式严格 mypy、491 项测试以及 sdist/wheel 构建通过；`python -m wavequant.interfaces.cli --help` 冒烟通过。
- 消费方回归：`wavequant-api` 33 项通过，1 项按运行环境跳过。
- 功能子域细分后：CLI 冒烟和 503 项 Core 测试通过；随后移除兼容层的基线为 501 项 Core 测试通过。
- 规范路径升级后：Core 的 Ruff、严格路径 mypy、501 项测试与构建通过；wheel 结构检查确认 85 个 Python 文件均位于规范分层路径，14 个代表性模块可直接从 wheel 导入，旧扁平模块不再发布。
- API 最终消费方门禁：Ruff、严格 mypy、33 项通过/1 项环境跳过的测试与构建全部通过。

## Risks and Next Steps

- 真实数据不提交 Git；其他机器运行浏览器 E2E 前需通过 API 的 `--root` 与 `--tdx-root` 显式提供封存结果和通达信目录。
- 既有 Python 模块保留历史代码风格；Ruff 先执行 Pyflakes 级检查，mypy 对新增路径模块启用严格模式，后续可按变更范围逐步扩大严格检查。
- 包根只允许 `__init__.py`，层级根目录只允许有意维护的入口；架构测试会阻止扁平模块重新出现。
- `packages/async-ui` 已按当前仓库规范接入 workspace 锁文件和进度结构，仓库级 Harness 阻塞已经解除。
