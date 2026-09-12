# WaveQuant Web

WaveQuant 的浏览器研究工作台。默认 `/` 进入从 `E:\WorkSpace\股票\web` 完整迁移的主控研究页面，保留 K 线复盘、历史回放、股票搜索、买点扫描、幅度对比、当前股票回测、绩效、订单信号和系统状态等原有功能。

`/` 与 `/research` 都由 Next.js App Router 直接渲染 React 研究工作台，不再跳转静态 HTML。页面结构拆分在 `src/features/research-workbench/components`，浏览器图表和原有研究算法通过 `runtime` 客户端边界挂载，同源 `/api/*` 由开发服务器代理到 WaveQuant API。

全景市场看盘保留在 `/market`，使用 React 19、Next.js App Router、Tailwind CSS 4、ECharts 与共享 `@repo/design-system-web` shadcn 原语；业务组件位于 `src/features/market-dashboard`。其中证券与行情是明确标识的虚构合成样本，不与原研究工作台的本地通达信数据混用。

原始 WaveQuant v2 单文件原型按文本等价副本保留在 `/wavequant-v2-classic.html`，作为 162 项既有交互的兼容验收入口；Next.js 页面逐项建立等价回归后再移除该兜底。

## 命令

```powershell
pnpm --filter wavequant-web lint
pnpm --filter wavequant-web typecheck
pnpm --filter wavequant-web test:unit
pnpm --filter wavequant-web test:e2e
pnpm --filter wavequant-web build
```

启动完整本地工作台：

```powershell
pnpm --filter wavequant-web dev
```

该命令同时启动 Next.js（`http://localhost:3003`）和只读 WaveQuant API（默认 `8765`），并代理 `/api/*`。它会优先读取 `WAVEQUANT_RESULTS_ROOT` 与 `WAVEQUANT_TDX_ROOT`；当前迁移机器未设置变量时会回退到 `E:\WorkSpace\股票\results\operations_v1` 和 `D:\TDX`。

开发态直接打开 `http://127.0.0.1:8765/` 时，API 会临时重定向到 Next.js 的 `http://127.0.0.1:3003/`；这既保留了旧入口，也避免由两个进程分别提供两份页面。

只启动前端用于独立 UI 调试时，可执行：

```powershell
pnpm --filter wavequant-web dev:web
```

生产静态导出完成后，也可由 API workspace 提供页面和接口：

```powershell
pnpm --filter wavequant-api dashboard
```

Next.js 静态导出位于 `out/`，不提交仓库。构建前会从已锁定的 `lightweight-charts` 依赖准备旧研究页所需的本地 vendor 文件；生成目录同样不提交。
