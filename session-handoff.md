# Session Handoff

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