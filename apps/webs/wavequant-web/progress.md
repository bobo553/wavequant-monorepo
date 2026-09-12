# Progress

## Current State

- `MONOREPO-011` 已完成：本 workspace 是 WaveQuant 独立浏览器应用边界。
- `MONOREPO-013` 已完成：默认入口是全景市场看盘页，原服务端研究工作台保留在 `/research`。
- `MONOREPO-014` 已完成：默认看盘页已迁移到 Next.js App Router、React 19、Tailwind CSS 4 与共享 shadcn 设计系统。
- `MONOREPO-015` 已完成：原型八个看盘视图的交互和原研究、回测、模拟交易、信号、设置模块入口均已保留。
- `MONOREPO-016` 已完成：原 `E:\WorkSpace\股票\web` 完整研究工作台恢复为默认入口，Next.js 全景页保留在 `/market`。
- `MONOREPO-017` 已完成：默认 `/` 与 `/research` 直接由 Next.js App Router 和 React 渲染完整研究工作台，单一开发命令同时提供页面与本机 API。

## Completed

- 根据 WaveQuant v2 设计稿还原默认全景看盘页，并将内联原型拆分为符合现有 CSP 的 HTML、CSS 与 JavaScript 静态资源。
- 保留原有服务端研究工作台到 `/research`，并增加市场总览、涨停阶梯及响应式布局的自动验证。
- 静态页面、交互模块、第三方声明和浏览器测试已从工具 workspace 迁入本 workspace。
- Lightweight Charts 与 Playwright 依赖改由 `wavequant-web` 独立声明。
- 新增可复现静态构建脚本，输出自包含页面与 vendor 资源。
- 建立 `app`、`features/market-dashboard` 与 `shared` 分层；ECharts 图表按交互边界动态加载，旧研究资源迁入 `public` 兼容层。
- 使用 `@repo/design-system-web` 的 Button、Badge、Card、Input、Separator 与 Skeleton，应用内不复制 shadcn UI 原语。
- 新增 Vitest 组件回归和 Playwright 浏览器验收，覆盖桌面总览、涨停阶梯、ECharts 渲染及窄屏溢出。
- 八个 Next.js 看盘视图已补齐共享日期/时点/范围上下文、搜索、三态排序、个股钻取、观察组、异动暂停与已读、4/6/9 同屏、复盘笔记、导出和主题密度设置。
- 新增 `/wavequant-v2-classic.html` 兼容入口，按文本完整保留原 v2 六大模块、八个市场页签及其既有交互；原服务端研究工作台继续通过 `/research` 使用。
- 原研究工作台的页面结构拆为 `features/research-workbench/components` React 组件；经完整行为回归的图表、扫描和回测适配器限定在 `runtime` 客户端边界。
- 新增开发编排脚本：自动发现封存结果和通达信数据，同时启动 Next.js `3003` 与 API `8765`，并将 `/api/*` 保持为同源调用。
- 修复 Next 开发代理下扫描 POST 的显式回环来源校验，买点扫描、取消及上下文失效流程可通过 React 页面使用。

## Verification

- Web 语法、Prettier、40 项单元测试与自包含静态构建通过。
- API 跨 workspace 测试验证默认页面、Lightweight Charts 和第三方声明可被正确提供。
- Chromium 在 1920 桌面视口验证市场总览和涨停阶梯，在 390 窄屏验证无整页横向溢出；无页面异常和外部网络请求。
- 根 `pnpm harness:check` 与 `pnpm verify` 通过。
- MONOREPO-014 Web 门禁通过 ESLint、严格类型检查、1 项 React 组件测试、40 项兼容单元测试、Playwright E2E 和 Next.js 静态导出。
- MONOREPO-015 Web 门禁通过 ESLint、严格类型检查、2 项 React 组件测试、41 项兼容/结构测试和 Next.js 静态导出。
- MONOREPO-015 Playwright 3 条流程覆盖 Next.js 八视图关键交互、搜索/设置/导出/复盘、390 窄屏，以及经典 v2 六大模块和八个市场页签；无页面异常。
- 原始 v2 文件与 `/wavequant-v2-classic.html` 规范化文本逐字相同，均为 219695 个字符。
- MONOREPO-015 完成后的根 `pnpm verify` 通过 15 个 Feature、17 个 Node workspace、2 个 Python workspace 和 32 份规则的全仓门禁。
- MONOREPO-016 Web 门禁通过 ESLint、严格类型检查、2 项 Vitest、41 项 Node 契约、4 项 Playwright 页面回归和 Next.js 静态导出。
- 使用源封存结果与 `D:\TDX` 的浏览器验收通过 23 项完整工作台流程、4 项幅度比较、6 项慢扫描传输和 3 项二级趋势连续性检查。
- MONOREPO-017 Web 门禁通过 ESLint、严格类型检查、2 项 Vitest、41 项 Node 契约、4 项 Playwright 页面回归和 Next.js 静态导出。
- MONOREPO-017 真实 Next 入口通过 23 项完整工作台、4 项幅度比较、6 项买点慢扫描和 3 项二级趋势连续性检查。
- MONOREPO-017 完成后的根 `pnpm verify` 已通过全部 workspace 的 Harness、lint、类型检查、单元测试和生产构建。

## Risks and Next Steps

- 生产静态导出仍使用同源 `/api/*`，独立域名部署时需要由 API workspace 提供静态页面，或配置等价反向代理。
