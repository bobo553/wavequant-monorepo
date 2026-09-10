# Repository Progress

## Last Updated

2026-09-10

## Current Objective

当前没有进行中的功能；功能状态以 `feature_list.json` 为准。

## Current State

- 应用已按 `apps/servers/*`、`apps/mobiles/*`、`apps/webs/*`、`apps/tools/*` 分类。
- 仓库级规则、按领域规则、功能状态、会话交接及验证入口均已建立。
- Python 通用、测试与打包规则已纳入根路由和 Harness，可按任务渐进加载。
- Web 规则区分移动优先 H5 与 PC 管理后台，Admin 作为桌面专项场景追加约束。
- 模板项目创建 CLI 已支持 H5、Admin、API 和 Mobile 四类 workspace。
- 公开 Roadmap 使用 Now、Next、Later 与 Delivered 表达方向，精确执行状态继续由 `feature_list.json` 独占维护。
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

## Blockers

无。

## Recommended Next Step

从 `ROADMAP.md` 的 Next 候选中选择方向，完成讨论和范围定义后，在 `feature_list.json` 中创建下一个 backlog Feature。
