# Progress

## Current State

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
