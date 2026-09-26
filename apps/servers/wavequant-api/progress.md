# Progress

- MONOREPO-193：运行中的回测任务快照与单任务查询公开独立的 `progress_percent` 和 `progress_stage`，进度单调递增且未完成时最高 99%。API 42 项测试及 10 个子测试通过；真实浏览器展示未验证。

- MONOREPO-191：常驻 API/Worker 启动器限制为 `main`；一次性 Python 命令仍可在特性 worktree 运行。特性分支启动拒绝检查及 Node 语法检查通过；完整 Python 套件未运行。

- MONOREPO-183：API 启动按量化 Core 引擎版本清理旧回测任务完成摘要，并触发各缓存作用域的旧回测派生记录清理。人工只读确认活跃任务与历史摘要均为 0；AkShare/TDX 回测入口缺参均返回 400，网页手动回测接口保持可用。依用户要求未运行自动化测试、构建或回测，待人工验收。

- MONOREPO-181：运行任务记录单调时钟开始时间，`GET /api/backtest-jobs` 与运行中的 `GET /api/backtest-job` 返回 `elapsed_seconds`；并发上限 4 和同股互斥不变。现场排查乐心时任务数从 1 增至 2，均未满载。API 定向测试 41 项及 10 个子测试、Ruff 检查与格式检查通过；真实 `sz.300562` 任务完成并返回 60 笔成交。

- MONOREPO-182：`GET /api/backtest-jobs` 在运行任务之外返回最近完成摘要，按请求签名在结果根目录 SQLite 保留最多 256 条、最长 7 天，服务重启后仍可读取；结果正文仍按原有 24 条/15 分钟短期保留。摘要仅含规范化参数、策略版本、状态、实际成交笔数与结果可读取标记，版本/参数不匹配由前端排除。API 95 项通过、1 外部环境项跳过、18 子测试通过，Ruff 与构建通过；目标文件 mypy 仍有 8 项既有错误。

- MONOREPO-180：TDX/AkShare 及无任务 ID 的单股回测统一进入有界任务注册表；全局按股票互斥，满 4 个直接返回 503 `BACKTEST_CAPACITY`，同股返回 409 `BACKTEST_SYMBOL_RUNNING` 和现有任务 ID。`GET /api/backtest-jobs` 与拒绝响应提供原子运行快照，前端可同步徽标。API 92 项通过、1 跳过、18 子测试通过；Ruff 检查与格式检查通过。

- MONOREPO-179：API 消费 Core 单源行情仓库后，AkShare/TDX 目录与日线响应均报告唯一的对应 provider、supplemented=0；所选源不可用时保留明确错误和 AkShare 503 语义。真实 8765 接口核对华瓷 AkShare 回测 09-15 加仓已成交；API 定向 40 项测试与 10 个子测试通过。

- MONOREPO-176：TDX/AkShare 回测增加幂等任务 ID 和查询接口，客户端断连后计算结果可恢复；无 ID 的同步 GET 保持兼容。新增策略版本接口供自选股队列检测配置和源码变化。API pytest 89 通过、1 跳过，Ruff 检查及打包通过；全量 mypy 仍受既有依赖类型和旧代码错误阻断，新任务模块 mypy 通过。

- MONOREPO-117：当前股票回测 API 缺省 max_position_weight 改为 1.0；显式配置仍按原有边界校验并传入 Core。69 项 Python、4 项 Node（1 项基础设施跳过）及 Ruff 通过。

- MONOREPO-116：TDX 与 AkShare 当前股票回测 GET 支持 initial_capital、max_position_weight，缺省 100000 元/0.5；拒绝非数值、非有限值及越界输入，传入 Core 执行配置。API lint、类型、构建、69 项 Python 与 4 项 Node 通过（1 项基础设施测试跳过）；真实 TDX 响应返回正确配置。

- MONOREPO-109 消费方：V3 v10 配置传递 exit_on_target=false，API 4 项 Node、68 项 Python 通过（1 项环境跳过）。完整瑞凌 2018 起点回测因新持仓路径需要 2026-05-27 五分钟数据而返回 OHLC 不一致；日线高 10.66、五分钟高 10.62，保留显式失败，待数据修复或用户授权更改撮合模型。

- MONOREPO-100：API 运行依赖加入 `wavequant-core[data]`，V3 单股回测通过 Core 拉取并缓存经日线核对的五分钟行情；响应包含分钟来源摘要、决定时间及模拟成交时间。68 项 Python 测试、4 项 Node 测试、Ruff 与构建通过；BaoStock 2018–2019 缺失时仅在实际需要该日分钟线时拒绝运行。

- `MONOREPO-102`：当前股票回测新增 net_reward_risk_filter 参数，默认 false，严格校验 true/false 且拒绝重复，写入执行配置与缓存键。4 项 Node、68 项 Python 通过，1 项环境测试跳过；lint、类型和构建通过。

- `MONOREPO-099` 已完成：本地长驻 API、通达信信号与 AkShare 结构 Worker 的 Python 启动包装器监测 Core/API 源码；稳定保存后替换旧进程树，启动失败延时重试，生产/预发布及一次性命令不监听。新增 4 项 Node 回归覆盖合并保存、父子进程清理和失败退避；API 68 项 pytest 通过、1 项外部基础设施测试跳过，Ruff、mypy、sdist/wheel 通过。真实 9365 服务在临时源码增加/移除时均自动换 PID，同源健康与瑞凌股份回测返回 HTTP 200；验证探针已移除。

- `MONOREPO-085` 消费方验证：接口复用更新后的 Core 算法指纹及默认 V3 配置，实际通达信回测返回三级交替来源和确认日；66 项 API 测试通过，1 项依赖外部基础设施的测试按既有环境跳过。本地 API 已重载新引擎。

- `MONOREPO-082` 已完成：API 继续通过统一变体配置输出新增收盘价半幅策略，变体计数与响应测试更新；66 项 API 测试及 Ruff 检查通过。

- `MONOREPO-067` 已完成：AkShare 结构代际除数量完整外还校验目标行情日覆盖多数股票；日期异常的完整代际不会替换上一份健康读模型。
- `MONOREPO-064` 已完成：AkShare Worker 使用市场目录日期，结构读模型在新代际重建时持续服务上一完整代际，并在发布完整后原子切换。

## Current State

- `MONOREPO-062` 已完成：Core 空翻多失效语义已进入结构算法指纹，多周期快照版本种子同步提升；2026-09-07 AkShare 新版本已由八分片 Worker 发布 5,562 / 5,562，常驻进程会继续响应新行情日。
- `MONOREPO-060` 已完成：Web 统一开发入口向 API 和 Worker 注入基础设施配置，在启动页面前等待本地 MySQL/Redis 健康并幂等建表。
- `MONOREPO-058` 已完成：多周期行情和结构画线作为独立版本化只读快照持久化，并提供幂等预计算 Worker、Redis 热缓存与 ETag 条件读取。
- `MONOREPO-056` 已完成：AkShare 与通达信 view/theory GET 接口接受可选 `timeframe`，缺省日线并拒绝未知周期；HTTP 层保持薄适配，API 应用服务聚合 Core 规范日线并把结果交给相同的结构算法。
- `MONOREPO-055` 已完成：八个稳定动态分片已处理页面日期 2026-09-07 的 5,562 / 5,562 只当前有效 AkShare 股票；停牌股票保持统一市场基准日，截止日后上市股票发布明确空快照，当前分区完成后 Worker 空闲等待新行情日或算法版本。
- `MONOREPO-054` 已完成：AkShare 与通达信股票目录响应包含基于规范 JSON 的稳定 ETag；匹配 `If-None-Match` 时返回 304/空响应体，目录变化时返回 200 和新版本。
- `MONOREPO-053` 已完成：2026-09-07 AkShare 全目录结构 Worker 已从既有 296 个完成分片恢复断点续算；查询继续只聚合 SQL/Redis 已发布版本，不因页面读取触发计算。
- `MONOREPO-052` 已完成：结构快照接受 `bullish_turn` 并直接过滤已发布的转多事件；查询不调用 Core 计算，算法变化后由 Worker 以新指纹逐股重建 AkShare 分片。
- `MONOREPO-050` 已完成：结构快照查询按上证、深证、创业板、科创板、北京市场组合过滤，默认前三类，并在服务端排除名称含半角或全角星号的股票。
- `MONOREPO-049` 已完成：AkShare 结构查询聚合服务器已发布的逐股分片，跨买点策略复用同一结构算法版本；全目录 Worker 隔离单股失败并继续后续股票。
- `MONOREPO-048` 已完成：买点和结构信号各自拥有 SQL 完成快照与 Redis 查询缓存，HTTP 只读已发布版本且不再暴露启动/取消扫描的写接口。
- `MONOREPO-047` 已完成：既有 AkShare/通达信 HTTP 端点保持兼容，但统一委托 Core 市场数据仓库并返回请求源、实际源、补齐来源和数据版本。
- `MONOREPO-044` 已完成：API 通过后台 Worker 生成带行情/算法版本的 SQL 结构快照，查询端点只读取完成版本并以 Redis 加速。
- `MONOREPO-043` 已完成：API 提供独立的结构搜索启动、增量查询和取消端点，HTTP 层不复制地标判断规则。
- `MONOREPO-042` 已完成：API 透明返回 Core 生成的各级交替后首段多头高点与完整前置证据。
- `MONOREPO-041` 已完成：API 透明返回 Core 生成的各级已确认空多交替低点及其因果证据。
- `MONOREPO-038` 已完成：API 透明返回 Core 生成的二级高点交替升级证据和最终因果可用日。
- `MONOREPO-037` 已完成：API 透明返回 Core 生成的二级发展路径、内部转折数和待决尾部点数。
- `MONOREPO-036` 已完成：API 透明返回 Core 生成的二级开放尾段末跌高换锚事件及其一级确认低点证据。
- `MONOREPO-035` 已完成：API 透明返回完整三级发展路径、内部转折数与待决尾部点数。
- `MONOREPO-034` 已完成：API 原样提供 Core 生成的发展中三级尾段，不在 HTTP 层重算结构规则。
- `MONOREPO-012` 已完成：本 workspace 具备可切换 SQL 持久化与 Redis 缓存基础设施。
- `MONOREPO-013` 已完成：静态资源白名单支持默认全景看盘页，并保留原研究工作台 `/research` 路由。
- `MONOREPO-014` 已完成：静态服务读取 Next.js `out` 清单，并继续将 `/research` 映射到兼容工作台。
- `MONOREPO-016` 已完成：默认入口承载原研究工作台，新增 `/market` 全景页别名并补齐 TDX 现场回测运行依赖。
- `MONOREPO-017` 已完成：API 支持不依赖静态构建的开发模式，并接受显式声明的本机 Next.js 代理来源。

## Completed

- MONOREPO-067 将“超过半数分片 stale”定义为系统性市场日期错误，查询会跳过这类代际并选择最近完整健康快照。真实 MySQL/Redis 请求已从 2026-09-15 坏快照的 0 个二级转多、5556 只过期，恢复为 2026-09-14 健康快照的 7 个结果、12 只过期；后台继续预计算目标日。

- MONOREPO-064 增加结构代际聚合索引和完整度查询，分片持久化 `market_total`；读路径优先当前完整代际，否则返回最近完整代际，首次生成从零分片起返回明确的 rebuilding 空结果或已发布部分。真实 MySQL/Redis 请求在重建期间对 2026-09-14、2026-09-15 均返回 HTTP 200，而非 503。

- MONOREPO-064 API 门禁通过 Ruff、严格 mypy、65 项 pytest（另 1 项跳过）和 sdist/wheel 构建；测试覆盖行情日不降级、空库重建响应、上一完整代际回退、部分首代可读及完整后原子切换。

- MONOREPO-060 真实联合启动确认 API 基础设施状态为 `ok`，MySQL/Redis 均健康；既有 AkShare 结构快照可在服务重启后直接读取，Worker 继续负责行情或算法变化后的后台重建。

- MONOREPO-058 新增 `wavequant_market_timeframe_snapshots`，以来源、股票、请求/实际截止日、周期、数据版本和可执行算法指纹发布完整 view+theory bundle；`timeframes:refresh/watch` 支持全目录、定向股票与 1–16 分片，Redis `market:timeframe:v1:*` 可由 SQL 重建。

- MONOREPO-058 API 门禁通过 Ruff、严格 mypy、61 项 pytest（另 1 项跳过）和构建；真实 MySQL/Redis 初始化、健康检查及贵州茅台五周期幂等预计算通过。

- MONOREPO-056 API 为 `/api/tdx-view`、`/api/tdx-theory`、`/api/akshare-view`、`/api/akshare-theory` 增加兼容的周期参数传递，旧请求继续按 `1d` 返回。

- MONOREPO-055 API 消除逐股合并目录扫描，支持 1–16 个稳定互斥分片、未发布断点续算、统一市场基准日、截止日后上市空快照、先按股票取最新版本再分页和合法 Redis 市场组合键；真实覆盖 5,562 / 5,562，失败 0、跳过 4，Ruff、严格 mypy、53 项 pytest（另 1 项跳过）和构建通过。

- MONOREPO-054 API 门禁通过 Ruff、严格 mypy、49 项 pytest（另 1 项跳过）和构建；真实 AkShare 目录首次响应约 1.24 MB，匹配 ETag 的二次请求返回 304、0 字节。

- MONOREPO-053 真实 Worker 固定 `2026-09-07`、`lecture_v3` 与当前结构算法指纹运行，覆盖由 296 增至 342 后继续增长；同日只读 API 保持 `ready`，后台计算和交互查询边界未改变。

- MONOREPO-052 API 门禁通过 Ruff、严格 mypy、49 项 pytest（另 1 项外部集成跳过）和 sdist/wheel 构建；真实 AkShare `bullish_turn` GET 在 2026-09-14 截面返回新算法版本 `ready`，命中永兴股份、星网锐捷、深物业 A、中国石化和中远通，常驻结构 Worker 已重启并继续扩展覆盖。

- MONOREPO-050 API 门禁通过 Ruff、严格 mypy、49 项 pytest（另 1 项外部集成跳过）和 sdist/wheel 构建；市场组合进入 `signal:structure:v3:*` 缓存键，空值、未知值和重复值均被拒绝。

- MONOREPO-049 API 门禁通过 Ruff、严格 mypy、47 项 pytest（另 1 项外部集成跳过）和 sdist/wheel 构建；真实 AkShare Worker 在单股失败后继续发布，页面当前 `lecture_v3` 查询跨策略命中市场快照。

- 新增 `wavequant_buy_signal_snapshots` 与 `wavequant_structure_signal_snapshots`，按运行、策略、场景、来源、股票范围、日期、数据版本和算法版本建立确定性快照；不修改旧表，显式 `infra:init` 以加法方式建表。
- 新增 `signals:refresh` / `signals:watch`：通达信按全市场版本刷新，AkShare 要求显式股票列表；数据或算法未变化时幂等复用，计算失败时不覆盖上一完成版本。
- 新增只读 `GET /api/buy-signals`，并统一 AkShare/TDX `GET /api/structure-signals` 走 SQL/Redis；旧扫描 POST/轮询端点不再公开。

- API-only 开发模式支持受限的 loopback Web 根地址：访问 8765 根路由时以非缓存 `307` 跳转到 Next.js，外部主机、路径、查询和无显式端口的重定向目标均拒绝。

- HTTP Server 与 Dashboard 启动入口已从核心包移入本 workspace。
- API 仅依赖核心包，并从独立 Web workspace 读取静态资源。
- 新增 `/research`、`/market.css` 与 `/market.js` 的受限静态资源映射和 HTTP 契约测试。
- 支持 Next.js `_next` 构建资源、旧研究兼容资源与安全的静态文件清单，不提供目录遍历。
- 新增 SQLAlchemy 研究运行仓储，支持 MySQL、PostgreSQL 与 SQLite 测试方言的幂等写入和查询。
- 新增 Redis JSON 缓存、短期锁、故障降级和不泄露连接信息的健康检查。
- 新增回环绑定的 MySQL 8.4 + Redis 8 Compose 环境，以及显式建表和健康检查命令。
- 将 `wavequant-core[tdx]` 设为 API 必需依赖，保证安装 API 后即可读取通达信除权除息并运行现场复权回测。
- 新增 `--api-only` 与可重复的 `--allow-origin` 参数；GET 与买点扫描 POST 统一校验显式回环代理来源。
- MONOREPO-034 保持 API 为透明消费边界：`tertiary_trends.developing_strokes` 与正式三级结构一同返回，字段、因果日期和显示语义均来自 Core。
- MONOREPO-035 延续透明消费边界：`developing_point_count`、`nested_turn_count`、`pending_point_count` 及逐点角色由 Core 生成，HTTP 层不压缩或补点。
- MONOREPO-036 延续透明消费边界：二级换锚事件的旧低、跌破日、新高、确认低点、来源级别与因果可用日均由 Core 生成，HTTP 层不补点或重算趋势。
- MONOREPO-037 延续透明消费边界：`secondary_trends.developing_strokes` 的来源位置、角色、计数和因果日期均由 Core 生成，HTTP 层不压缩、不补点、不把它传入三级趋势。
- MONOREPO-038 延续透明消费边界：`alternation`、`provisional_reversal`、旧二级关键位和 `available_at` 均由 Core 生成，HTTP 层不复制升级算法。
- MONOREPO-041 延续透明消费边界：`bear_bull_alternation_lows` 的前置高点、原低点、冻结末跌高、回档比例和可用日均由 Core 生成，HTTP 层不重算或补点。
- MONOREPO-044 新增 `wavequant_structure_snapshots` 事实表、完成后原子发布和 `GET /api/structure-signals`；一次性刷新与常驻检查命令会在行情或 Core 算法指纹变化后重建，Redis 故障不影响 SQL 发布或读取。

## Verification

- MONOREPO-056 API 门禁通过 Ruff、严格 mypy、58 项 pytest（另 1 项跳过）和 sdist/wheel 构建；真实通达信五周期 view/theory 数据版本逐一一致，AkShare 周线可用，未知周期返回 400。

- MONOREPO-048 API 门禁通过 Ruff、严格 mypy、45 项 pytest（另 1 项外部集成跳过）和 sdist/wheel 构建；真实 MySQL 8.4、Redis 8 的建表与健康检查通过，贵州茅台买点/结构快照独立发布并命中带 TTL 的分离缓存键。

- MONOREPO-047 API 门禁通过 Ruff、严格 mypy、41 项 pytest（另 1 项环境跳过）和 sdist/wheel 构建；HTTP 层不复制来源选择、补齐或结构算法。
- MONOREPO-045 API 门禁通过 Ruff、严格 mypy、41 项 pytest（另 1 项环境跳过）和 sdist/wheel 构建；目录、行情、理论三个只读端点覆盖精确参数校验、禁用状态与上游故障 503。重启服务后真实目录返回 5,562 只 A 股，贵州茅台返回 6,003 根日线并标明 `stock_zh_a_hist_tx` 官方回退来源。
- MONOREPO-044 API 门禁通过 Ruff、严格 mypy、40 项 pytest（另 1 项环境跳过）和构建；SQLite HTTP/仓储测试覆盖幂等发布、版本变化重建、查询过滤和缺失快照 503 失败关闭。
- MONOREPO-043 API 门禁通过 Ruff、严格 mypy、34 项 pytest（另 1 项环境跳过）和构建；真实通达信任务启动、增量查询与取消通过。
- API-only 根路由回归通过：`GET http://127.0.0.1:8765/` 返回 `307` 和受限的 `Location: http://127.0.0.1:3003/`；跟随跳转得到 `200 text/html`，`/api/catalog` 继续返回 `200`。
- 本次修复通过 API Ruff、严格 mypy 与 pytest（33 项通过，1 项外部环境跳过）。

- Ruff、严格入口类型检查、23 项 pytest 和 sdist/wheel 构建通过。
- API CLI 帮助、默认 Web workspace 页面、Lightweight Charts 与第三方声明资源集成测试通过。
- 根 `pnpm harness:check` 与 `pnpm verify` 通过。
- MONOREPO-012 workspace 验证已通过 Ruff、严格 mypy、31 项测试（另 1 项外部服务集成测试按配置跳过）和 Python 构建。
- MONOREPO-012 完成后的根 `pnpm verify` 已通过全部 workspace 的 Harness、lint、类型检查、单元测试和构建。
- MONOREPO-013 workspace 验证已通过 Ruff、严格 mypy、31 项测试（另 1 项外部服务集成测试按配置跳过）和 Python 构建。
- MONOREPO-014 workspace 验证已通过 Ruff、严格 mypy、31 项测试（另 1 项外部服务集成测试按配置跳过）和 Python 构建；Next.js 资源、研究兼容页及路径遍历保护契约通过。
- MONOREPO-016 使用源封存结果和 `D:\TDX` 验证 5,895 只带日线股票、贵州茅台 2018–2026 现场回测、买点扫描、幅度比较与同源静态页面；工作台浏览器专项全部通过。
- MONOREPO-017 workspace 验证通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部服务集成测试按配置跳过）和 Python 构建；Next `3003` 代理的扫描 POST 契约已覆盖。
- MONOREPO-034 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；真实接口在最新日与 `2015-07-15` 回放日均返回上海电力同一条因果可见的发展中三级尾段。
- MONOREPO-035 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；最新上海电力接口返回 14 个发展点、8 个已确认内部转折及 4 个待决尾部点。
- MONOREPO-036 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；上海电力真实接口在 `2026-07-16` 尚未换锚、于 `2026-07-17` 因果可见地换锚到 `2026-05-29 H27 22.35`。
- MONOREPO-037 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；中大力德真实接口返回 17 个正式二级点与独立的 30 点发展路径，三级正式点仍为 3 个。
- MONOREPO-038 API 消费方门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项外部环境测试跳过）和 Python 构建；中大力德真实接口在 `2023-02-06` 首次返回升级后的 `2022-08-03 H70 49.56` 及完整三段证据。
- MONOREPO-039 延续透明消费边界：API 原样返回三个趋势级别的 `bear_to_bull_highs`，不在 HTTP 层选择窗口高点或重算确认日期；真实回放在 `2022-08-09` 首次返回中大力德 H70 地标。
- MONOREPO-039 API 门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项环境跳过）和 Python 构建；`2022-08-08`/`2022-08-09`/最新日三个真实截面通过因果可见性核验。
- MONOREPO-040 API 透明返回收紧后的 Core 地标；中大力德、首创环保、华夏银行、上海电力的一级、二级、三级真实结果均通过“高点严格大于冻结末跌高”审计，非法计数为 0。
- MONOREPO-041 API 门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项环境跳过）和 Python 构建；中大力德真实接口在 `2022-08-31` 无 L71 地标，于 `2022-09-01` 首次返回完整证据。
- MONOREPO-042 API 门禁通过 Ruff、严格 mypy、33 项 pytest（另 1 项环境跳过）和 Python 构建；中大力德真实接口在 `2022-09-07` 无 H71 地标，于 `2022-09-08` 首次透明返回完整证据。
- MONOREPO-045 服务重启后真实 AkShare API 验证通过：中国平安首次回退 2.85 秒，美的集团后续回退 0.98 秒，均返回 2026-09-11 的原始不复权日线和股单位成交量；API 41 项通过、1 项环境跳过。
- MONOREPO-046 的买点 POST 支持 `source=akshare` 与单一 `symbol`，结构 GET 支持当前股票即时分析；贵州茅台真实请求均完成 1/1、失败 0，API 41 项测试通过、1 项环境跳过，Ruff、严格 mypy 与构建通过。

## Risks and Next Steps

- 服务当前保持原有 loopback-only 安全边界，不作为公网 API 部署。
- 本机 Docker daemon 当前未运行；Compose 配置已通过静态解析，真实 MySQL/Redis 集成测试需在 Docker 可用后通过环境变量显式启用。

## 2026-09-20 · MONOREPO-109 同源回测

- 在线日线、五分钟和复权因子固定 AKShare / 新浪，周月线由同源日线聚合；禁用跨源补齐，通达信只用本地原生分钟文件。
- V3 保持 exit_on_target=false；点击回测保留来源。缺失分钟返回 data_unavailable，保留策略推演，清空账本与收益。
- 瑞凌 2018 起点需要 2025-05-15 分钟，当前源仅完整覆盖 2026-07-24 至 09-18；历史长区间尚不能完成。07-24 至 09-07 已完成、32 日、零成交。
- Core 669+5、API 68+4（1 跳过）、Web 9+121、Playwright 2 项及 lint/type/build 通过。未提交 Git。

## 2026-09-20 · MONOREPO-110 分钟缺失回退日线

- 用户授权按交易日选择：完整同源五分钟优先，缺失当日分钟则以日线低点/收盘判断、当日收盘加滑点费用撮合。保留 T+1、整手、风控和 V3 目标不自动退出。
- V3 v11 启用 missing_minute_daily_fallback；账本含 minute_fallback，汇总含 minute_fallbacks，页面及复制说明可区分成交口径；矛盾数据继续报错。
- 瑞凌 2018-01-02 至 2026-09-07 全区间完成：2107 日、3 买 4 卖、9 日回退判断；08-07 无目标退出。原分钟覆盖阻塞已按本次授权解决。
- Core 677、API 68+4（1 跳过）、Web 9+121、Playwright 2 项及相关 lint/type/build 通过。未提交 Git。

## 2026-09-20 · MONOREPO-111 倒 N 后累计九成

- V3 v12：实际支撑减仓后确认独立倒 N，只补卖至首次减仓前持仓 90%，整手约束保留；重复信号不重复卖。其他清仓风控优先，倒 N 收盘确认后次开盘成交。
- 瑞凌 2025-05-15 减仓后 05-23 已止损清仓，05-30 才确认倒 N；不能前移信号。2026-06-11 新规则实际补卖，06-25 清余仓。
- Core 全量 682 + 追加止损优先回归、API 68+4、Web 9+121、类型/构建及构建页面 Playwright 2 项通过。全量 Web lint 受其他改动的 market-dashboard.spec.ts:50 格式错误影响；本次变更文件 ESLint 通过。未提交。

## 2026-09-20 · MONOREPO-112 修复 05-19 倒 N 漏报

- 根因：C 在 05-19 收盘确认，旧代码只从其下一棒检查，漏过当天首次双破。V3 倒 N 显式允许历史 C 的确认当日攻击，要求 B 此前已知；正 N 与旧版默认不变。
- V13 真实回测：05-19 产生倒 N，05-20 补卖 1829.45850054 等价份额，余 715.87506543，累计减仓 90%；05-23 再止损清余仓。此前 05-30 才确认的结论已纠正。
- Core 685、API 68+4（1 跳过）、相关 lint/type/build、构建页面 Playwright 2 项通过。未提交 Git。

## 2026-09-20 · MONOREPO-113 正 N 该回不回与退出链

- V14 按已说明的“抵抗后下一根守虚拟低且收高”口径，V3 局部抵抗失败不等待整段创新高；无抵抗强轧空及旧版默认分类兼容。
- 修正回踩确认后仍要求反弹超过整根 K 高点的额外门槛；母子 K 同日 A/B 可用更早独立高点形成较大倒 N，仍检验严格几何及因果时序。
- 瑞凌 05-08 确认、05-11 买入；05-26 支撑双破、05-27 九成目标、06-02 弱反弹清余仓。新增证据显示 N/抵抗日期与虚拟低、确认低及收盘。新买点也会改变其他持仓与保护价，不固定原账本金额。
- Core 688+43、API 68+4（1 跳过）、Web 9+121、相关 lint/type/build、浏览器2项及追加买点详情核验通过；真实截至05-08前缀信号与全历史一致。未提交 Git。

- MONOREPO-152：V44六种幅度方案经共享配置校验并维持不同回测身份；配置独立性测试不再固定五组。API69项Python通过、1项环境跳过，4项Node通过。

- MONOREPO-153：新增GET /api/limit-up-ladder，AkShare stock_zt_pool_em按日期读取。日期独立LRU128、当日/空池60秒历史1小时，强制刷新失败不补旧数据；数值缺失null、保留元单位、重复/异常核心字段拒绝。85项Python+4项Node通过（1环境跳过），lint/严格mypy/build通过；真实09-21 103只、09-18 78只。

## 2026-09-25 · MONOREPO-172 浅回撤买点开关

- TDX 与 AkShare 当前股票回测接受 `shallow_base_breakout_enabled=true|false`，缺省 true；重复或非法值拒绝。参数进入独立回测缓存键与策略定义，切换结果可复现。API 85 项、18 子测试通过（1 项外部环境跳过），Ruff 和包构建通过；真实 TDX API 与 Chromium 验证开启有国芳 2025-04-03 买入、关闭无该信号。全量 mypy 受既有 NumPy stub/Python 版本不匹配及 server.py 184–308 行类型错误阻塞，新增参数行无相关报错。
