# Repository Progress

## Last Updated

2026-09-12

## Current Objective

当前没有进行中的 Feature；MONOREPO-017 已完成，原股票项目 Web 工作台已由 `apps/webs/wavequant-web` 的 Next.js App Router 与 React 直接渲染，并可由单一开发命令连同本机 API 启动。

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
- 详细进度由各 pnpm workspace 根目录的 `progress.md` 维护。

## What Completed

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

## Verification Evidence

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

## Blockers

无。

## Recommended Next Step

从 `ROADMAP.md` 的 Next 候选中选择方向，完成讨论和范围定义后，在 `feature_list.json` 中创建下一个 backlog Feature。
