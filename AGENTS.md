# 智能体协作规则

本文件是仓库级智能体规则的唯一入口。详细规范位于 `docs/agent/`，只按当前任务需要加载，避免一次读取全部规则。

## Git worktree 工作流

Git 代码开发默认在独立 worktree 中进行；每个会话只使用一个独立分支和 worktree，会话内后续功能继续复用，不按功能新建分支。特性分支命名为 `feat-中文名`，会话默认使用分支名；用户手动修改会话名后保留用户名称。每次提交的标题准确描述改动且少于 35 个字符；提交后逐个同步主分支、检查、复核并在本地合并，不自动推送 `main`。本仓库主分支是 `main`。完整流程及个人 Codex 全局规则的版本化副本见 `docs/agent/codex-global-AGENTS.md` 的“Git 多任务开发与本地合并流程”。该副本不会自动替换各开发者本机的 `~/.codex/AGENTS.md`。

## 语言与基本约定

- 与用户沟通、规则、进度和交接使用简体中文；代码标识符、文件名、协议字段和第三方 API 保留英文。
- 代码注释只解释职责、原因、边界和外部约束，不复述实现步骤。
- 命令默认从仓库根目录执行。JavaScript/TypeScript 包管理器只使用 pnpm，不生成 npm 或 Yarn 锁文件；Python 依赖按对应 `pyproject.toml` 声明和锁定。
- Node.js 版本以 `.nvmrc` 为准，pnpm 版本以根 `package.json#packageManager` 为准。

## 项目速览

| 区域                       | 技术栈                                       | 职责                      |
| -------------------------- | -------------------------------------------- | ------------------------- |
| `apps/servers/*`           | 当前为 NestJS、TypeScript、PostgreSQL        | API、任务和后台进程       |
| `apps/mobiles/*`           | Expo、React Native、NativeWind               | 移动端应用                |
| `apps/webs/*`              | Next.js、React、Tailwind CSS                 | B 端后台与 C 端 H5        |
| `apps/tools/*`             | 按工具选择，默认 TypeScript                  | 浏览器扩展、CLI、桌面工具 |
| `packages/contracts`       | Zod、TypeScript                              | 跨端数据契约              |
| `packages/design-system/*` | shadcn/ui、NativeWind                        | Web 与移动端设计系统      |
| `packages/env`             | Zod、dotenv                                  | 环境变量加载与校验        |
| `packages/config/*`        | ESLint、Vitest、Playwright、TypeScript、tsup | 共享工具配置              |

目录职责按顺序判断：无界面的运行单元进入 `servers`；原生移动端进入 `mobiles`；管理后台与浏览器 H5 页面进入 `webs`；可独立发布的开发者工具进入 `tools`；只有稳定跨应用复用的能力进入 `packages`。不得恢复 `apps/<workspace>` 的扁平布局。

Python 服务、Worker、CLI 和库也按上述职责落位，不单独创建技术栈顶级目录；每个独立 Python 项目以自己的 `pyproject.toml` 定义边界。

## 启动流程（Startup Workflow）

开始修改前按顺序执行：

1. 根据当前任务定位仓库根目录；开发阶段不运行 Git 状态或差异检查，已有改动默认属于用户，不覆盖、不回退。
2. 完整阅读本文件和根 `feature_list.json`；规划或排期任务再读 `ROADMAP.md`，有未完成交接时再读 `session-handoff.md`。
3. 读取目标 workspace 的 `package.json` 或 `pyproject.toml` 及 `progress.md`，确定开发边界；工作区清单命令只在实际提交阶段按需执行。
4. 在 `feature_list.json` 中只保留一个 `in-progress` 功能，确认其依赖与验收条件。
5. 读取距离目标文件最近的 `AGENTS.md`，再按下方路由读取必要规范。
6. 开发阶段只开发代码，不执行任何检查或验证命令；依赖初始化也不自动触发验证。所有检查统一留到实际提交阶段，按变更风险只执行一次必要验证。
7. 搜索已有实现、契约、设计系统组件和测试后再修改。

## 上下文路由

- 任意代码实现：读取 `docs/agent/general/rules.md`。
- 调试、测试设计、评审、重构、技术文档或依赖变更：额外读取 `docs/agent/general/quality-rules.md`。
- `apps/servers/*`：读取 `apps/servers/AGENTS.md`、`docs/agent/backend/rules.md` 和 `docs/agent/backend/nestjs-rules.md`，再按任务加载 API、数据、可靠性或安全专项。
- `apps/mobiles/*`：读取 `apps/mobiles/AGENTS.md` 和 `docs/agent/frontend/rules.md`。
- `apps/webs/*`：读取 `apps/webs/AGENTS.md` 和 `docs/agent/frontend/rules.md`；管理后台、桌面门户与数据工作台再读 `docs/agent/frontend/pc-web-rules.md`。
- `apps/tools/*`：读取 `apps/tools/AGENTS.md`；有 UI 时再读前端规范。
- Python 源码、`pyproject.toml`、类型检查或 Python 运行时任务：读取 `docs/agent/python/rules.md`，测试或打包任务再按其路由加载专项规则。
- Web 设计系统：额外读取 `packages/design-system/web/AGENTS.md`。
- 共享契约、环境变量或组件库：读取通用规范中的“共享包规则”。
- 应用/服务拆分、技术选型、容量、一致性或高可用：读取 `docs/agent/architecture/rules.md`，再按其路由加载系统设计或分布式专项。
- 非平凡算法、复杂度、数值、排序、搜索或随机过程：读取 `docs/agent/algorithms/rules.md`。
- 埋点、ETL/CDC、指标、治理、分析、可视化或实验：读取 `docs/agent/data-warehouse/rules.md`。
- CI/CD、容器、IaC、可观测、事故、发布或灾备：读取 `docs/agent/operations/rules.md`。
- 模型、Prompt、RAG、Agent 工具、微调、多模态或 AI 评测：读取 `docs/agent/ai/rules.md`。

各领域入口只负责边界、不变量和专项路由；只加载当前任务命中的文件，不因“可能有用”读取整个 `docs/agent`。

## 状态与范围（Stay in scope）

- `feature_list.json` 是功能范围、依赖、状态、验收条件和验证证据的唯一事实来源。
- `ROADMAP.md` 只表达面向公众的 Now、Next、Later 与已交付方向；不得重复维护精确状态、依赖、验收条件或验证证据。获准执行的 Roadmap 项必须先在 `feature_list.json` 中取得 Feature ID。
- 每个独立 workspace 在自身根目录维护 `progress.md`；新增 Python 项目时同步扩展 Harness 的 workspace 发现逻辑，只更新受当前任务影响的 workspace。
- `session-handoff.md` 只记录跨会话仍未完成的工作、阻塞、关键文件和下一步。
- One feature at a time：同一会话可在同一 worktree 和特性分支中依次处理多个功能，但任一时刻只允许一个 `in-progress` 功能；并行会话须在各自 worktree 中维护状态，合并时协调 `feature_list.json`，不顺手处理无关问题。
- 新增 workspace 时同步创建 `progress.md`；`pnpm harness:check` 必须能够发现它。

## 工程不变量

- 审查（CR）、验证、测试排障、文档整理及提交／推送准备仅处理各自任务范围，不得顺带修改产品功能、业务规则、交易判定、计算结果或其执行时机；不得通过放宽测试断言掩盖差异。发现功能或逻辑问题时先记录证据与影响，只有当前任务明确要求修复该问题时才修改相关实现，并单独说明行为变化及验证结果。
- TypeScript 保持严格类型。外部输入先以 `unknown` 接收并校验，禁止用 `any`、`@ts-ignore` 或无范围的 `eslint-disable` 掩盖问题。
- 跨 Web、Mobile、API 的数据结构定义在 `@repo/contracts`，不重复声明 DTO。
- 环境变量集中在 `@repo/env` 声明和校验，并同步 `.env.example`；秘密不得进入客户端 bundle 或仓库。
- 通用 UI 原语优先复用设计系统；含接口、权限、路由或业务流程的组合组件留在应用内部。
- Web 保持 Server/Client Component 边界；API 保持 `presentation → application → domain ← infrastructure` 依赖方向。
- 新增依赖前确认现有能力不能满足需求，使用 `workspace:*` 引用内部包并同步锁文件。
- Python 项目以 `pyproject.toml` 作为配置和打包事实来源，虚拟环境保持隔离；Codex 按 Python 规则直接写出符合规范的代码，不运行 Python 格式化或 lint 工具；类型检查和 pytest 按任务风险选择。
- 缺陷修复补充能够复现问题的测试；无法自动化时记录手工验证和剩余风险。

## 多级趋势统一计算（强制）

- 一级、二级、三级趋势必须调用同一个核心趋势函数，通过 `level` 参数区分级别；允许拆分共用辅助函数，不得为三个级别分别维护算法、公式或修线补丁。
- 输入来源固定为：一级读取技术折线高低点，二级读取一级趋势结果，三级读取二级趋势结果。`level` 只用于确定来源层级、结果归属、缓存身份及展示名称，不得成为使用不同趋势判定规则的理由。
- 趋势识别、普通及特殊翻转、六态、连续性和发展尾部处理使用各级共用的规则实现。发现某一级的问题，必须追溯并修复共用逻辑，同时考虑其对其他级别的影响；禁止按股票、日期或 `level == 2/3` 增加专用业务判定。合法的输入校验、层级调度和展示样式分支不受此限制。
- 同一来源版本、规则版本和计算参数下，每一级只计算一次并复用缓存结果。一级 → 二级 → 三级是三次有依赖的计算；禁止图表预览、完整结果或显示层再运行另一套趋势判定，删除、重组或重新锚定已经计算出的趋势线。
- 图表、历史回放和策略消费者使用同一份对应时点的趋势结果。显示层只负责样式、标记和按回测进度显示，不得用显示补丁改变结构或向下一级提供不同的来源。
- 趋势结构、六态确认和交易资格分别保留明确的状态含义；缺少确认记录或交易资格本身不能代替结构判定。统一入口不得擅自改变已确认的普通／特殊规则；回归用例覆盖一、二、三级，执行时机遵循本文件的开发验证节奏。

## 安全与变更边界

- 不读取、输出或提交真实密钥；本地 `.env.*` 从 `.env.example` 生成并保持忽略。
- 不使用 `git reset --hard`、`git checkout --` 或批量覆盖清理未知改动。
- 删除数据、回滚迁移、生产部署、发布包等高风险操作必须先获得用户明确授权。
- 特性分支提交与本地合并按本文件工作流执行；未经用户明确要求不得推送、创建 PR、发布或部署。

## 验证命令与完成定义（Verification Commands / Definition of Done）

开发阶段只开发代码，不执行任何检查或验证命令，包括 Prettier、lint、类型检查、测试、包构建、Git 状态／差异／暂存区检查和运行结果探针；不通过替代命令绕过此要求。实际提交阶段才按变更风险执行一次必要检查，不能边开发边验证或反复验证。本地提交不运行 Git 钩子检查。`pnpm harness:check` 保持禁用为显式跳过，也不直接调用 `scripts/harness/validate-state.mjs`；`verify:quick` 仅供实际提交阶段按需使用。开发所需的代码搜索、阅读和编辑，以及功能状态、进度和交接记录的维护继续进行。现有 GitHub Actions 配置保持不变，原 Harness 步骤只输出禁用提示；浏览器端到端测试仅在用户当次明确要求时执行。未运行的检查记录为“待验证”，不得写成已通过；提交阶段已运行的检查失败时先修复。

按风险选择最小充分验证：

```bash
pnpm --filter <workspace> lint
pnpm --filter <workspace> typecheck
pnpm --filter <workspace> test:unit
pnpm --filter <workspace> build
pnpm verify:quick
pnpm verify
```

Python workspace 使用项目已选定的环境/依赖执行器运行等价门禁；默认命令形态为：

```bash
mypy src tests
pytest
python -m build
```

具体路径和是否需要构建由 `pyproject.toml` 与任务类型决定，不能为了套用示例运行不存在的目标。Python 格式、导入和命名遵循 `docs/agent/python/rules.md` 与现有代码风格，由 Codex 在编辑时处理。

目标行为实现且相关格式/lint/类型/构建实际通过后，按风险记录已运行的定向测试和待验证项；状态与进度文件更新、无法运行项及风险明确记录后才能标记为 `done`。UI、API、数据库和基础设施改动所需的浏览器、设备或集成验证遵循当次用户要求与实际验证结果，不得把未运行项写成通过。

## 会话结束（End of Session）

1. 更新 `feature_list.json` 的状态和命令级证据。
2. 更新所有受影响 workspace 的 `progress.md`。
3. 未完成或跨 workspace 的长任务更新 `session-handoff.md`。
4. 复查 `git diff`、`git status` 和实际执行的验证，记录待验证项；提交前确认没有凭据、产物或无关文件。

按以上记录即可保持 clean、restartable 的交接路径。
