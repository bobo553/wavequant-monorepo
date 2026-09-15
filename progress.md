# Repository Progress

## Last Updated

2026-09-15

## Current Objective

MONOREPO-064 已完成：AkShare 结构快照在后台重建期间持续可用，并在完整发布后原子切换。

## Current State

- 应用已按 `apps/servers/*`、`apps/mobiles/*`、`apps/webs/*`、`apps/tools/*` 分类。
- 仓库级规则、按领域规则、功能状态、会话交接及验证入口均已建立。
- Python 通用、测试与打包规则已纳入根路由和 Harness，可按任务渐进加载。
- Web 规则区分移动优先 H5 与 PC 管理后台，Admin 作为桌面专项场景追加约束。
- 模板项目创建 CLI 已支持 H5、Admin、API 和 Mobile 四类 workspace。
- 公开 Roadmap 使用 Now、Next、Later 与 Delivered 表达方向，精确执行状态继续由 `feature_list.json` 独占维护。
- WaveQuant 已拆分为 `packages/wavequant-core`、`apps/servers/wavequant-api` 与 `apps/webs/wavequant-web` 三个独立边界，并纳入 pnpm、Python 与 CI 质量门禁。
- WaveQuant API 已具备可选 SQLAlchemy 与 Redis Infrastructure 层；SQLite 封存证据、共享 SQL 元数据和可重建 Redis 缓存保持职责分离。
- WaveQuant Web 的 `/` 与 `/research` 由 Next.js App Router 和 React 19 直接渲染完整研究工作台；Tailwind CSS 4、共享 shadcn 原语和 ECharts 全景市场页保留在 `/market`。
- Harness 可同时发现并验证 Node 与 Python workspace 的进度文件。
- 结构搜索复用 Core 已确认地标，支持任一/单类结构、一级至三级、最近 1/5/20 个交易日，并保持发生日与确认可用日分离。
- 结构信号读模型同时绑定通达信数据指纹与 Core 全包算法指纹；行情或算法任一变化都会生成新快照，失败计算不会覆盖已完成版本。
- 市场数据仓库以 AkShare 为默认主源，按交易日从备用源补齐缺失结果；重复日期保留主源值，并对外提供实际来源与补齐数量。
- 买点与结构信号分别使用 `wavequant_buy_signal_snapshots`、`wavequant_structure_signal_snapshots` 事实表和 `signal:buy:v1:*`、`signal:structure:v3:*` Redis 缓存；结构缓存按市场组合隔离，前端不能启动计算任务。
- AkShare 结构 Worker 使用八个稳定互斥分片，只补齐当前行情日与算法版本尚未发布的股票；停牌股票按统一市场基准日归档，完成后常驻进程保持空闲。
- 详细进度由各 pnpm workspace 根目录的 `progress.md` 维护。

## What Completed

- MONOREPO-064 修正 AkShare Worker 的市场分区日期，逐分片写入全市场预期数量；结构查询在行情日或算法重建时持续服务最近一份不晚于请求日的完整代际，新代际完整后才原子切换。首次建库从零分片起即返回 rebuilding 空结果或已发布部分，不在交互请求中计算结构。

- MONOREPO-063 完整重启统一开发服务以加载最新策略引擎，并将 TDX 当前股票回测从普通 45 秒请求上限拆分为独立五分钟时限；代理纯文本错误现在显示稳定 HTTP 服务错误，不再暴露 JSON 解析异常。

- MONOREPO-062 在 Core 统一地标发布层撤销确认前低被后续低点严格跌破的一级至三级空翻多高点；等低重测保留、因果回放在跌破确认前保留。国芳集团 2026-04-21 与 2026-05-14 二级高点已按 2026-05-07、2026-07-15 的确认截面依次失效，结构/多周期快照版本同步更新并由常驻 Worker 重建。

- MONOREPO-060 修复 `wavequant-web dev` 未加载 `.env.infrastructure` 的根因；统一启动现在等待本地 MySQL/Redis 健康、幂等建表，并持续托管 API、TDX 信号 Worker 与八个 AkShare 结构分片 Worker。

- MONOREPO-059 将研究图表卡片改为纵向弹性布局，K 线宿主填满标题、OHLC 与历史回放之外的剩余高度；Lightweight Charts 的实际根容器由 autoSize 同步跟随宿主。

- MONOREPO-058 将日、周、月、季、年改为可点击和键盘操作的单行 Tab；服务端以数据/算法版本发布 K 线与画线完整快照到独立 SQL 表和 Redis 热缓存，Worker 支持全目录、指定股票与稳定分片；Web IndexedDB 以 ETag 增量同步并保留 40 份最近 bundle，离线时不会混配画线。

- MONOREPO-057 将 17 项图层开关、图例、折线口径和三级趋势摘要整合到图表顶部单行工具栏；详细定义通过悬停/聚焦提示和可固定顶层浮层呈现，支持外部点击与 Escape 关闭、焦点恢复和窄屏自适应，不再持续占用 K 线高度。

- MONOREPO-056 为 AkShare 与通达信行情浏览增加日、周、月、季、年 K 线；服务器按自然周期聚合首开、最高、最低、末收与成交量并使用最后实际交易日，理论结构读取同一聚合序列；Web 保存周期偏好、同步回放与未完成周期提示，封存回测保持日线。

- MONOREPO-055 将 AkShare 分母纠正为 5,562 只当前有效股票，消除逐股完整 TDX 目录扫描、趋势前缀重复遍历、失效新浪源逐股超时及 SQL 历史版本先分页后去重的漏数；分片从 201 个已发布股票断点续算到 5,562 / 5,562，五市场查询失败 0，4 只截止日后上市股票以明确空快照记录。

- MONOREPO-054 为 AkShare 与通达信目录增加稳定 ETag 和 IndexedDB 持久缓存；首次加载写入完整目录，后续加载以 `If-None-Match` 校验，未变化返回 304/0 字节，变化时原子替换，临时断网时继续使用已缓存目录。

- MONOREPO-053 将模糊的“已覆盖 296 只”改为“后台重建中：已发布 296 / 5,904 只；当前筛选市场 4,850 只”；真实 Chromium 在 2026-09-07 转多查询中确认覆盖增至 342，GET 不含当前股票，Console 与失败请求均为 0。全目录 Worker 固定该回放日继续逐股原子发布。

- MONOREPO-052 新增 `bullish_turn` 结构契约：Core 只在交替确认后的首个严格收盘上穿产生信号，SQL/Redis 快照随算法指纹重建；图表以分级箭头标记突破 K，并从原空翻多高点画虚线。中大力德真实通达信链路验证为 2025-01-24 收盘 49.28 → 54.21 突破 49.56；2026-09-14 AkShare 页面已返回永兴股份、星网锐捷等 5 条转多结果。

- MONOREPO-051 将空多交替低点的历史图窗范围与因果可知截止日分离；真实 AkShare 查询中，贤丰控股二级低点从无标识恢复为 `Ⅱ 空多交替低点 · L28 2.98`，并保留 2026-08-17 确认日证据。

- MONOREPO-050 为结构读模型增加五类 A 股市场多选，默认上证、深证、创业板；市场归属、名称含 `*`/`＊` 排除和缓存隔离均由 API 完成。真实 Chromium 请求返回 200，覆盖 2,419 只已发布股票，默认组合发现 9 个结构且无 Console/Network 错误。

- MONOREPO-049 将 AkShare 结构快照按股票原子发布并在服务端聚合为市场级列表；结构查询不带当前股票且跨买点策略复用。真实 Chromium 在 `lecture_v3` 下返回 200 ready、无 Console/Network 错误，全目录 Worker 覆盖数持续增长并能隔离单股失败。

- 完成 MONOREPO-001：目录重构与工程规范整理。
- 同步 Docker、CI、文档、Metro、Tailwind、补丁与锁文件路径。
- 完成 MONOREPO-002：迁移 architecture、algorithms、backend、frontend、data-warehouse、operations 与 ai 全部规则，并适配 NestJS/TypeScript 技术栈。
- Harness 现会校验 32 份规则完整性和 Markdown 路由目标。
- 完成 MONOREPO-003：仓库名称、项目链接和测试期望已切换到 `bobo553/monorepo-template`，且本地只保留该 `origin`。
- 完成 MONOREPO-004：新增 `apps/webs/admin` B 端管理后台模板，包含响应式应用壳、主题、经营仪表盘、ECharts 图表、业务空状态和测试入口。
- Web 设计系统新增 Badge、Card、Input、Separator 与 Skeleton 通用原语，Admin 未创建重复的 `components/ui`。
- 收尾修复依赖与测试告警：React 规则兼容 ESLint 10，Vitest 使用 Vite 8 原生路径解析，移动端移除已弃用测试渲染器。
- 完成 MONOREPO-005：新增 Python 通用、pytest 测试与 PyPA 打包规则，覆盖环境依赖、类型、异常、日志、并发、安全、CLI 和发布门禁。
- 完成 MONOREPO-006：将偏 Admin 的规则优化为 PC Web 通用规范，并新增 Admin 数据表、权限、批处理和危险操作专项约束。
- 完成 MONOREPO-007：新增 `@repo/create-project` CLI，提供交互/参数化创建、模板列表、dry-run、安全复制、端口分配和模板字段改写。
- 完成 MONOREPO-008：将通用 Web 模板重命名为 `apps/webs/h5`，统一 workspace、环境变量、Docker、脚手架和文档命名，同时保留扁平 Web 目录。
- 完成 MONOREPO-009：新增中文公开 `ROADMAP.md`，使用现在、下一步、未来与已交付基础表达方向，并通过 Harness 校验必要章节和 Feature ID 引用。
- 完成 MONOREPO-010：将 WaveQuant 当前未忽略工作树迁入 `apps/tools/wavequant`，采用 Python `src` 布局、pnpm 管理浏览器依赖，并接入统一脚本、CI、Harness 与文档。
- 完成 MONOREPO-011：核心研究、HTTP 适配器和浏览器工作台已按职责拆分，建立单向依赖、独立构建和统一本地启动入口。
- 完成 MONOREPO-012：WaveQuant API 新增 MySQL/PostgreSQL 共享运行索引、Redis 缓存与锁、健康检查、显式初始化/索引命令和本地 Compose 环境。
- 完成 MONOREPO-013：按 WaveQuant v2 设计稿还原全景市场看盘页，将原型拆为 CSP 兼容的同源资源，并保留 `/research` 工作台。
- 完成 MONOREPO-014：参考 Admin 工程结构，将全景市场看盘迁移到 Next.js App Router、共享设计系统和 features 分层，并让 API 安全提供静态导出产物。
- 完成 MONOREPO-015：补齐 Next.js 八个看盘视图的共享上下文、搜索、钻取、观察组、异动雷达、多股同屏、复盘、导出和个性化设置，并保留原始 v2 六大模块的完整兼容入口。
- MONOREPO-016 已完成实现与专项验收：源项目 505 项 Python、37 项 Web 契约全部建立目标映射，原 Web 恢复为默认入口，全景页迁至 `/market`，API 补齐 `wavequant-core[tdx]` 现场回测运行依赖。
- 完成 MONOREPO-017：原研究页拆分为 React 业务组件与客户端运行时边界；`wavequant-web dev` 同时启动 Next.js 与只读 API，并修复开发代理下买点扫描 POST 的本机来源校验。
- 完成 MONOREPO-038：中大力德 `2022-08-03 H70 49.56` 在旧二级末跌高突破、54.42% 场景回撤和非正式二级低点确认三项证据齐备后，于 `2023-02-06` 升级为正式二级高点。
- 完成 MONOREPO-039：Core 为一、二、三级正式空翻多结构输出因果确认高点，Web 在图上提供默认开启且可独立关闭的分级标识；中大力德 `2022-08-03 H70 49.56` 从 `2022-08-09` 起可见。
- 完成 MONOREPO-040：空翻多地标统一要求确认高点严格突破冻结末跌高；一级不再把普通 `down → up` 波向切换误标为空翻多。
- 完成 MONOREPO-041：Core 输出各级已确认空多交替低点及完整前置证据，Web 在低点价格下方提供默认开启且可独立关闭的分级标识。
- 完成 MONOREPO-043：新增独立可取消的结构搜索任务与工作台入口，可搜索空翻多高点或空多交替低点并点击定位图表证据。
- 完成 MONOREPO-044：新增结构信号后台 Worker、SQL 完成快照、Redis 查询缓存与只读 GET；前端不再逐股计算或轮询任务。
- 完成 MONOREPO-047：增加统一市场数据端口及 AkShare/通达信适配器，目录、行情、讲义折线与多级趋势共用稳定仓库契约，并在主源不可用或滞后时安全补齐。
- 完成 MONOREPO-048 实现：新增买点/结构双读模型、行情与算法因果版本、完成后原子发布、一次刷新/周期 Worker 和只读查询 API；AkShare 按显式股票范围执行有界任务。

## Verification Evidence

- MONOREPO-063 Web ESLint、TypeScript、6 项 Vitest、81 项 Node 契约、Next.js 生产构建、Harness 与补丁空白检查通过；真实浏览器成功加载贵州茅台 V3 2026-01-01 至 2026-09-07 当前股票回测。

- MONOREPO-060 Web 门禁通过 ESLint、TypeScript、6 项 Vitest、79 项 Node 契约、Next.js 构建及专项 Chromium；真实重启后基础设施为 `ok`，同源结构查询返回 200，覆盖 5,562 / 5,562 只并返回 91 条结果，Console 与失败请求为 0。

- MONOREPO-059 Web 门禁通过 ESLint、TypeScript、6 项 Vitest、75 项 Node 契约、Next.js 生产构建和专项 Chromium 回归；1440×900 下图表卡片 876px、K 线宿主及实际图表均 672px，390×844 下 K 线 569.22px，Console、失败请求和横向溢出均为 0。

- MONOREPO-058 门禁通过：API Ruff、严格 mypy、61 项 pytest（另 1 项外部集成跳过）和 Python 构建；Web ESLint、TypeScript、6 项 Vitest、75 项 Node 契约、Next.js 构建及串行 14 条 Chromium 流程。真实 MySQL/Redis 建表与健康检查通过，贵州茅台首次发布五周期，重复刷新发布 0、幂等跳过 5、失败 0；浏览器验证周期键盘切换、view/theory 版本一致、IndexedDB 写入和 304 复用。

- MONOREPO-057 Web 门禁通过 ESLint、TypeScript、6 项 Vitest、72 项 Node 契约、生产构建与 14 条 Chromium 流程；真实浏览器确认桌面工具栏高 37px、K 线距卡片顶 95px，fixed 顶层浮层开关前后不改变图表位置，390px 窄屏无整页横向溢出且 Console 无错误。

- MONOREPO-056 三端门禁通过：Core Ruff、严格 mypy、542 项 pytest 和构建；API Ruff、严格 mypy、58 项 pytest（另 1 项跳过）和构建；Web ESLint、TypeScript、6 项 Vitest、71 项 Node 契约、生产构建及 13 条 Chromium 流程通过。真实通达信五周期返回 5999/1263/301/101/26 根 K 线，AkShare 周线返回 1263 根，5,562 只结构快照保持 `ready`。

- MONOREPO-055 三端门禁通过：Core 542 项，API 53 项（另 1 项跳过），Web 6 项 Vitest 与 70 项 Node 契约通过，lint、严格类型检查、生产构建和 12 条 Chromium 流程通过。真实 2026-09-07 服务确认 5,562 / 5,562、失败 0、截止日后上市跳过 4，八个动态常驻分片重启后均返回空工作集。

- MONOREPO-048 专项门禁：Core 538 项、API 45 项（另 1 项外部集成跳过）、Web 6 项 Vitest 与 64 项 Node 契约通过，三端 lint/严格类型检查与生产构建通过；真实 MySQL/Redis 健康检查、显式建表、贵州茅台双信号发布及 3003 代理查询通过。

- MONOREPO-047 三端门禁与 `pnpm verify:quick` 通过：Core Ruff、严格 mypy、538 项测试及构建；API Ruff、严格 mypy、41 项测试（另 1 项环境跳过）及构建；Web ESLint、TypeScript、6 项 Vitest、63 项 Node 契约、Next.js 构建和 11 条 Chromium 主流程。真实贵州茅台请求确认 AkShare/TDX 均使用同一领域结构契约，AkShare 主目录由 TDX 补充 342 只证券。
- MONOREPO-045 全仓 `pnpm verify:quick` 通过；Core 531 项、API 41 项（另 1 项环境跳过）、Web 6 项 Vitest 与 63 项 Node 契约通过，三端生产构建通过。真实 AkShare 1.18.94 目录返回 5,562 只沪深京 A 股，贵州茅台在线原始不复权日线共 6,003 根、更新至 2026-09-11；浏览器确认数据源标识、按需目录、成交量单位及回测/扫描禁用边界。
- MONOREPO-045 AkShare 延迟修复：主接口探测收紧为 3 秒并熔断 10 分钟，优先回退官方新浪日线、腾讯接口保留为最终兜底；真实 API 首次请求 2.85 秒、后续请求 0.98 秒，浏览器确认格力电器 7,009 根日线、29,800,684 股成交量并且无错误。全仓 `pnpm verify:quick` 再次通过，Core 532 项、API 41 项（另 1 项跳过）、Web 6+63 项测试通过。
- MONOREPO-001 的 `pnpm verify`：通过目录重构后的 lint、类型检查、单元测试及生产构建。
- MONOREPO-002 的 `pnpm harness:check`：通过，覆盖 2 个功能、12 个 workspace 和 28 份规则。
- MONOREPO-002 的 `pnpm verify`：通过 lint、类型检查、单元测试及生产构建。
- Harness 通用结构审计：100/100。
- MONOREPO-003 的 `pnpm verify`：仓库链接变更后全量验证通过。
- `git push -u origin main`：成功创建并推送新远端 `main`。
- MONOREPO-004 的 Chromium E2E：桌面概览、业务导航与移动端侧栏 3 条流程通过。
- MONOREPO-004 的 `pnpm verify`：13 个 workspace 的 Harness、lint、类型检查、单元测试和生产构建全部通过。
- `pnpm install`：依赖图无 peer dependency 冲突；全量单测不再出现 Vitest 配置和 React 测试渲染器告警。
- MONOREPO-005 的 `pnpm verify:quick`：Harness、lint、类型检查及 18 个单元测试全部通过。
- Python 官方参考链接检查：Python、PyPA、Ruff、mypy 与 pytest 的 11 个链接均可访问。
- Python 规则加入后 Harness 通用结构审计仍为 100/100。
- MONOREPO-006 的 Web 消费者验证：`h5`、`admin` 与 `@repo/design-system-web` 的 lint、类型检查和生产构建通过，H5/Admin 共 5 个单元测试通过。
- PC Web 规则加入后 Harness 通过，覆盖 6 个功能、13 个 workspace 和 32 份规则。
- MONOREPO-007 的 CLI 门禁：lint、类型检查、13 个单元测试和构建通过；四种真实模板 dry-run 与非法路径拒绝通过。
- 新增 CLI 后 `pnpm verify:quick` 与全仓构建通过，Harness 覆盖 7 个功能、14 个 workspace 和 32 份规则，全仓共 31 个单元测试。
- MONOREPO-008 的 H5/Admin 模板 dry-run 均解析到 `apps/webs/*` 正确目标；相关 workspace 门禁和 `pnpm verify` 通过，Harness 覆盖 8 个功能。
- MONOREPO-009 中文化后的文档格式、`pnpm harness:check` 与 `pnpm verify:quick` 通过；Harness 通用结构审计五个子系统均保持 5/5。
- MONOREPO-010 迁移前基线：505 项 Python 测试与 37 项 Web 单元测试全部通过。
- MONOREPO-010 workspace 门禁：Ruff、渐进式严格 mypy、Web 语法检查、505 项 Python 测试、37 项 Web 单元测试及 sdist/wheel 构建全部通过。
- MONOREPO-010 完成后的 `pnpm verify`：10 个功能、15 个 Node workspace、1 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及构建全部通过。
- MONOREPO-011 的 workspace 门禁：核心 483 项、API 23 项、Web 37 项测试通过，三个 workspace 的 lint、类型检查和构建通过。
- MONOREPO-011 完成后的 `pnpm verify`：11 个功能、17 个 Node workspace、2 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及构建全部通过。
- MONOREPO-012 workspace 门禁：Ruff、严格 mypy、31 项测试和 Python 构建通过；Compose 静态解析及显式建表/健康检查冒烟通过。
- MONOREPO-012 完成后的 `pnpm verify`：12 个功能、17 个 Node workspace、2 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及构建全部通过。
- MONOREPO-013 Chromium 验收：1920 桌面市场总览与涨停阶梯切换通过，390 窄屏无整页横向溢出，且无页面异常和外部网络请求。
- MONOREPO-013 workspace 门禁：Web 40 项单元测试、API 31 项测试通过且 1 项外部服务测试按配置跳过，两个 workspace 的 lint、类型检查与构建全部通过。
- MONOREPO-013 完成后的 `pnpm verify`：13 个功能、17 个 Node workspace、2 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及构建全部通过。
- MONOREPO-014 Web 门禁：ESLint、严格类型检查、1 项 React 组件测试、40 项兼容单元测试、Next.js 静态导出和 Playwright 端到端验证全部通过。
- MONOREPO-014 API 门禁：Ruff、严格 mypy、31 项测试通过且 1 项按环境跳过，Next.js `out`、研究兼容页和路径遍历保护契约通过。
- MONOREPO-014 完成后的 `pnpm verify`：14 个功能、17 个 Node workspace、2 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及构建全部通过。
- MONOREPO-015 Web 门禁：ESLint、严格类型检查、2 项 React 组件测试、41 项兼容/结构测试和 Next.js 静态导出全部通过。
- MONOREPO-015 Chromium 验收：3 条流程覆盖 Next.js 八视图关键交互、搜索、设置、导出、复盘、390 窄屏，以及经典 v2 六大模块和八个市场页签，全程无页面异常。
- 原始 `WaveQuant_全景看盘_v2.html` 与 `/wavequant-v2-classic.html` 规范化文本逐字相同，均为 219695 个字符。
- MONOREPO-015 完成后的 `pnpm verify`：15 个功能、17 个 Node workspace、2 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及 15 个生产构建任务全部通过。
- MONOREPO-016 源基线：`E:\WorkSpace\股票` 的 505 项 Python 测试与 37 项 Web 测试全部通过；目标测试按 Core 483 项、API HTTP/可视化契约和 Web 兼容契约覆盖。
- MONOREPO-016 真实数据 Chromium 验收：23 项完整工作台流程、4 项幅度比较、6 项慢扫描传输与 3 项二级趋势连续性全部通过；使用 5,895 只有日线数据的本地股票和源封存结果，无外部网络依赖。
- MONOREPO-016 完成后的 `pnpm verify`：16 个功能、17 个 Node workspace、2 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及 15 个生产构建任务全部通过。
- MONOREPO-017 真实数据 Chromium 验收：23 项完整工作台、4 项幅度比较、6 项慢扫描传输、3 项二级趋势连续性和 4 项 Next 页面流程全部通过。
- MONOREPO-017 完成后的 `pnpm verify`：17 个功能、17 个 Node workspace、2 个 Python workspace 和 32 份规则的 Harness、全仓 lint、类型检查、单元测试及 15 个生产构建任务全部通过。
- MONOREPO-038 的全仓 `pnpm verify:quick` 通过；Core 510 项、API 33 项（另 1 项环境跳过）、Web 56 项单测及 9 条 Chromium 主流程通过。
- MONOREPO-039 的真实 API 回放确认中大力德二级空翻多高点在 `2022-08-08` 不可见、`2022-08-09` 首次可见；浏览器可显示、查看证据并独立开关该标识。
- MONOREPO-039 workspace 门禁：Core 512 项、API 33 项（另 1 项环境跳过）、Web 6 项 Vitest 与 57 项 Node 契约通过；三端 lint、类型检查和生产构建通过，串行 9 条 Chromium 主流程通过。
- MONOREPO-040 真实接口审计覆盖中大力德、首创环保、华夏银行和上海电力：三个级别所有地标均有末跌高且严格突破，非法计数均为 0；中大力德一级错误候选由 128 个收敛为 6 个真实突破点。
- MONOREPO-041 全仓快速门禁通过；Core 516 项、API 33 项（另 1 项环境跳过）、Web 6 项 Vitest 与 58 项 Node 契约通过，串行 9 条 Chromium 主流程验证中大力德 L71 只能从 `2022-09-01` 起显示。
- MONOREPO-042 全仓门禁通过；Core 518 项、API 33 项（另 1 项环境跳过）、Web 6 项 Vitest 与 59 项 Node 契约通过，串行 9 条 Chromium 主流程验证中大力德 `H71 33.89` 只能从 `2022-09-08` 起显示。
- MONOREPO-044 全仓快速门禁通过；Core 525 项、API 40 项（另 1 项环境跳过）、Web 6 项 Vitest 与 61 项 Node 契约通过，三端构建及串行 10 条 Chromium 主流程通过。
- MONOREPO-046 已启用 AkShare 当前股票买点与结构信号分析；真实 API 对贵州茅台分别完成 1/1，Chromium 点击回归通过且无页面异常或失败请求。
- MONOREPO-046 完成后的 `pnpm verify` 通过；Core 534 项、API 41 项（另 1 项环境跳过）、Web 6 项 Vitest 与 63 项 Node 契约及全仓生产构建全部通过。

- MONOREPO-061 将转多信号从突破收盘价的精确价格锚点改为 `belowBar`，箭头随对应 K 线最低点定位；收盘价详情与空翻多高点虚线保持不变，Web 门禁、专项 Chromium 和真实页面检查通过。

## Blockers

无。

## Recommended Next Step

从 `ROADMAP.md` 的 Next 候选中选择方向，完成讨论和范围定义后，在 `feature_list.json` 中创建下一个 backlog Feature。
