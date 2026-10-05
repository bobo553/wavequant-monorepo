## 2026-10-05 · MONOREPO-262 已完成

继续复用新华传媒 worktree / feat-新华传媒轧空买点，基线 main eda7e9c。新增共用已确认父 A 结构观察，保留二级整段起点、内部正式 N 独立测幅、同路径一级已确认 B 持续更低刷新及已确认 C 归档；普通 A 共用因果跟踪器，交易 Core 未改。真实 1699 日线和独立 1621 日线 09-03 正式前缀一致，完整 A 02-08→03-25、B 08-28→C 10-31 被发布，B 09-03、C 11-08 才可知，03-20 BUY/filled 保持。150 项定向回归及 Web lint/typecheck/build 通过；完整套件和浏览器/Playwright 待独立验证。262 done，恢复 256 为唯一 in-progress 并保留 4 项旧失败，本轮不推送。提交、合并及常驻验收稍后补入本段。

# Session Handoff

## 2026-10-05 · MONOREPO-261 正N测幅归属

实现 `6f0b14f` 已经 `9e5d74c` 本地合并，两检出干净。三个修改模块的 13003 HTTP 响应均 200 且逐字匹配 main，main Harness 通过。用户刷新可加载修复；未重启、未推送、未运行浏览器。

会话 worktree/分支继续复用新华传媒，基线 main `01702c7`。用户明确原低点 2024-02-29、突破 03-05 的特殊 N：底层已经按 A=4.299882554747297、X=4.794469984753443 测算 5.289057414759589/5.783644844765735；实际显示错是焦点套用 03-19 后续 N（5.4001/5.9149），原 03-05 攻击的 03-20 成交又套用了同日其他 N（5.4102/5.9048）。现焦点历史日期和原攻击抵抗突破的买点均绑定对应 N；成交来源明确时沿用其 n_date，裁剪视图优先全局 bar_index，没有改动特殊阴母同日 B/C 几何或交易测幅。

4 项真实混合来源回归先失败，最终来源/图表及相邻共 142 项通过；lint（6 条既有警告）、typecheck、build、相关格式/语法和 Harness 通过。复用实际 1508 根正式 HTTP 响应完整复验原 N 焦点和 LONG/BUY 来源，未重跑账户。完整 Web 套件和浏览器/Playwright 未运行，待独立验证。261 完成后恢复 256 为唯一 in-progress 并保留旧失败，按默认流程提交和本地合并，不自动推送；Git 和常驻验收以交付记录为准。

## 2026-10-05 · MONOREPO-260 正N范围焦点目标

实现 `2b007e3` 已以 `47092d7` 合并本地 main，两检出干净。研究页及三个相关模块 HTTP200，新焦点 helper 和图表、目标 overlay 响应逐字匹配 main；main Harness 通过。当前服务已经返回新代码，刷新页面即可加载；未重启、未运行浏览器或推送。

继续复用 `C:/Users/zb/.codex/worktrees/xinhua-squeeze-entry/wavequant-monorepo` / `feat-新华传媒轧空买点`，从本地 main `6ca5a0d` 同步。用户反馈前次正 N 目标出口修复后，范围内悬停和选中仍不显示。根因是焦点只识别 ABC、规则开关阻挡 N、ABC 覆盖 N，以及 C 点或价格图外时 overlay 过滤；现 N 范围焦点、ABC 并显、目标去重和图外边缘标签共用覆盖全部股票，没有增加交易条件。

最终 137 项 Web 定向测试、lint（6 条既有警告）、typecheck、生产 build、相关格式/语法及 Harness 通过。复用上一轮真实 1508 日线正式 HTTP 响应验收 03-05 的 5.2891/5.7836 两目标、历史可知性、原 C 点和 03-20 唯一 BUY/filled；没有重跑账户。已选 N 在更新理论后重新绑定，失效旧选择清理；临时悬停移出清理，点击或日期定位固定焦点，目标开关控制显示。完整 Web 套件和浏览器/Playwright 未运行，待独立验证，不把纯模块检查写成像素验收。

260 完成后恢复 256 为唯一 in-progress，保留其 4 项既有失败，不在本轮扩展处理。按默认流程提交并本地合并；本轮没有新的推送要求。最终 Git 和常驻模块验收结果以交付记录为准。

## 2026-10-05 · MONOREPO-259 正N目标标识

实现 `9a61ace` 已以 `d745b34` 合并本地 main。Web13003 三个修改模块响应逐字匹配；API8765 和代理版本接口均 HTTP200，v91 / `f9b1cb9e78e5a5b96a48c521c4f0e1c36803557d07ce9b50f0824c291cfd6838` 与 main 127 源指纹一致。实际 AkShare 2018-01-01..2024-03-20 正式 1508 日线接口返回 03-05 两目标完整元信息与精确价格，03-20 保留唯一 BUY/filled；通过 main Web 标记及目标模块核对中文名、C 点和形成日未突破。自动加载期间曾拦截旧引擎，计算期间短探测曾超时，完成后最终版本检查均通过；未手工重启或改缓存。验收原始 JSON 只存本机临时目录。用户刷新并重新回测旧结果即可，本轮未推送。

继续使用 `C:/Users/zb/.codex/worktrees/xinhua-squeeze-entry/wavequant-monorepo` / `feat-新华传媒轧空买点`，先同步此前已推送的本地 main `e86cc35`。根因是共用理论出口已经计算 1P/2T，但正 N 事件的 level 缺少 stage、anchor_at 和 available_at，图表按普通参考价处理，未使用买点已有的左端目标标识。现统一由 Core 共用出口发布元信息，Web 详情和图表消费中文显示名，保留旧原始名称和价格；不按股票日期特判，不新增交易条件。

新华传媒 2024-03-05 的一饱 5.289057414759589、二吐 5.783644844765735，C 点 03-04、可知 03-05；真实 1508 日线已审计结果与当前渲染器导出固定夹具，6 个策略源码哈希不变。42 项 Core、110 项 Web Node、3 项拓扑、渲染模块 mypy、Core 既定严格 typecheck、Core/Web 构建、Web lint/typecheck 和相关格式通过。渲染模块原 9 项类型问题已修正；两处 Web 旧桩补齐端点 overlay。完整套件、浏览器/Playwright 和外部分钟未运行，待独立验证。最终实现合并与常驻 HTTP 结果见本节首段。

259 完成后恢复 256 为唯一 in-progress，保留其 4 项既有失败，不扩展到无关规则修复。按默认流程提交并本地合并；本轮没有新推送请求。前次 258 的合并及推送已在 `e86cc35` 完成。

## 2026-10-05 · MONOREPO-258 已完成

实现c05d8e4已以e6d1e13合并本地main，两个检出干净且main3项拓扑通过。13003代理/API8765自动加载v91、HTTP200，127源引擎468d1456680c21d8f5904aa51f20bca1c0b341a4801309624a23b029d7eab354与main一致，新成交说明helper逐字相同；未重启、未改缓存或进程，本轮未推送。用户需刷新页面并重新回测旧结果。

继续复用 `C:/Users/zb/.codex/worktrees/xinhua-squeeze-entry/wavequant-monorepo`、`feat-新华传媒轧空买点`，本轮基线为此前已推送的 main `cdfdf6c`。全局 V3 v91 接纳讲义阴母已知先高后低的同日 B/C 正 N；已有 t/t+1 抵抗且原防守从未破时，最早 t+2，强势棒高点/收盘分别严格越过原 N 突破棒。用户确认强势形态为实体至少开盘 3%、至少振幅 60%、上影至多振幅 20%，各比例等号允许；不加入次日提前确认。弱 N 仍须量大于前日，深 B 恢复保留整段至少 2/3、已知逆 N 的归属及时序、B 底和严格收复杀多高。

真实536日夹具及2018起1508日正式账户重放确认03-05正N、03-20唯一该N LONG/BUY filled约1000股，观察raw收4.84元、正式5bps滑点成交4.84242元，三形态比率4.3103%/86.9565%/13.0435%；03-21扩展前缀信号/审计完全相同，同N不重复。最终注释修正后6源hash前后相同、12checks全true。82项及5子用例、独立73项、星网1项和相邻39项通过（35项重叠不重复累计）；2019新华传媒、星网02-13及西电旧record恢复保留。相关mypy、类型、Core/Web构建、wheel隔离安装、Web44项/3拓扑、Harness和只读复核通过，最终63来源摘要9c7045db...。

本功能标done，恢复MONOREPO-256为唯一in-progress并保留其4项既有失败证据；不在本轮扩大修复。按默认工作流提交并本地合并，最终Git结果见本轮交付；本次新增修改不自动推送。完整Core/Web套件、浏览器/Playwright及外部分钟执行未运行，留待独立验证。前次257完整截至2026的HTTP回放受其他服务重启中断，仍不能写成通过。用户页面需刷新并重新回测旧结果。

## 2026-10-05 · MONOREPO-257 已完成，完整后续历史待独立验证

会话worktree `C:/Users/zb/.codex/worktrees/xinhua-squeeze-entry/wavequant-monorepo`，分支 `feat-新华传媒轧空买点`，基线main `2cb3819`。实现1641816已以299d330本地合并，分支已同步该main；实际13003/API8765为v90，引擎728924798c0776761b578faa4c7fdd235fb1d7035896e1f2e2de7d274ca82d57与main源码一致，main3项拓扑通过。新华传媒02-25买点归属02-19正N，02-20已有抵抗；02-22原始开5.68低于前收5.77，被旧实现当作新增抵抗阻挡，虽然收6.35严格突破此前高6.06且原防守5.46完整。全局V3正N现仅取突破当日及次日抵抗，后续K形态不延长该N抵抗。277日线真实夹具及正式日线收盘模拟确认02-22买入、02-25同N不重复，量过滤与前缀一致性保留。

完整截至2026-09-30的HTTP任务f0a29ca6在服务退出后丢失；确认原管理PID不存在且无端口监听后，按既有入口隐藏恢复PID10116。随后其他本地流程于00:26及00:28重启同服务、写入新的管理PID34604，第二个任务f2dd72a4也变为404；未终止其他流程，未宣称完整后续历史完成。当前13003/API8765仍v90且源码指纹一致。正式2018-01-01..2019-02-25的277根完整账户任务4ba0c9d8-4758-4283-a594-20a9fa5fa459已complete，参数与原界面一致；02-22唯一轧空LONG/BUY filled原始6.35元500股，same_day_close和既有非一字涨停模拟，抵抗02-20、窗口02-19..02-20；02-25 BUY=0。当前买点修复已完成，257标done，恢复256为唯一in-progress并保留其旧失败证据。后续如需完整截至2026回放，应等其他重启流程结束后再运行；浏览器、完整套件和外部分钟待独立验证。未推送。

用户要求暂不加入次日中大阳短上影提前确认，已撤回，最早仍第三棒。星网02-13开发阶段失败随撤回消除，未修改旧断言或五顶门禁。累计87个定向及5子用例、相关mypy、Core既定typecheck及构建、Web lint/typecheck/build、3项拓扑、文档格式、Harness和独立只读复核通过。当前买点及正式账户历史验收完成，完整套件、浏览器、外部分钟和受服务重启影响的后续完整历史待独立验证。

## 2026-10-04 · MONOREPO-256 已合并并常驻生效

会话 worktree `C:/Users/zb/.codex/worktrees/combined-a-entry/wavequant-monorepo`，分支 `feat-组合A回调买入`，基线 main `c99b825`。实现 `ffd49b9` 及扫描原因修复 `46bb7bc` 已经由 `9d6893b` 本地合并；拓扑换行校验 `ef0290b` 经 `eb43b64` 本地合并。用户授权的合并及服务生效已完成，不推送。后续同会话继续复用此 worktree/分支，修改前同步最新本地 main。

V3 v89 回调时间按 C 顶到放量突破日计算，包含低点之后的长期窄幅横盘。国芳 2025-04-03 的 58 日虽然不超过内部 B 的 70 日，但超过最末同源子 B 的 9 日，满足严格择一条件；最低收盘 6.215769 守 2/3 位 5.918297，放量中大阳线收盘 7.158380 突破此前已知高点 7.049092。普通 N 原拒因 `wave_second_peak_pullback_sequence` 保留，新独立通道产生当日 LONG。

较广回归仍有 4 项主分支既有失败，已在 clean main 明确导入 main/src 复现。`test_wave_entry_epoch.py` 两项 January6 的旧断言要求 local epoch recovered，但实际 N 和入场同属 epoch138；实际买入日期、价格和风控断言通过。`test_nested_alternation_breakout.py` 两项华瓷 January17 缺预期一级上下文而无信号。启停组合通道证据不变，未修改旧断言。此前按 AGENTS“检查未通过……无法通过……不合并”保留 feature in-progress；2026-10-04 用户已明确回答“允许合并并重启”，允许保留这四项既有失败覆盖本次门禁，不推送。保留failed事实，Harness不能将带failed证据功能标done；旧 MONOREPO-231 所有失败证据保留，暂设backlog以保持单一活动功能。

实际用户页面是 `http://127.0.0.1:13003/research?page=workspace`，API8765；此前仍运行 main c99b825/v88，新原因helper404，旧正式回测04-03无LONG/BUY。合并后 API 随 main 源码自动重载，由10756变为29192，仍属 `.codex-runtime/wavequant-dev.pid=24196` 已登记服务树；13003/API8765策略 v89、原始引擎 `6cd79d78ecc4c96a2ce4bc2a7f94475f2b1ecabd2dda7f3944d4d153022faaaf` 与 main 源码一致，新原因helper200，旧引擎完成摘要已失效。受控进程树终止命令曾被自动审批拒绝（仅返回 blocked by policy），未绕过；之后确认现有自动重载已实现新版本生效，未强行重复终止。现有 restart-wavequant.ps1 等待硬编码3003与实际13003不符，后续不要直接套用其默认端口。

正式 HTTP 用户参数完整账户任务 `7346d5b8-9101-4342-8a1f-fa124ca2f711` 已 completed：2018-01-01至2026-09-30，AkShare2123日线，volume_filter=false、net_reward_risk_filter=false、浅回撤开、本金100000、最大权重1，52笔filled订单。04-03唯一组合BUY/filled原始5.24元500股、费用后RR1.62725689；LONG、完整decision_evidence和entry_conditions保留。首次同步长请求超时但后台正常完成，任务接口与同参数缓存重试均HTTP200。此前只读1759日线信号前缀78.744秒、正式账户前缀81.87秒也通过；现已补齐真实接口全窗账户回放。04-03缺分钟仍按既有日线收盘回退和非一字涨停模拟，不宣称实盘排队成交。

Web信号、成交详情和复制共用完整组合proof，扫描列表已补独立分类及AkShare/TDX理由消费，Backend扫描JSON保留18个必需proof字段。实际HTTP成交经过共享helper、reasonText、numberedTradeReasons及buildAnnotations生成“组合A回调放量突破”、58/70/9、2/3和已知高点量价证据；当日B标记stock-order-39存在，独立只读复核通过。通用label返回英文不影响原因展示链；原始订单/信号表直接显示原因代码为既有行为。普通OHLCV复制仍只包含行情字段，查看完整原因需点击B/BUY或LONG标记。

新规则24项、十满17项、Web原65项及追加18项Node、拓扑3项、相关类型/格式和Core/Web构建通过。合并后main三项拓扑曾因CRLF/LF摘要差异有1项失败；仅校验归一换行并增加CRLF不变/其他字符变化必变断言，54来源两检出摘要一致，最终worktree及main同3项通过。策略拓扑指纹 `5655992d616d91db95743fd746aff3cf0c99ababfe8a5354a8f13503f92261ab`，Core源码变更后须同步门禁。完整Core/Web套件、真实浏览器/Playwright和外部分钟执行未运行；四项已确认基线失败待独立处理，不自动扩大本次修复范围。

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
