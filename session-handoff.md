# Session Handoff

## 2026-10-02 · MONOREPO-231 持仓归零与最终最大回撤

用户最终明确：每轮首次买入至完全卖出独立计算最大亏损，每次持仓数量归零结束并重置本轮；已完全卖出的各轮中亏损最深者才是最终最大回撤。未清仓本轮亏损单独展示，不计入最终统计。版本 `holding_entry_cost_mae_cycle_v2`；Core 增加逐轮序号、数量归零身份和因果观测曲线，Web 增加归零曲线及逐轮明细，API 同时保存最终与本轮指标。

工作区 `E:/WorkSpace/work/wavequant-huaci-volume-breakout`，同会话分支 `feat-华瓷放量突破`，main 基线 `df1025b`。38 项 Core、72 项 Web Node、2 项拓扑、7 项 API（合计 119）以及相关类型检查、Web/Core/API 构建和 Core wheel 隔离安装通过；Web lint 有 6 条既有警告。未运行完整 Web、浏览器或 Playwright，未重算真实账户全历史。

两项旧波段测试用 main 原源码再次复现失败：`test_huaci_volume_double_break_closes_cycle_before_next_gap_buy` 没有 2026-08-26 预期买入；`test_strong_a_body_confirmation_can_advance_to_later_higher_gap` 没有预期 body/gap 信号。已确认导入 main 的 backtest.py；本轮没有修改策略或降低这两项断言。遵守用户 AGENTS.md 检查失败不合并，修复保留在当前特性分支，常驻 main 页面未更新，未推送。后续先处理原波段资格回归，再同步 main、检查、复核和本地合并；推送需要明确授权。MONOREPO-231 保持唯一 in-progress，MONOREPO-183 的人工验收继续 backlog。

## 2026-09-29 · MONOREPO-208 双破确认

用户最终明确母子倒 N 清仓必须今低严格小于昨低且今收严格小于昨收，今高不参与判断。V3 全局识别器按双破确认，版本升至 v69，Web 原因文案同步。2018-01-01 起的完整通达信回测确认 2020-08-07 无 EXIT/SELL、2022-07-04 仍 EXIT 并以约 4.9478 元卖清；Core 118 项、Web 29 项及改动模块 strict mypy 通过。完整 Web 单测和浏览器端到端未运行。

## 2026-09-29 · MONOREPO-208

`feat-母子倒N清仓` 已完成国芳集团 2022-07-04 母子阳线破子低的 V3 清仓信号、同日收盘全仓执行、Web 原因文案及定向验证。真实同源日线 2022-01-01 至 07-08 的前缀与后缀信号一致；2018-01-01 起的完整通达信个股回测复现 06-30 买入、07-04 约 4.9478 元卖清、余仓为零。Core 原相邻 107 项及同步最新 `main` 后 101 项、Web 29 项定向测试通过；新模块严格 mypy、Core 构建、Web Lint／类型／构建和格式检查通过。Core 整仓严格 mypy 仍有 410 条既有错误；改动的两个大模块定向 44 条与本地 `main` 基线相同。用户 2026-09-29 明确要求合并当前分支到 `main` 并重启服务，本次按其指示覆盖既有错误门禁。不推送远端。

## 2026-09-26 · MONOREPO-183

量化 Core Python 源码变更后，开发服务重启会按引擎指纹清理各作用域的回测结果、信号和筛选缓存，并清空 API 旧引擎的历史完成摘要；行情及分钟数据缓存保留。用户澄清网页手动回测接口必须一直可用，因此已移除临时 API 拦截文件与 503 逻辑；AkShare/TDX 回测入口缺参均返回 400，未启动回测。Web 自动队列默认暂停，检测到引擎版本变化后也会暂停并同步偏好。人工只读检查 11 个缓存库的目标命名空间均为 0、API 活跃回测 0。用户要求仅人工测试，未运行 E2E、Playwright、Lint、构建或真实回测；人工验收后再决定是否恢复门禁并将 `MONOREPO-183` 标为 done。

- MONOREPO-108 已完成：原 N 点击五顶/十满线，用户公式见 strategy_lecture_v3.md；叠箱五顶 X+5H、堆箱五顶 B+3H，五顶后按实际段高减起涨点放大十满。最近入场目标仍独立保持旧逻辑。瑞凌 2026-07-27 在 2018 起点/2026-09-07 截面返回五顶 15.26 已满足、十满 22.32 回调暂停；真实浏览器与 Core/API/Web 门禁通过。保留并行任务 MONOREPO-107 的活动状态，未提交 Git。

- MONOREPO-100：用户要求回测和成交均改为当日 14:30–15:00，最低价跌破减 35%，最新价也跌破参照收盘减至原持仓 65%。staged_exit.py 新增 support_break_reduction / closing_reduction_intent，19 项新测试+7 项原测试通过。尚未接入执行链：D:/TDX/vipdoc/sz/minline 与 fzline 无 sz300154 分钟文件，现有只有日线，实际券商通道未实现。需分钟数据及执行接口，禁止拿最终日收盘伪造尾盘成交。更新：MONOREPO-104 已接通日线 35% / 65% 当日收盘撮合并验证瑞凌 05-15；真实尾盘分钟及券商通道仍待接入。保留其他任务的 MONOREPO-099 activeFeature。

## Active Work

`MONOREPO-088` 的本地代码和受控浏览器回归已完成，官方 TradingView Widget 的真实加载仍待网络恢复；功能状态以 `feature_list.json` 为准。`MONOREPO-087` 因用户改为要求独立 TradingView 绘图视图而退回 backlog，B/S 定位效果未实施。

## Blockers

当前机器访问 `https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js` 超时，浏览器报 `net::ERR_TIMED_OUT`，PowerShell HEAD 也超时。需网络可达后，在星网锐捷 `sz.002396` 上实际打开绘图侧栏、画线并复核切换；公开 Widget 无法读取本地行情，若要同图使用本地数据需官方 Advanced Charts 私有库授权。

## Key Files

- `AGENTS.md`
- `feature_list.json`
- `packages/wavequant-core/progress.md`
- `apps/servers/wavequant-api/progress.md`
- `apps/webs/wavequant-web/progress.md`
- `apps/webs/wavequant-web/public/tradingview-widget.js`
- `apps/webs/wavequant-web/src/features/research-workbench/components/tradingview-chart-view.tsx`
- 各 workspace 的 `progress.md`

## Recommended Next Step（Next Session）

先检查 `s3.tradingview.com` 是否可达；若已恢复，打开 `/research?page=workspace`，选择星网锐捷并进入 TradingView 画图，实际验证左侧绘图工具、切换保留和股票同步，再将 `MONOREPO-088` 标为 done。其余方向按 `feature_list.json` 和 `ROADMAP.md` 处理。\n

## 2026-09-24 · MONOREPO-165 交接

放量收跌默认累计 70% 于当日收盘模拟卖出；次一交易日低开收阴，或收阴且收盘跌破警示日低点，均于当日收盘清余仓。国芳集团 2020-05-13 减仓，05-14 开盘 5.30 元高于前收 5.27 元，但收盘 5.07 元跌破 05-13 低点 5.26 元，触发清仓；05-20 不再退出同笔持仓。桂林旅游 2022-09-22/23 原低开分支保持。Core 相关 94 项、Web 12+150 项单测通过，两条真实浏览器回归通过；并行桂林首次请求超时后单独重跑通过。Core/Web静态检查与构建通过。

功能保持 `in-progress`：Core 全量 971 通过、1 项 `tests/test_folded_n.py::test_guofang_july29_n_survives_internal_smaller_swings` 失败（入场证据 `alternation_index` 预期 643、实际 621，未改动的入场逻辑）。此前 3 个 Python 文件的 Ruff 格式检查失败仅作为历史记录；该工具已从项目门禁移除。Harness 要求 done 功能所有适用验证记录均通过，故当前保留测试失败证据与活动状态。后续需明确处理这项既有测试失败，再复跑相关验证并将 MONOREPO-165 标记 done；不要为通过门禁盲目更改已确认的退出规则。

完整 Web lint 当前复跑通过；本任务新增的国芳、桂林聚焦 E2E 与更新的旧桂林用例定向 ESLint 也通过，未触碰并行改动。旧桂林浏览器用例的其他年份断言完整保留，在 2025-11-06 买点处已有独立失败。
