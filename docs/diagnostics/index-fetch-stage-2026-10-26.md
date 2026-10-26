# 上证指数15%取数阶段诊断

日期：2026-10-26，MONOREPO-362。用户报告指数停留在“读取同源行情及因果复权”，取数2.360秒、分析0。指数本就不读取个股复权因子；进度文案来自共享producer的固定字符串。

## 本轮已取得的证据

最初本机Docker引擎未运行，6380没有监听，日志连续出现Redis不可用与指数缓存缺失。用户重启后，原`wavequant-api-redis-1`恢复healthy，127.0.0.1:6380映射保持，重启策略unless-stopped，未新建或清空容器及缓存。

用户明确允许仅上证指数单次联调。新版启动后提交一次`sh.000001`，记录于本任务忽略目录`.codex-runtime/index-fetch-fix/single-index-evidence.json`及`single-index-response.json`：

- 实际进度已改为“读取指数日线（无需复权）”。
- 取数1.3047136秒、分析0秒、任务总计1.4秒，HTTP墙钟1446.0513ms。
- 以503返回`BACKTEST_JOB_FAILED`，保留`cause_code=SERVICE_UNAVAILABLE`，消息明确指数日线代理连接失败。
- 系统代理为本机127.0.0.1:58309，进程属于Astar的`net-core.exe`；没有读取或输出代理凭据。API日志中该请求SDK排队近0、SDK往返约1289ms，确认失败发生在取数而非趋势计算。

数据请求尚未成功时，不能因为Redis容器恢复便声称已有该指数缓存命中，也不能将此次失败耗时当作优化前后性能对照。

## 实现

1. 指数使用专属进度文案，缓存来源信息也标注指数日线刷新策略。
2. SDK错误只映射安全的代理、连接、超时及无效响应类别，保留失败请求的排队/启动/往返计时；不输出异常原文、URL凭据或完整上游响应。API单独记录指数取数及映射耗时。
3. 已结束失败任务直接返回`BACKTEST_JOB_FAILED`，与轮询失败语义一致，原原因保存在`cause_code`；版本变化继续返回原代码。前端现有重试器已将该代码作为终态，因此不再把失败任务的503当作暂时状态查询错误重试。历史目录保留失败原因、状态码及失败步骤，供重启后排查。
4. Rust自有SDK桥接对指数公共域名`push2his.eastmoney.com`使用单请求作用域内的`NO_PROXY`直连，退出时恢复全部代理环境；保留系统代理供其他主机使用，用户显式提供的`NO_PROXY`仍优先。常驻SDK串行处理请求，不改Windows系统设置或Astar配置，不更换行情供应商、拼接来源或放宽价格校验。

Rust SDK分类、终态映射及桥接环境恢复的回归源码已补充，开发阶段未执行测试、lint、独立类型或Git检查。必要启动编译与后续直连结果另记；首次授权单次联调已使用，未把失败后第二次请求算作同一成功。

## 直连生效后的现状

2026-10-26 MONOREPO-362 指数直连版本已本地生效：必要cargo +stable build --locked -p wavequant-api-rs -p wavequant-quant-rs --bins完成（5m09s，既有MemoryPoint::read告警），API31536/8010、前端15280/3003，Redis原容器healthy。用户授权的单次人工指数联调已用于定位Astar代理失败，未发起第二次人工请求。页面在新版本启动后自行重试，日志显示已从ProxyError变为ConnectionError：source_total2312.9021ms，其中SDK队列2041.3386ms、SDK往返271.3807ms，趋势仍未开始。指数数据尚未恢复，不能把缓存恢复或文案/终态修复报成完整成功；同类上游错误有官方SDK问题记录，但本机未证明是封IP。已通过异步问题请求是否允许继续仅此指数联调至取数恢复，当前等待回答。系统/Astar代理未改，SDK内作用域直连只针对指数公共主机，显式NO_PROXY优先，其他请求环境恢复。未运行测试/lint/独立类型/Git检查，无提交推送；源码回归待提交验证，整体in-progress。日志.codex-runtime/index-fetch-fix/，诊断docs/diagnostics/index-fetch-stage-2026-10-26.md。

AKShare官方记录中存在该指数接口连接中断的同类报告：[Issue 6805](https://github.com/akfamily/akshare/issues/6805)、[指数SDK官方文档](https://akshare.akfamily.xyz/data/index/index.html)。这些记录只能支持“该链路存在同类失败”，不能据此认定本机IP被封或以旧行情冒充当前成功。

## 恢复取数后的最终结果

用户随后明确授权继续仅上证指数联调至恢复。两次受控诊断只读取sh.000001：东财SDK等价参数直连约319.736ms、官方hist参数直连约307.013ms，均为ConnectionError → ProtocolError → RemoteDisconnected，即HTTP响应之前已断开。未证明封IP，不修改Astar或全局代理。

AKShare 1.18.94的官方新浪指数方法`stock_zh_index_daily(symbol="sh000001")`成功，完整历史8749根，1990-12-19至2026-10-26、1445529B，诊断调用2091.282ms含SDK导入。该接口只有date/OHLC/volume，没有成交额；[官方接口说明](https://akshare.akfamily.xyz/data/index/index.html)同样仅列出这六项。

### 当前实现

- 沪深指数固定为AKShare新浪指数日线；北证保留原东财通道。共用`data/indices.rs`定义接口、上游和缓存策略，预览、分析和Redis同时引用，不在一个快照中拼接或自动切换供应商。
- 新浪指数成交量原样保留为股，不乘100。缺失成交额保留`None`/`null`，不填零或通过价格乘成交量估算。共有DailyBar成交额改为Option，但个股、TDX、旧Rust新浪解码及浏览后备依然要求真实值，原Some数值序列化格式保持不变；旧Python业务不改。
- 沪深指数Redis策略改为`akshare-sina-index-kline-v2`，与原`akshare-eastmoney-index-kline-v1`隔离；原缓存不删除，仍受LRU容量管理。历史前缀身份包含该来源策略、OHLCV及截止日，跨来源不能共用结果。指数单请求NO_PROXY作用域改为实际公共主机finance.sina.com.cn，退出恢复其他SDK环境，显式用户NO_PROXY优先。
- 新浪接口不读个股复权因子，指数无成交权限。折线、一至三级趋势、N及资格算法均未改。

### 实际耗时（开发构建、单次记录）

必要启动编译`cargo +stable build --locked -p wavequant-api-rs -p wavequant-quant-rs --bins`完成4m16s，仅既有MemoryPoint::read未使用告警。API25284/8010，会话77697；前端15280/3003、既有Redis/数据目录及`--no-watchlist-refresh`保持。

| 路径 | 取数 | 实际分析 | 后端总计 | 说明 |
| --- | ---: | ---: | ---: | --- |
| 页面自然重试后的首次完整任务 | 2709.1402ms | 93.7486ms | 4292.4928ms | 首次SDK/Worker启动路径 |
| 人工同参请求 | 37.271ms | 0ms | 196.0115ms | Redis与趋势结果缓存命中 |
| 浏览器页面请求 | 约41ms | 0ms | 约206ms | 页面已显示结果，分析0表示复用 |

首次任务`003ebd41-f600-41f0-8cd9-8460fc6680d5`的producer等待预览占用SDK管道2588.266ms，随后复用同一成功SDK响应，并非再次取数；Worker往返1144.5696ms含进程启动、协议和结果读取，纯分析93.7486ms。Redis快照成功保存728565B。未清空缓存来制造冷请求，也未把首次故障与命中路径当作算法优化的同条件对比。

人工任务`index-sina-fix-b88ab369abeb4a3c842dac26cb65bb31`HTTP200、complete=true，2136根输入、折线/一级/二级/三级点数1379/133/12/2；节点表响应238356B，dataVersion为4ee41eb49761d9f911f9664249419f4f49d195bde6b3103c847e29629c510543。HTTP墙钟480.056ms包含400ms轮询间隔，不等于请求网络延迟。

### 页面证据与边界

研究页`/research?page=workspace&source=akshare&symbol=sh.000001&asof=2026-10-26`显示“各级趋势计算已完成”，真实图表中K线与趋势线可见，7个图表canvas；行情、分析、年度行情及趋势行情接口均200，Console无error/warn。浏览器记录该次分析HTTP约316.4ms、压缩响应23471B。页面不再停留15%。临时范围保护未拦截任何请求，所有行情请求符号仅sh.000001；无保护重载被自动审批拒绝（可能触发其他股票），改为安全结束独立调试页并清除钩子，用户原页面未刷新，未操作其他股票或自选设置。

本轮实际执行的是用户明确授权的单指数联调及启动所需编译。新增SDK来源/日期边界/股数/缺失成交额/代理恢复与Rust字段校验回归源码，未运行测试套件、lint、独立类型检查、格式化或Git状态/差异检查，待实际提交时验证。未提交、合并或推送；MONOREPO-362整体迁移仍in-progress。
