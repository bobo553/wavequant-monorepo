# Session Handoff

## 2026-10-04 · MONOREPO-256 组合A回调买入已获合并授权

会话 worktree `C:/Users/zb/.codex/worktrees/combined-a-entry/wavequant-monorepo`，分支 `feat-组合A回调买入`，基线 main `c99b825`。V3 v89 的新通道已实现，回调时间按 C 顶到放量突破日计算，包含低点之后的长期窄幅横盘。国芳 2025-04-03 的 58 日虽然不超过内部 B 的 70 日，但超过最末同源子 B 的 9 日；最低收盘 6.215769 守 2/3 位 5.918297，放量中大阳线收盘 7.158380 突破此前已知高点 7.049092。新规则 24 项、十满 17 项、Web 65+3 项、定向类型检查及 Core/Web 构建通过。完整账户和外部分钟回测未运行，独立单笔成交不替代账户回放。

主行情缓存 2123 根只读截到 2018-01-02 至 2025-04-03 的 1759 根，明确导入新 worktree 源码，不借旧 signals/audit，78.744 秒生成当日唯一组合通道 LONG；58/70/9、量价、守线与此前可知高点证据全部通过。未运行账户成交，普通 N 原拒因不变。

较广回归仍有 4 项主分支既有失败，已在 clean main 明确导入 main/src 复现。`test_wave_entry_epoch.py` 两项 January6 的旧断言要求 local epoch recovered，但实际 N 和入场同属 epoch138；实际买入日期、价格和风控断言通过。`test_nested_alternation_breakout.py` 两项华瓷 January17 缺预期一级上下文而无信号。启停组合通道证据不变，未修改旧断言。此前按 AGENTS“检查未通过……无法通过……不合并”保留 feature in-progress；2026-10-04 用户已明确回答“允许合并并重启”，允许保留这四项既有失败覆盖本次门禁，不推送。保留failed事实，Harness不能将带failed证据功能标done；旧 MONOREPO-231 所有失败证据保留，暂设backlog以保持单一活动功能。

追加定位与验证：实际页面是13003，Next/API8765同属main `.codex-runtime/wavequant-dev.pid=24196` 进程树，HTTP源码修订c99b825/profile v88、新原因helper404。按正式界面vol=false/netRR=false等参数完整2018起1759根账户前缀重放81.87秒确认04-03原始5.24元500股filled BUY、费用后RR1.62725689及完整台账原因。缺分钟使用现有日线收盘回退，不改权限、不伪造分钟。现有restart-wavequant.ps1等待硬编码3003与实际13003不符；合并后验证所有者和零活跃任务，仅停止该登记的dev进程树，再用main cwd、WAVEQUANT_WEB_PORT=13003/WAVEQUANT_API_PORT=8765隐藏启动pnpm --filter wavequant-web dev，登记新pid并HTTP确认新版本。Web扫描入口已补独立分类及AkShare/TDX理由消费，新增6项回归、18项Node、3项拓扑及最终lint/typecheck/build/格式/语法通过，独立只读复核无问题；Backend扫描JSON保留完整proof。

本段记录时尚未合并重启，用户授权已到；完成扫描显示检查后同步最新本地main、复核差异并按no-ff本地合并，沿实际13003入口重启并HTTP确认，不重复请求授权，不推送。不要自动扩大旧信号修复范围。策略拓扑指纹 `5655992d616d91db95743fd746aff3cf0c99ababfe8a5354a8f13503f92261ab`，任何Core源码修改后须同步拓扑门禁；最终合并与服务证据将追加到本节。

## 2026-10-03 · MONOREPO-233 清空依据待澄清

会话 worktree `E:/WorkSpace/work/wavequant-xianfeng-c-target`、分支 `feat-先锋新材C浪目标` 已同步 main `d7ee177`。先锋2020-06-24禁买已合并并常驻生效：v81/20541a57aa76，同参数正式AkShare回测状态complete、该日BUY/LONG标记0，目标5.42、箱高0.29、余量0.22、06-23已知。Web禁买文案已加载。首次启动MySQL空握手经重启既有本地MySQL容器恢复。未推送。

相关Core112项、Web33项及类型/构建通过。旧华瓷断言已区分正式08-10 N与手工08-03转浪观察，两层仍分别验证原防守/目标、前缀与阶段去重。MONOREPO-233保留backlog，剩余清空需求待澄清；未覆盖其他会话MONOREPO-231的唯一in-progress状态。完整Core/Web套件与真实浏览器未执行。

用户说“06-05形成正N，06-24近五顶不要买入，06-05应符合清空条件”。清空是否指06-05持仓退出、06-24或以后，以及触发依据尚未回答；最新澄清是“2020-06-24仍显示买入”，本轮完成该日禁买生效。当前正式引擎N完成06-02、轧空确认06-05；给定06-05是放量阳线，不能擅自添加清仓。后续按用户澄清处理清空日期/条件；之前的推送授权仅已完成ABC任务，未自动沿用于本次修改。

## 2026-10-03 · MONOREPO-231 已合并

用户在了解两项 main 既有波段信号失败后明确要求合并。实现提交 `912ac09`、修正提交 `5a9655d` 已以 `40522c1` 合并到本地 main。119 项修复定向测试及类型/构建证据保留；`test_huaci_volume_double_break_closes_cycle_before_next_gap_buy` 和 `test_strong_a_body_confirmation_can_advance_to_later_higher_gap` 的 main 既有失败未修复，按本轮明确指示覆盖该门禁，未降低断言。

每轮零持仓首次买入至数量归零独立计算成本 MAE；已清仓轮次中亏损最深者为最终最大回撤，未清仓只展示本轮亏损。HTTP 已确认 Web main 修订 `40522c1`、`holding_entry_cost_mae_cycle_v2` 模块和研究页持仓轮次表。API 引擎 `dbaa9a01700f` 与 main 本地源码一致，旧引擎完成摘要在启动检查时 recent=0；行情文件及封存样本仍截至 2026-09-07。没有运行浏览器、Playwright 或完整 Web 套件，没有推送。

本次合并已完成；Harness 不允许存在 failed 证据的功能标记 done，因此 MONOREPO-231 继续保持唯一 in-progress，保留待验证的既有波段失败，MONOREPO-183 原人工缓存验收继续 backlog。用户本轮的合并请求已经完成，不自动开展旧信号修复。该会话继续保留分支 `feat-华瓷放量突破` 和 worktree `E:/WorkSpace/work/wavequant-huaci-volume-breakout`；后续修改前同步最新本地 main。

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
