# WaveQuant Web

研究工作台首次进入默认使用可用的 AkShare 行情。股票列表和自选股切股保持 AkShare 数据口径；只有点击“运行当前股票回测”才明确切换到通达信因果复权回测。手动选择通达信口径时，原有选股自动回测行为仍可用。

WaveQuant 的浏览器研究工作台。默认 `/` 进入从 `E:\WorkSpace\股票\web` 完整迁移的主控研究页面，保留 K 线复盘、历史回放、股票搜索、买点扫描、幅度对比、当前股票回测、绩效、订单信号和系统状态等原有功能。

`/` 与 `/research` 都由 Next.js App Router 直接渲染 React 研究工作台，不再跳转静态 HTML。页面结构拆分在 `src/features/research-workbench/components`，浏览器图表和原有研究算法通过 `runtime` 客户端边界挂载，同源 `/api/*` 由开发服务器代理到 WaveQuant API。

研究页左侧“策略拓扑”可通过 `/research?page=topology` 直接打开。tldraw 画布把结构候选、买点与公共门禁、A/B→C 波续攻、模拟执行与成交、退出与减仓拆成可逐关阅读的判断路径；每一关均列出“是 / 否”结果和源码位置。图以 `src/features/research-workbench/topology/topology-data.ts` 为版本化来源，画布只读以避免临时拖动被误认为策略规则。修改 `wavequant-core` 的策略/市场结构、回测执行或浏览器买点证据后，必须复核并更新该文件的条件与路径，再更新其中的 `strategySourceDigest`；`topology-data.test.ts` 会在源码指纹变化时失败，提醒同步维护。该指纹是变更门禁，不会自动推断策略语义，人工复核仍是必要步骤。

tldraw 本地开发无需密钥；在 HTTPS 非本地域名上线前须按 [官方许可说明](https://tldraw.dev/sdk-features/license-key) 提供有效授权密钥（SDK 支持 `NEXT_PUBLIC_TLDRAW_LICENSE_KEY`）。

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

该命令同时启动 Next.js（`http://localhost:3003`）和只读 WaveQuant API（默认 `8765`），并代理 `/api/*`。存在 API workspace 的 `.env.infrastructure` 时，开发启动器会加载其中未被终端显式覆盖的 SQL/Redis 配置；对于指向 loopback 的 MySQL/Redis URL，还会自动执行 Compose `up --wait`、幂等建表，并托管一个通达信信号 Worker 与八个 AkShare 结构分片 Worker。Worker 异常退出会在 5 秒后自动重启，所以重启页面或开发服务不再丢失结构读模型。可设置 `WAVEQUANT_DEV_AUTO_INFRA=false` 禁止管理 Compose，设置 `WAVEQUANT_DEV_STRUCTURE_WORKERS=1..16` 调整 AkShare 分片数，或设为 `false` 只启动 API。

启动器会优先读取 `WAVEQUANT_RESULTS_ROOT` 与 `WAVEQUANT_TDX_ROOT`；当前迁移机器未设置变量时会回退到 `E:\WorkSpace\股票\results\operations_v1` 和 `D:\TDX`。研究页可从“数据 / 结果口径”选择 AkShare 或通达信，并通过图表标题区的单行 Tab 切换日、周、月、季、年 K 线；键盘方向键、Home、End 同样可切换。K 线与结构画线从服务器同一个版本化预计算 bundle 读取，最后一个未完成周期会明确提示。浏览器 IndexedDB 的 `market-timeframes` 表按来源、股票、请求日期和周期缓存最多 40 份 bundle，用 ETag 只同步变化的数据；短暂离线时可安全复用完整旧 bundle。封存样本与策略回测仍锁定日线，避免聚合行情改变成交语义。“符合买点”和“结构信号”继续只查询服务器预先发布到 SQL/Redis 的结果。

研究图表提供“本地研究图 / TradingView 画图”切换。前者保留本地行情、结构线、回测 B/S 与回放；后者按需嵌入 TradingView 官方 Advanced Real-Time Chart，开启完整绘图侧栏，并将 `sh.600519` / `sz.002396` 映射到 `SSE:600519` / `SZSE:002396`。嵌入图只使用 TradingView 的独立行情，不能读取 AkShare/通达信数据、因果复权或本地策略叠加；沪深市场在其 Widget 中为日终数据。切回本地图不会销毁嵌入图；切换本地股票后需点击“同步当前股票”，重载外部图表可能丢失未保存的画线。该功能需要浏览器能访问 `s3.tradingview.com`，失败时页面会提供重试和官网入口。若以后需让 TradingView 图直接消费本地数据，应取得官方 Advanced Charts 私有库授权并另接 Datafeed API，不应把公开 Widget 当作本地数据适配器。

本地研究图的“图层 → 三级空翻多三分线”默认开启，关闭后刷新仍保持选择。它取图窗右端之前最近一段已确认三级空翻多的服务端低点 L、高点 H，按 `H − (H − L) / 3` 和 `H − 2 × (H − L) / 3` 绘制两条橙色回撤线，从低点日延伸至当前数据日；切换窗口不会以局部最高、最低价重算整段。需要同时开启“三级趋势线”和“折线 / N 字”。历史回放只使用截至回放日已确认的锚点。三分线用于判断回档深度，是否形成空多交替仍以地标确认结果为准。

瑞凌股份 `sz.300154` 的当前股票回测中，2024-02-06 至 2025-03-20 的因果复权锚点约为 4.8363 → 16.7980，两条回撤线约为 12.81、8.82；未复权行情图按其自身价格口径计算，不混用复权锚点。

“图层 → 三级 a/b/c 观察”显示三级 c 段启动候选、后续收盘突破 a 高点及候选失效。a 是已确认空翻多的低高整段，b 是其后回调。价格路径要求 b 最低价不低于 a 的 2/3 回撤价；或者，b 的交易 K 线耗时严格长于 a，且 b 最低收盘价严格低于 a 的 1/2 回撤价。后者以用户最终更正的 `<` 为准，等于半幅不通过。b 收盘区间从 a 高点之后至 b 低点，耗时不包含后面等待正 N 的时间。满足任一路径且保持较高 b 低点，再由新正 N 的轧空/强轧空确认形成候选；图标按当日已知证据显示，点击可核对完整价位与时间。候选本身不等同于 LONG 或成交。

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

所有 V3 幅度方案现共用空多交替结构识别。合格 b 经正 N + 轧空/强轧空确认后，各级交替低点图层在 b 日期标识、注明实际确认日；尚未收盘突破 a 高点就跌破 b 时显示“已失效”，历史回放不会提前显示确认或失效。原突破确认路径和各方案的幅度、量能、风控门槛保留。

回测工具栏增加“启用次开盘含费净盈亏比过滤”，默认不勾选。勾选后才因次开盘含费净盈亏比不足拒单；关闭后仍扣除费用，其他执行约束保持生效。当前股票回测、五组幅度比较和导出账本均记录相同选择；切换开关重新回测并清除旧比较结果。

## 涨停天梯跳转复盘

天梯股票卡片和明细中的代码、名称可打开 `/research?page=workspace&symbol=sz.001216&asof=2026-09-18&source=akshare`。入口携带股票和天梯日期，以日线进入行情与复盘，优先于默认自选股；刷新链接仍保持定位。

优先读取指定数据源。其目录缺少目标股票时，仅允许回退到另一数据源中的同一股票，并明确提示；两者都无数据时展示错误，不切到默认股票。实际行情早于请求日期时展示实际截止日。页面不会因点击天梯而自动执行回测，可使用既有回测按钮继续分析。
