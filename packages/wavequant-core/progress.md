# Progress

## Current State

- `MONOREPO-038` 已完成：中大力德 `2022-08-03 H70 49.56` 按“突破旧二级末跌高 → 场景回撤 → 非正式二级反转确认”的证据链升级。
- `MONOREPO-037` 已完成：正式二级关键位长期未破时，由 Python 输出最后正式点之后完整、可因果回放的一级发展路径。
- `MONOREPO-036` 已完成：二级开放尾段的旧低被收盘严格跌破后，末跌高会在尾部高点完成确认时下移到该高点。
- `MONOREPO-035` 已完成：在不改正式三级反转的前提下，补全最后正式点之后的已确认二级发展路径及待决尾部。
- `MONOREPO-034` 已完成：三级正式反转点确认后，由 Python 输出到当前已确认二级极值的发展中三级尾段，且不改变正式三级结构。
- `MONOREPO-032` 已完成：二级旧低被收盘严格跌破后，由 Python 生成可追溯的末跌高换锚事实。
- `MONOREPO-031` 已完成：一级趋势跨路径衔接已提升为 Python 领域层正式数据，图表与二、三级趋势统一读取同一组趋势点。
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
- MONOREPO-031 将相邻一级讲义路径之间已确认的真实高低极值纳入正式趋势路径，保留来源日期与因果确认时间；华夏银行 `2026-01-23 L 6.32` 已成为正式一级低点，并进入二级趋势计算。
- 理论结果缓存版本改为覆盖整个 `wavequant` Python 包，领域规则变化后不会继续复用旧的理论响应。
- MONOREPO-032 为二级结构增加收盘严格跌破旧低的因果换锚事件：盘中下影越线和收盘相等不成立，且新低必须在跌破日之前已经确认为二级点；华夏银行从 `2025-07-10 H33 8.72 / 2026-01-23 L34 6.32` 换锚到 `2026-04-02 H34 7.52 / 2026-06-29 L35 6.34`。
- MONOREPO-034 为三级结构增加独立的 `developing_strokes` 契约：从最后一个正式三级点出发，选择其后已确认且届时可知的同方向二级极值；等价极值保留最早出现点，并显式标记为 `display_only/developing`，不写入正式三级点、波段计数、策略或回测。
- MONOREPO-035 将单一发展端点升级为完整检查路径：正式三级点之后先串联已确认的二级内部转折，最后尚未完成三级确认的尾部保留全部正式二级点直到最新点；每个点保存来源位置、角色和因果日期，正式三级结构不变。
- MONOREPO-036 扩展二级末跌高的开放尾段换锚：若旧二级低点已被收盘严格跌破，尾部二级高点已经由更低的一级低点确认，但该低点尚未升级为正式二级点，则以该确认低点作为只读证据，在高点可知后将末跌高换锚到尾部高点；不会伪造新的二级点。
- MONOREPO-037 抽取通用层级发展路径生成器，二级与三级统一从最后正式点、确认来源位置和下一级已确认结构生成只读路径；内部转折与待决尾部逐点保留来源坐标和因果日期，但不会写入正式点或成为更高级别输入。
- MONOREPO-038 为二级高点增加受限升级通道：翻空为多高点必须突破上一正式二级末跌高，首轮已确认回撤保持较高且严格小于 `2/3`，并等待一级波段递推确认出非正式二级低点；升级日取最终证据可知日，不能倒填。

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
- MONOREPO-031 Core 门禁通过 Ruff、严格 mypy、503 项测试和 sdist/wheel 构建；华夏银行真实接口返回一级 `2026-01-23 L 6.32`（因果可用日 `2026-03-23`），二级序列从该点起算。
- MONOREPO-031 API 消费方门禁通过 Ruff、严格 mypy、33 项通过/1 项环境跳过的测试与构建；根 Harness 校验 31 个 Feature、18 个 Node workspace、2 个 Python workspace 和 32 份规则通过。
- MONOREPO-032 Core 门禁通过 Ruff、严格路径 mypy、504 项测试和 sdist/wheel 构建；API 消费方通过 Ruff、严格 mypy、33 项通过/1 项环境跳过的测试与构建；根 Harness 校验 32 个 Feature、18 个 Node workspace、2 个 Python workspace 和 32 份规则通过。
- MONOREPO-034 Core 门禁通过 Ruff、严格路径 mypy、505 项测试和 sdist/wheel 构建；上海电力正式三级点保持 3 个，发展中尾段为 `2008-11-06 L2 2.68 → 2015-06-02 H12 34.50`，端点于 `2015-07-15` 因果可知。
- MONOREPO-035 Core 门禁通过 Ruff、严格路径 mypy、506 项测试和 sdist/wheel 构建；上海电力正式三级点仍为 3 个，发展路径为 14 个点并结束在 `2026-05-29 H27 22.35`（`2026-07-17` 可知）。
- MONOREPO-036 Core 门禁通过 Ruff、严格路径 mypy、507 项测试和 sdist/wheel 构建；上海电力 `2026-04-28 L26 16.73` 于 `2026-06-23` 被收盘严格跌破后，在 `2026-07-17` 将二级末跌高换锚到 `2026-05-29 H27 22.35`，确认低点证据为 `2026-07-14 L283 13.26`；华夏银行既有正式二级低点换锚保持不变。
- MONOREPO-037 Core 门禁通过 Ruff、严格路径 mypy、508 项测试和 sdist/wheel 构建；中大力德正式二级点保持 17 个并结束于 `2022-05-27 L9 13.16`，发展路径为 30 点并结束于 `2026-09-01 H129 66.07`（`2026-09-07` 可知），三级正式结构和输入保持不变。
- MONOREPO-038 Core 门禁通过 Ruff、严格路径 mypy、510 项测试和 sdist/wheel 构建；中大力德在 `2023-02-03` 仍只有 17 个正式二级点，到 `2023-02-06` 才升级 `2022-08-03 H70 49.56`，最新正式二级点增至 46 个并继续到 `2026-07-06 H23 88.60`。

## Risks and Next Steps

- 真实数据不提交 Git；其他机器运行浏览器 E2E 前需通过 API 的 `--root` 与 `--tdx-root` 显式提供封存结果和通达信目录。
- 既有 Python 模块保留历史代码风格；Ruff 先执行 Pyflakes 级检查，mypy 对新增路径模块启用严格模式，后续可按变更范围逐步扩大严格检查。
- 包根只允许 `__init__.py`，层级根目录只允许有意维护的入口；架构测试会阻止扁平模块重新出现。
- `packages/async-ui` 已按当前仓库规范接入 workspace 锁文件和进度结构，仓库级 Harness 阻塞已经解除。
