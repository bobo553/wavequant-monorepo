# WaveQuant Core 分层架构

Owner：WaveQuant Core 维护者
适用范围：`packages/wavequant-core/src/wavequant`
最后验证：2026-09-12；Core、API 消费方与仓库 Harness 门禁通过。

## 目标

WaveQuant Core 是与 HTTP 和浏览器框架无关的 Python 研究内核。目录按“业务职责和依赖方向”组织，而不是按文件类型组织：

```text
interfaces  ───────┐
                   v
application ───> domain
     │             ^
     v             │
infrastructure ────┘
```

- `domain`：价格行为、趋势、N 形、盘态、策略规则和值对象。不得读取文件、连接数据库、解析命令行或依赖其他层。
- `application`：回测、筛选和诊断等用例编排。可以依赖领域层，并通过明确边界使用基础设施。
- `infrastructure`：SQLite、缓存、路径和行情文件等外部适配器。这里保存“如何读写”，不决定“何时交易”。
- `interfaces`：CLI、图表几何和面向展示的读模型。只负责把输入转换为应用调用并格式化输出。

## 功能目录

```text
wavequant/
├─ domain/
│  ├─ models/            # K 线、信号、成交、配置和已校验序列
│  ├─ market_structure/  # 价格行为、N 形、折线和一/二/三级趋势
│  ├─ market_state/      # 主控棒、六态、洗盘、轧空恢复、力度和扭转
│  └─ strategies/        # 基准、讲义、分级、整波及集成策略
├─ application/
│  ├─ analytics/         # 回测、研究、证据、质量检查和验证协议
│  ├─ trading/           # 纸面订单、账户、执行、场所和流式运行
│  └─ governance/        # 离线运维编排和平台验收
├─ infrastructure/
│  ├─ market_data/       # CSV/TDX、数据清单和证券主数据
│  ├─ persistence/       # 事件、实验、缓存和运行状态存储
│  └─ filesystem/        # 项目路径及可验证备份恢复
└─ interfaces/
   ├─ cli.py             # 命令行入口
   ├─ charts/            # 图表几何、可视化读模型和行情浏览器
   ├─ research_tools/    # 单股诊断、策略证据和固定协议验证器
   └─ screening/         # 可取消的本地买点扫描接口
```

包根只保留 `__init__.py` 暴露的小型稳定公共 API；四个层级根目录只保留各层的有意公开入口，`interfaces/cli.py` 是唯一例外。业务实现必须进入上述最细功能目录，架构测试会阻止扁平实现重新出现。

## 导入策略

规范模块入口统一为 `wavequant.<layer>.<feature>.<module>`。例如：

- `wavequant.model` → `wavequant.domain.models.model`
- `wavequant.trend_structure` → `wavequant.domain.market_structure.trend_structure`
- `wavequant.order_service` → `wavequant.application.trading.order_service`
- `wavequant.visualization` → `wavequant.interfaces.charts.visualization`
- `python -m wavequant.cli` → `python -m wavequant.interfaces.cli`

本仓库是这些模块的唯一受控消费者，因此本次执行一次性破坏性升级：先迁移普通导入、延迟导入、测试 patch 字符串和 CLI，再删除过渡门面。`wavequant.__init__` 只重导出少量稳定类型和常用函数，不作为完整模块注册表。

## 关键领域不变量

1. `source_index` 表示图上的来源日期，`confirmed_index` / `available_at` 表示该事实何时可知；回测只能使用后者不晚于当前日期的数据。
2. 末跌高是指定低点左侧最近的已确认高点；末升低为镜像定义。二者不是窗口绝对高低点，也不被之后的中间反弹/回档改写。
3. 趋势关键位必须严格越过；相等只表示触碰，不表示突破。
4. 一级、二级、三级趋势分别在自己的已确认点序列中计算，禁止跨级拼接。
5. 展示层的补线、连续性桥接和未确认尾点不得进入策略判断。
6. 研究结果、执行证据和订单状态是不同事实；零成交不能被解释为策略通过。

## 新增代码检查清单

- 这是纯业务规则、用例编排、外部适配还是输入/展示接口？
- 领域模块是否导入了文件、SQLite、CLI、HTTP 或展示模块？若是，应反转依赖或上移编排。
- 输入是否区分来源时间与可知时间？是否存在未来数据泄漏？
- 公共函数是否说明契约、返回状态、异常、副作用和非显然边界？
- 是否复用了领域定义，而不是在报表或接口层复制一套近似判断？
- 是否添加最小行为测试和架构边界测试？
