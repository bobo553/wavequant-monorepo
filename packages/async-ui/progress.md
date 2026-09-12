# Progress

本文件只记录 `@repo/async-ui` workspace 的实现、验证、风险和下一步；跨项目功能通过根
`feature_list.json` 的功能 ID 关联。

## Current State

- 最后更新：2026-09-07
- 活动功能：无
- 状态：`MONOREPO-021` 可复用 Async UI 与弹框调度能力已完成

## Completed

- 新增独立 React 运行时入口，包含 `LoadController`、`defineAsyncView`、稳定 `PriorityQueue`、类型化 Popup
  Registry、`PopupManager`、不可变 `PopupStore` 和唯一 `PopupHost`。
- 动态 loader 在注册阶段不执行；并发加载复用 Promise，默认 8 秒超时和一次可恢复错误重试，失败保持终态直至
  显式 reset，`timeoutMs <= 0` 可禁用超时。
- `popup()` 按小数字高优先级与同级 FIFO 串行；`open()` 可立即叠加。排队、加载和可见阶段均可取消，业务
  Promise 统一返回 `undefined`，不把取消误记为加载失败。
- Registry key、业务 Props 与关闭结果由动态模块默认导出推导；`NoPopupProps` 保持严格空入参，运行时保留
  `close` 字段并在 Host 最后注入。
- Manager 统一提供失败模式、加载策略、生命周期 Hook 隔离、预加载策略、显式 reset、外部关闭/取消和 Host
  所有权；最终 Host 卸载延迟一个微任务清理，以兼容 React StrictMode 重挂。
- 新增独立 `@repo/async-ui/webpack` 入口，输出确定性 `async-ui-manifest.json`，支持 report/enforce 两种模式，
  检查 ID/chunk 重名、asset 缺失、initial graph、源码静态穿透和 gzip 预算。
- README 已记录完整接入、边界、取消方式、Webpack magic comment 和 Turbopack 限制。
- 核心源码已补充详细简体中文注释，覆盖公开 API 职责、状态机终态、并发 Promise 复用、取消哨兵、SSR、
  StrictMode、类型推导边界以及 Webpack Chunk/gzip 治理原因；避免重复描述可由代码直接看出的逐行操作。
- 前端专项规则新增“异步视图与弹框体验”路由和选型矩阵，明确低频重型视图、用户意图/服务端响应预加载、
  互斥队列与受控叠加等适用场景，以及首屏轻量反馈、可分享长流程、请求取消和渲染异常等非适用边界。
- 规则固定 Manager/Host 所有权、priority 不抢占、`undefined` 不等于成功、预加载业务时机、Webpack/Turbopack
  构建治理，以及时延、焦点、键盘、窄屏、失败重试和 Chunk 预算验证要求。

## 进行中

- 无；等待首个业务消费者按实际产品时机接入。

## Verification

- 2026-09-12：接入当前 monorepo 后，`pnpm harness:check` 通过 20 个功能、18 个 Node workspace、2 个 Python workspace 和 32 份规则检查；包级 lint、typecheck、19 项 unit 与双入口 build 全部通过。

- 2026-09-06：编码前标准 `init.ps1 -Mode quick -SkipInstall` 通过 31 个功能、18 个 workspace Harness、
  10 个共享包构建与 17/17 快速门禁任务。
- 2026-09-06：`pnpm --filter @repo/async-ui lint`、`typecheck`、`test:unit` 与 `build` 分项通过；当前 6 个
  测试文件、19 项测试覆盖 SSR 不加载、客户端切换、并发复用、超时/重试/终态、稳定队列、串行/叠加、关闭/
  取消、Host 卸载、类型推导和 Webpack 治理。
- 2026-09-06：完整 `pnpm verify` 通过全部治理门禁、19 个 workspace 的 lint/typecheck/unit 与 17/17
  构建；首轮完整门禁只捕获新改动中的一处 Prettier 格式错误，修正后原命令完整通过，没有关闭或弱化检查。
- 2026-09-06：功能状态收口后，标准 `init.ps1 -Mode quick -SkipInstall` 从 Node.js 24.14.1、pnpm
  10.26.2、Go 1.27.0、31 个功能/19 个 workspace Harness 和 11 个共享包重建到全仓快速门禁完整通过。
- 2026-09-06：补充核心源码中文注释后，重新执行包级 lint、typecheck、19 项 unit 和双入口 build 均通过；
  随后完整 `pnpm verify` 再次通过全部治理门禁、19 个 workspace 以及 17/17 构建。
- 2026-09-07：`pnpm exec prettier --check docs/agent/frontend/rules.md`、`pnpm harness:check` 与
  `git diff --check` 通过；通用 harness-creator 结构审计为 92/100，其唯一扣分是未识别仓库明确采用的分布式
  workspace `progress.md`，仓库自身 Harness 已确认 19 个 workspace 进度文件完整。
- 2026-09-07：使用仓库 `.cache/go-mod`、`.cache/go-build` 与 `GOPROXY=off` 执行标准
  `powershell -ExecutionPolicy Bypass -File ./init.ps1 -Mode quick -SkipInstall` 完整通过，共享包构建、全部治理门禁、
  19 个 workspace 的 lint/typecheck/unit 均成功。此前两次基线分别因未设置 `GOMODCACHE` 和默认 Go 代理超时而在
  许可证依赖枚举阶段中止，正确复用仓库缓存后原门禁通过，未修改或弱化检查。

## Risks and Next Steps

- 由首个业务消费者在生产构建中建立真实 chunk gzip 基线，再把 Webpack 模式从 `report` 升级为 `enforce`。
- 按具体产品时机接入 `APP_START`、`AFTER_SERVER_RESPONSE` 或 `USER_INTENT` 预加载；基础包不自行推断时机。

### 风险与阻塞

- 当前包提供基础能力但尚无业务应用消费者，因此没有伪造真实生产 chunk 名、浏览器交互或 gzip 预算证据。
- Webpack 治理入口不适用于 Turbopack；使用 Turbopack 的应用必须在单独的 Webpack 生产门禁或等价构建分析中验收。
