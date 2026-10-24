# Rust 行情源与协议边界

本文件记录原生 Rust 数据适配器的来源和兼容范围。所有模块均为本次源码迁移，未运行构建、验证、接口请求、行情下载或回测；不能把已有 Python 验证结果用作 Rust 的证据。

## 模块与接口

| 模块 | 接口 | 来源与身份 |
| --- | --- | --- |
| `data/tdx.rs` | `TdxReader::catalog/bars/view/stock_metadata` | 本地 `.day` 原始不复权行情及 `.tnf` 当前名称；SHA-256 绑定源字节 |
| `data/gbbq.rs` | `read_actions`、`TdxReader::adjusted_bars` | 本地 `T0002/hq_cache/gbbq`，原生解码，绑定日线与除权文件双版本 |
| `data/adjustments.rs` | `adjust_rows`、`price_limit_bounds`、`opening_permissions` | 原 Python 的因果复权、板块规则、原始价格权限；分位舍入使用 Decimal |
| `data/minute.rs` | `TdxMinuteSource::get`、`verify_minute_day` | 同源 `.lc5`；上海时间完整 48 根、日线 OHLC 与成交量约束 |
| `data/baostock.rs` | `BaoStockClient::history/minute_day` | 原生 BaoStock TCP `00.9.30` 协议；不会调用 Python |
| `data/sina_codec.rs` | `decode_daily/decode_calendar` | 原生 KLC2 日线 3466/version0，日历 139/version0或1 |
| `data/sina.rs` | `SinaProvider::daily/factors/share_amounts/minute_day` | 新浪原始日线、股本、后复权因子和分钟，不跨上游补齐 |
| `data/catalog.rs` | `SinaProvider::catalog/calendar` | SSE、SZSE、BSE 原目录端点；新浪交易日历 |
| `data/cache.rs` | `DataCache::get/put` | 可重建 SQLite 派生缓存，不替代源数据 |

`SinaProvider::catalog(now)` 接受显式 UTC 时间，并换算上海时间：17:00 前使用上一日及之前的已完成交易日。日历不可用时返回显式警告，并按原 Python 规则使用周末排除的保守工作日近似；该近似不是节假日真相。目录含北交所，但固定新浪历史图表模式按原有入口限制处理北交所，不能据目录存在宣称历史可用。

## 参考源码

迁移依据仓库 Python 源码及现有虚拟环境已安装的第三方源码，不是根据接口名称猜测协议：

- `packages/wavequant-core/src/wavequant/infrastructure/market_data/{tdx,data,minute,akshare_history}.py`
- `packages/wavequant-core/src/wavequant/interfaces/charts/{tdx_browser,akshare_browser}.py`
- `packages/wavequant-core/src/wavequant/interfaces/research_tools/akshare_backtest.py`
- 已安装 `pytdx 1.72` 的 `reader/gbbq_reader.py`：4 字节数量头、29 字节记录、三个 8 字节 Feistel 块与尾部 5 字节明文。
- 已安装 AKShare 的 `stock/cons.py`、`stock/stock_zh_a_sina.py`、`stock/stock_info.py`、`tool/trade_date_hist.py`：KLC 位流、JSONP 包装、股票目录、股本拼接及日历补项。
- 已安装 BaoStock 的 `common/contants.py`、`data/messageheader.py`、`data/resultset.py`、`util/socketutil.py`、`security/history.py`、`login/loginout.py`：21 字节帧头、分隔符、CRC32、压缩响应、匿名会话与 2000 条分页。

可查阅的上游项目：[pytdx](https://github.com/rainx/pytdx)、[AKShare](https://github.com/akfamily/akshare)、[BaoStock](http://www.baostock.com)。源码引用用于记录技术来源，不代表上游确认本次迁移的正确性。

`gbbq_keys.hex` 保存 pytdx 所用的公开固定文件格式变换表，不是用户密钥或认证凭据。pytdx 官方归档 README 表述个人学习目的及不得用于商业目的；本次未获得其他授权声明，不能将其标注为 MIT 或宣称可自由商业发行。发行前须确认既有 pytdx 依赖和该协议表的许可适用范围，保留本来源记录。

## 在线请求与精度

新浪日线端点是 `finance.sina.com.cn/realstock/company/{symbol}/hisdata_klc2/klc_kl.js`；股本为同源 `StockService.getAmountBySymbol`；因子为同股票 `hfq.js`。日线及股本按日期外连接、前向填充，先按原始 OHLCV/amount 去重，再按分位舍入、缺失剔除和最终行去重；来源版本同时包含两份响应及请求截止日期。不同股票代码、截止日期或源响应不会共用版本。

分钟端点 `CN_MarketDataService.getKLineData` 使用 `scale=5`、`ma=no`、`datalen=1970`；只接受 JSONP 中的数据，不执行服务端脚本。不足 48 根、成交量缺失或 OHLC 不一致明确失败，不能据此生成收益或跨源替换。BaoStock 通过 `public-api.baostock.com:10030` 的现有公共匿名协议，读写超时、响应与解压大小、分页次数都有上限。

SSE 按主板/科创板调用 `sseQuery/commonQuery.do`；SZSE 读取 `ShowReport` 的 `CATALOGID=1110`、`TABKEY=tab1` XLSX；BSE 按既有 POST 分页参数读取。BSE 当前 Python 按返回字段顺序取证券代码和简称，Rust 显式保留 JSON 对象顺序并在布局不符时拒绝，不猜测不同字段。

复权与图表口径保持区分：`bars/daily/view` 为原始不复权行情；`adjusted_bars/apply_factors` 才是因果复权输入。无 gbbq、名称缺失、历史 ST/退市身份不能确认、起点未晚于上市首日、拆并股类型 11/12 时拒绝执行输入，不把空事件当作确定无除权。

## 缓存

SQLite 缓存键覆盖 provider、endpoint、symbol、asof、codec_version 与完整参数，参数经递归排序后摘要。记录同时包含源版本、内容摘要、创建及过期时间；同一身份/源版本不允许更换内容。缓存有 TTL、容量上限和短锁等待，读取会检查内容摘要，不返回已过期或创建时间在请求时钟之后的数据。上层可通过 `DataCache` 显式接入，不能把未绑定的独立缓存模块写成已启用。

## 尚未覆盖

- 新浪旧日线 1479 已提供 decode_legacy_daily，返回原协议实际包含的 OHLCV；它没有 amount，现代日线/股本管线仍明确拒绝，不虚构成交额。其他未支持编码继续拒绝。
- BrowserHistoryProvider 新增显式非固定浏览模式：东方财富、Sina、腾讯及 600 秒上游冷却；固定新浪模式仍不跨源降级。每次返回实际 upstream/endpoint，后备历史不是同源执行输入。
- BaoStock 历史查询和分钟数据接口已提供，但原数据导入命令、全市场证券主档/财务接口不在此适配器内。
- SinaMinuteArchive 已增加同源历史分钟保存与补历史读取，保存 known_at、源摘要并在消费时再次校验日线；自动缓存装配、生产级缓存监控、数据库/Redis 共享读模型与作业调度仍需上层实现。
- 日期、源码版本、协议错误和 HTTP/TCP 实际互通均待用户允许后验证；不声称目前已有实盘数据兼容证据。

## 表格与历史主档扩展

`data/exchange.rs` 提供 CSV/JSON 行情导入、权限字段保留、同一时区感知性约束、排序与重复时点拒绝，以及CSV/JSON导出。成交量保留浮点原始份额，不为适配整数行情结构而截断。`data/security_master.rs` 对应 Python SecurityFact/SecurityMaster，按 effective_from 与 known_at 选取当时已知事实，新事实过期不复活旧事实。`data/dataset.rs::build_manifest` 对应数据集登记前事实清单：校验源metadata摘要、历史主档、日历、缺失会话与未来行；不把日线文件声明为分钟完整性或全市场完整性。清单写入不可变事件日志仍由上层负责。