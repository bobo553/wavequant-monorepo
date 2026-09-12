# Progress

## Current State

- `MONOREPO-031` 已完成：Web 已停止自行生成一级业务点，改为渲染 Python 返回的统一正式一级趋势路径。
- `MONOREPO-011` 已完成：本 workspace 是 WaveQuant 独立浏览器应用边界。
- `MONOREPO-013` 已完成：默认入口是全景市场看盘页，原服务端研究工作台保留在 `/research`。
- `MONOREPO-014` 已完成：默认看盘页已迁移到 Next.js App Router、React 19、Tailwind CSS 4 与共享 shadcn 设计系统。
- `MONOREPO-015` 已完成：原型八个看盘视图的交互和原研究、回测、模拟交易、信号、设置模块入口均已保留。
- `MONOREPO-016` 已完成：原 `E:\WorkSpace\股票\web` 完整研究工作台恢复为默认入口，Next.js 全景页保留在 `/market`。
- `MONOREPO-017` 已完成：默认 `/` 与 `/research` 直接由 Next.js App Router 和 React 渲染完整研究工作台，单一开发命令同时提供页面与本机 API。
- `MONOREPO-018` 已完成：研究工作台与全景市场已统一到参考 shadcn dashboard 模板改造的响应式应用壳。
- `MONOREPO-019` 已完成：在保留 WaveQuant 青的基础上新增证券蓝主题，并与涨跌语义、明暗模式和信息密度解耦。
- `MONOREPO-022` 已完成：研究 K 线按一级、二级和三级结构标识当前图窗末跌高，可直接核验来源高点与对应低点。
- `MONOREPO-023` 已完成：一级趋势线的可见已确认高低点显示紧凑价格，高点在上、低点在下。
- `MONOREPO-024` 已完成：建立末跌高严格突破后的水平虚线延长能力；实际突破 K 线口径由 `MONOREPO-030` 完善。
- `MONOREPO-025` 已完成：一级趋势端点价格数字可以独立显示或隐藏，并持久保存本地偏好。
- `MONOREPO-026` 已完成：一级趋势线跨路径衔接同时校验 H/L 类型和价格方向，消除首创环保 5–8 月伪低低线。
- `MONOREPO-027` 已完成：一级显示桥真实来源极值显示价格，同时保持桥接点与正式一级点、末跌高定义的语义边界。
- `MONOREPO-028` 已完成：一级连续显示路径与图上末跌高标注使用同一口径，首创环保改为 6 月 2 日高点对应 6 月 30 日最低点。
- `MONOREPO-029` 已完成：相邻讲义路径在高低类型与价格方向均成立时补充纯显示虚线，首创环保 6 月 3–4 日缺口已闭合。
- `MONOREPO-030` 已完成：末跌高虚线按市场收盘首次严格穿越关键位延长，皖通高速二级虚线止于 2026 年 7 月 20 日。
- `MONOREPO-031` 已完成：跨讲义路径的真实确认极值由 Python 升级为正式一级点，Web 与二、三级趋势不再维护两套一级定义。

## Completed

- 联合开发命令为独立 API 配置显式 Next.js 根地址，使旧的 8765 浏览器入口跳转到唯一 3003 页面进程，不恢复第二套 Shell 或静态页面服务。

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
- 新增共享 `WaveQuantShell`，统一可折叠桌面侧栏、移动抽屉、sticky 顶栏、面包屑、环境状态和内容滚动边界。
- 侧栏折叠偏好使用可恢复的本地状态并支持 `Ctrl/Cmd+B`；移动抽屉支持遮罩关闭与 `Escape`，导航配置按市场和研究模式保留原有命名与入口。
- 研究工作台继续保留 `data-page`、`page-title`、`reload` 等运行时契约，动态移动导航通过事件委托接入原图表、回测、扫描和系统状态控制器。
- 原研究样式的全局元素选择器已收口到工作台作用域，避免从 `/research` 导航到 `/market` 后污染 shadcn 壳层、表格与页脚。
- `/research` 与 `/market` 现在使用完全相同的 market 导航分组、双层顶部栏、侧栏状态和底部状态栏；研究页原有页面切换、刷新、图表及 API 行为映射到统一导航命名。
- 唯一 `WaveQuantShell` 已提升到 App Router 根布局，市场与研究路由仅替换业务内容；研究样式随根布局预载，跨页不再卸载侧栏与头部或等待样式块。
- 研究页头部补齐与市场页共用的股票搜索、通知、界面设置和研究员入口，同时保留原 `reload` 刷新契约及完整研究运行时逻辑。
- 参考 dashboard 模板的 `data-theme` 与 CSS token 机制新增“证券蓝（推荐）”；蓝色仅用于操作、选中、焦点和中性趋势线，红涨绿跌及无障碍蓝橙方案保持独立。
- 证券蓝覆盖深色、浅色、shadcn 组件、共享侧栏和 ECharts 趋势线；选择结果复用 `wavequant.market.v2` 本地空间，并在 React 水合前恢复，避免刷新时主题闪烁。
- 研究兼容样式不再固定深色背景和青色强调色，现由全局背景、卡片、边框、文字与主色 Token 驱动；Lightweight Charts 监听根主题属性并即时更新画布、网格、坐标文字和主趋势色。
- 新增“各级末跌高”独立图层：标签落在图窗最低已确认 L 左侧最近的同级已确认 H；未突破时水平虚线止于该 L，突破后延长到低点可知后首根收盘严格由关键位下方穿越到上方的 K 线；没有已确认结构的级别不补画，点击标签可查看来源、确认日、突破点和完整定义。
- 一级趋势线端点新增 9px 紧凑价格标签，整数不补零、非整数最多两位；原折线以及二、三级趋势线保持无价格标签。
- 图表图层设置新增默认开启的“一级点位价格”：关闭只清空价格数字，不影响一级趋势线、端点和末跌高；重新开启立即恢复，并在刷新后保持用户选择。
- 一级跨路径显示衔接现在要求 `L→H` 必须上涨、`H→L` 必须下跌；若异类端点直接方向不成立，则用期间真实已确认来源极值补成四点交替桥，无证据时保持断开。该显示桥不进入趋势确认、末跌高、策略或回测。
- “一级点位价格”同时覆盖正式一级端点和跨路径显示桥采用的真实来源极值，共用端点按原始索引、棒内顺序、类型和价格去重；价格显示不会把桥接点升级成正式一级点。
- 一级视窗摘要会把正式分段与有真实来源的显示桥拼成只读连续路径，再从图窗最低显示低点向左取最近交替高点作为“图上末跌高”；该摘要和水平虚线不写回服务端趋势、二三级、策略或回测。
- 讲义折线跨来源路径只在端点来自相邻 K 线且满足 `L→H` 上涨或 `H→L` 下跌时补画黄色虚线；同类端点、反向价格和非相邻缺口仍保持断开，补线不参与任何趋势或策略计算。
- 末跌高突破虚线直接核对市场 K 线收盘穿越，不再等待该棒日后形成新的同级趋势高点；盘中上影越线、收盘相等和低点尚未知时不提前延长，显示结果不参与趋势确认、策略或回测。

## Verification

- 8765 旧入口真实 HTTP 回归通过：临时跳转到唯一 3003 Next.js 页面后返回 `200 text/html`；Web ESLint、TypeScript、6 项 Vitest 与 41 项 Node 契约测试通过。

- Web 语法、Prettier、40 项单元测试与自包含静态构建通过。
- API 跨 workspace 测试验证默认页面、Lightweight Charts 和第三方声明可被正确提供。
- Chromium 在 1920 桌面视口验证市场总览和涨停阶梯，在 390 窄屏验证无整页横向溢出；无页面异常和外部网络请求。
- 根 `pnpm harness:check` 与 `pnpm verify` 通过。
- MONOREPO-014 Web 门禁通过 ESLint、严格类型检查、1 项 React 组件测试、40 项兼容单元测试、Playwright E2E 和 Next.js 静态导出。
- MONOREPO-015 Web 门禁通过 ESLint、严格类型检查、2 项 React 组件测试、41 项兼容/结构测试和 Next.js 静态导出。
- MONOREPO-015 Playwright 3 条流程覆盖 Next.js 八视图关键交互、搜索/设置/导出/复盘、390 窄屏，以及经典 v2 六大模块和八个市场页签；无页面异常。
- MONOREPO-018 复验通过 ESLint、严格类型检查、4 项 React 组件测试、41 项兼容契约、5 项 Playwright 流程与 Next.js 生产构建；真实浏览器确认 `/market` 和 `/research` 的应用骨架一致。
- 唯一 Shell 回归通过 DOM 稳定性探针：从 `/market` 切到 `/research?page=workspace` 后原 Shell 节点保持连接，搜索、设置、刷新和研究数据加载均正常。
- 原始 v2 文件与 `/wavequant-v2-classic.html` 规范化文本逐字相同，均为 219695 个字符。
- MONOREPO-015 完成后的根 `pnpm verify` 通过 15 个 Feature、17 个 Node workspace、2 个 Python workspace 和 32 份规则的全仓门禁。
- MONOREPO-016 Web 门禁通过 ESLint、严格类型检查、2 项 Vitest、41 项 Node 契约、4 项 Playwright 页面回归和 Next.js 静态导出。
- 使用源封存结果与 `D:\TDX` 的浏览器验收通过 23 项完整工作台流程、4 项幅度比较、6 项慢扫描传输和 3 项二级趋势连续性检查。
- MONOREPO-017 Web 门禁通过 ESLint、严格类型检查、2 项 Vitest、41 项 Node 契约、4 项 Playwright 页面回归和 Next.js 静态导出。
- MONOREPO-017 真实 Next 入口通过 23 项完整工作台、4 项幅度比较、6 项买点慢扫描和 3 项二级趋势连续性检查。
- MONOREPO-017 完成后的根 `pnpm verify` 已通过全部 workspace 的 Harness、lint、类型检查、单元测试和生产构建。
- MONOREPO-018 Web 门禁通过 ESLint、严格类型检查、4 项 React 组件测试、41 项兼容契约和 Next.js 静态生产构建。
- MONOREPO-018 Playwright 5 条流程覆盖桌面折叠持久化、390px 移动抽屉、完整研究工作台、市场八视图与经典 v2；1440px 实际浏览器检查无 Console 错误或警告。
- MONOREPO-018 完成后的根 `pnpm verify` 通过 18 个 Feature、17 个 Node workspace、2 个 Python workspace、32 份规则和 15 个生产构建任务。
- MONOREPO-019 Web 门禁通过 ESLint、严格类型检查、6 项 Vitest、41 项 Node 契约、5 项 Playwright 流程和 Next.js 静态生产构建。
- 证券蓝浏览器验收通过深色 `#4ea1ff`、浅色 `#1d64d8` 的即时切换与刷新持久化；主色前景对比度分别为 7.10:1 和 5.43:1，满足 WCAG 2.2 AA 普通文本要求。
- 研究页主题同步回归通过：浅色背景为 `rgb(243, 246, 250)`、卡片为白色、证券蓝强调色为 `rgb(29, 100, 216)`，图表画布同步为白色；5 项 Playwright 保持全部通过。
- MONOREPO-022 Web 门禁通过 ESLint、严格类型检查、42 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建。
- 茅台本地行情浏览器核验显示一级 `H12 1295 → L13`、二级 `H2 1568 → L2`；三级当前图窗无已确认结构不补画，图层开关、Console 和 Network 均正常。
- MONOREPO-023 Web 门禁通过 ESLint、严格类型检查、43 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；茅台图窗 33 个一级端点与 33 个价格标签一一对应。
- MONOREPO-024 Web 门禁通过 ESLint、严格类型检查、44 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；茅台一级 `H12 1295` 的虚线延长到首个突破点 `H14 2026-07-21 1344.70`，二级 `H2 1568` 未突破仍止于 `L2`，Console 与 Network 均无错误。
- MONOREPO-025 Web 门禁通过 ESLint、严格类型检查、44 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；茅台浏览器样本关闭后价格标签 `33 → 0` 而一级端点仍为 `33`，刷新保持关闭，重新开启恢复 `33`，Console 与 Network 均无错误。
- MONOREPO-026 Web 门禁通过 ESLint、严格类型检查、45 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；首创环保原 `L7 3.04（2026-05-22）→H1 3.01（2026-08-04）` 伪方向直连已改为 `L 3.04→H 3.28→L 2.68→H 3.01`，所有显示衔接非法腿为 `0`，Console 与 Network 均无错误。
- MONOREPO-027 Web 门禁通过 ESLint、严格类型检查、46 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；首创环保正式一级点 `17` 个、去重价格标签 `19` 个，`6月2日 3.28` 与 `6月30日 2.68` 均已显示，桥点未写回服务端一级数据。
- MONOREPO-028 Web 门禁通过 ESLint、严格类型检查、47 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；首创环保当前一级显示路径最低点为 `6月30日 2.68`，图上末跌高已改为 `6月2日 3.28`，8 月 4 日 3.01 不再承担该标识，Console 与 Network 均无错误。
- MONOREPO-029 Web 门禁通过 ESLint、严格类型检查、49 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；首创环保 `6月3日 L 3.21 → 6月4日 H 3.29` 讲义虚线已连续，理论响应保持不变，Console 与失败请求均为 0。
- MONOREPO-030 Web 门禁通过 ESLint、严格类型检查、50 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；皖通高速二级末跌高 `2025-06-20 H3 18.58` 的虚线已延长到 `2026-07-20` 首根收盘突破 K 线（收盘 `18.78`、最高 `18.85`），理论响应保持不变，Console 与失败请求均为 0。
- MONOREPO-031 Web 门禁通过 ESLint、严格类型检查、50 项 Node 契约、6 项 Vitest、5 条 Playwright 流程和 Next.js 生产构建；华夏银行页面渲染 567 个服务端正式一级点，前端独立一级连接对象为 `0`，二级趋势从 `2026-01-23 L 6.32` 起算。

## Risks and Next Steps

- 生产静态导出仍使用同源 `/api/*`，独立域名部署时需要由 API workspace 提供静态页面，或配置等价反向代理。
