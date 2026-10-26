# 指数搜索有名称却没有研究数据（2026-10-26）

## 当前运行线路（2026-10-26后续修复）

沪深指数已固定使用AKShare `stock_zh_index_daily` 新浪日线，北证仍保留 `stock_zh_index_daily_em`。下文东财记录是故障历史。东财直连被断开后，本轮获准继续仅上证指数联调，新浪SDK取得完整历史并恢复页面。来源、成交额缺失语义、Redis隔离及冷热耗时详见[取数阶段诊断](index-fetch-stage-2026-10-26.md#恢复取数后的最终结果)。旧Python业务和趋势判定未改。

此前Ctrl+K只附加了常用指数身份，结果跳转外部行情；研究链接与Rust取数仍只接受A股代码，默认调用`stock_zh_a_daily`和个股复权因子接口。因此“搜到名称”不能表示项目内已经有指数K线。

本轮补齐同一条链路：

- 搜索中的上证指数等常用指数进入本地`/research?page=workspace&symbol=sh.000001&source=akshare`，与平安银行`sz.000001`保留不同身份。
- 研究链接、显式研究证券、预览、结构请求及缓存共同接受在线指数身份；不把指数混进A股自动扫描目录，不扩大通达信个股支持范围。
- Rust调用常驻AKShare桥接的`index_daily`白名单操作，使用`stock_zh_index_daily_em`；请求按当天合并，历史行情身份只散列所选日期前缀。SDK接口签名已核对官方源码和本机安装源码。[AKShare官方实现](https://github.com/akfamily/akshare/blob/main/akshare/index/index_stock_zh.py)
- 指数点位不经过个股复权，因子恒为1；成交量由东财手数统一为股，响应注明指数/东财/点位及原始指数口径。K线、复制与N提示使用“点”。
- 指数Redis快照使用独立`akshare-eastmoney-index-kline-v1`身份及`unadjusted_index`口径，继续复用容量、LRU、交易日新鲜度与收盘补齐机制；保留原有个股缓存键，不清空缓存。
- 指数只进入当前统一四级结构/资格计算，行情行的所有成交权限均为false；开启账户模式时明确拒绝直接交易指数。原A股规格校验仍保留。

源码：Rust `data/indices.rs`、`data/akshare.rs`、Rust服务自有`scripts/rust_sdk_worker.py`，API `backtest_request.rs`/`backtest.rs`/`market.rs`/`adjusted_market_cache.rs`，Web `market-indices.js`/`research-link.js`/`research-symbols.js`及证券搜索组件。未更改旧Python服务和量化库业务。

开发阶段新增指数/个股身份、不可成交因子、内部链接及点位单位回归源码，尚未执行。默认启动编译曾遇前端JS目录导入的可空类型和Windows运行中exe文件锁，已分别收窄字段及停止本任务API/Worker后重建；原端口、数据路径及禁用自动回测配置保留。用户随后明确允许仅上证指数单次联调。

## 本轮实际联调及当前限制

首次上证指数请求成功：市场预览和结构接口均为HTTP 200，原生索引响应为`sh.000001`、`complete=true`、状态版本30、2136根K线。真实图层已有1664个折线顶点、132个一级确认点及11个二级确认点；三级参与同一计算，但当时未形成三级线段。页面价格单位为“点”，显示四级趋势完成、交易未运行。本次取数610.7ms、分析92.139ms、后端1361.082ms；这是单次请求记录，不是性能对照或稳定耗时保证。Ctrl+K输入“上证”显示上证指数和上证50，链接为本地研究页，未设置外部新窗口。

随后复查时本任务API退出，日志未记录panic或正常停止，退出原因尚未确定；原进程已恢复。服务重启后内存缓存不保留，本机Redis连接亦不可用，再次访问需要请求上游，此时AKShare指数接口失败。缩小到同一上证指数的数据适配调用后，只输出异常类型，确认系统代理路径为`ProxyError`；Python继承的Windows代理为本机回环端口，未读取或输出凭据。临时仅在本任务进程环境排除代理后，直连仍为`ConnectionError`，因此不能归因于指数代码校验，也不能声称仅取消代理便已解决。临时代理修改已撤销，没有更改Windows全局代理，没有更换或拼接行情来源，没有用旧缓存冒充实时成功。

上一阶段API按原配置启动于8010（PID32868），前端3003（PID19800）；`--no-watchlist-refresh`保留。页面保留上证指数研究地址，但当前取数失败，指数实时可用性尚未验收完成。需恢复本机代理或东财连接后继续复核；不要重复跑其他股票。浏览器中已经发生的连接错误不能以首次成功截图覆盖。

本轮未运行单元测试、lint、独立类型检查、格式化或Git状态/差异检查，未提交或推送。只执行本地生效所需的Rust/Next启动编译、服务重启及用户明确授权的上证指数联调；新增回归源码待实际提交时执行。

## 后续 `invalid stock symbol`：页面连接了另一工作树的旧服务

用户随后报告上证指数被拒绝为`invalid stock symbol`。本分支`BacktestRequest::parse`已经使用`indices::supported(source, symbol)`，其拒绝文案也已是`invalid security symbol`；没有继续放宽交易或个股校验来掩盖错误。

读取本机8010监听进程发现PID25100，实际exe和`--quant-worker`均来自`C:/Users/zb/.codex/worktrees/squeeze-qualification/wavequant-monorepo`。该工作树`application/backtest_request.rs`仍调用`tdx::is_stock`并返回用户看到的原文。因而这一次错误来自运行版本串接，不是本分支指数专用取数返回的网络错误；两种故障必须分别记录。

当时为避免中断另一工作树，曾准备独立8011方案并保留默认8010，未直接停止另一服务。用户随后明确要求将另一工作树合并过来、统一为一套，最终没有启用8011。

## 统一运行版本后的合并检查

2026-10-26 MONOREPO-362 按用户要求整合指数与v31资格：feat-轧空确认资格与当前分支同基线fbea9ea，其未提交的6个Rust核心文件、2份诊断及流程/状态记录已保存patch、文件副本和SHA256后合入当前工作树；来源工作树文件与索引未改，快照后摘要仍一致。保留v31本级严格翻转证明、旧支撑破位只消费一次、指数专用数据接口与完整交易所身份，旧Python业务无差异。最小合并检查：Rust共用内核31、指数身份2、API指数请求2、Node29、证券搜索4、离线SDK桥接11通过，前端严格类型检查通过；首次Node命令Windows绝对路径未转file URL及API误选bin筛选0项已修正，最终实际用例通过。git diff --check初次仅文档尾空行，修整后通过。必要Rust/Next生产启动编译完成；停止来源工作树API25100及其Worker，统一API15876/8010、前端32568/3003，数据路径、Redis配置、SDK解释器和--no-watchlist-refresh保留，不启用8011。浏览器与真实行情未重跑，先前ProxyError/ConnectionError及Redis不可连接限制仍保留；全量lint/共享工具配置检查沿用用户此前允许最小检查继续的安排，本机Node24.14.1与.nvmrc24.16.0有版本差异，未安装或修改系统Node。原有MemoryPoint::read未使用告警保留。准备提交并按仓库流程本地合并main，不推送；整体362仍in-progress。

来源快照与文件摘要：本任务忽略目录`.codex-runtime/merge-index-qualification/manifest.json`、`index.patch`、`qualification.patch`。两分支原本没有不同提交，所以本轮整合的是已备份的未提交文件快照，不将Git的“already up to date”当作代码已合并。来源工作树继续保留原文件；后续统一从当前整合版本启动，避免旧工作树再次替换8010。
