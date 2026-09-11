# 离线运行系统 v0.4：运行、告警、恢复与账务契约

## 范围

本版把已有理论、历史诊断、纸面执行与新增运行控制接成一个命令。它是单机、单写、显式调用的离线系统，不是交易服务器。没有启用真实券商订单、后台定时任务、邮件、Webhook 或云端部署。

```powershell
.venv\Scripts\python.exe -m wavequant.cli system-run --config configs/operations.json --run-id acceptance_20260908
.venv\Scripts\python.exe -m wavequant.cli system-status --root results/operations_v1
```

流程：冻结配置、源码和输入 → 全量自动测试 → 数据审计 → 原规则严格版/日线代理版分别做滚动和成本/容量诊断 → 合成纸面故障验收 → 账户归因守恒 → 输入及账户备份与恢复 → 状态门禁报告 → 产物哈希封存。

CLI 没有跳过测试的开关。测试里注入的小型检查器仅用于流水线故障夹具，不作为工程验收证据。`run_research: false` 可以省略历史诊断，但报告标为 NOT_RUN，不能伪装成已回测。

## 输入配置与数据更新

配置路径相对于配置文件所在目录解析。拒绝未知键、模式为 live、输入位于输出根目录内，以及未声明日期与股票名单的 TDX 更新。配置中不接受密钥和任意命令。

`configs/operations.json` 默认使用原十股快照及冻结 v4 协议，不覆盖原始数据、旧实验或 v5 研究报告。

需要更新通达信时使用独立配置：`refresh_tdx: true`、`tdx_root`、完整固定 `symbols`、`start`、明确 `end`。流水线先冻结各股 .day 和 gbbq，再从冻结原始数据导入，运行结束前核对原输入未变化。不会给 TDX 安装目录写文件。每次更新生成新快照，不在旧快照上直接追加；除权数据变化时完整重建更易审计。

`run_research: true` 时，冻结研究协议的 data_end 必须与行情一致；不能自动把未来日期塞进已看过结果的旧协议。单纯最新数据审计可设置 false，新的前瞻研究须另行冻结协议。

可选 `security_master` 为 `SecurityMaster` 的事实数组，`calendar` 为唯一、递增的 ISO 交易日数组，格式见 platform_contract。代码核验输入结构与覆盖，不自动鉴定外部来源真伪。

无日历时，新鲜度为 UNKNOWN，不用工作日推测节假日。日历须覆盖当前日期或更晚交易日；15:00 上海时间后该交易日才被视为完整日线。落后股票列出，不猜测停牌原因。缺少历史证券资料不阻止离线诊断，但阻止声称数据实盘就绪；未来数据、来源哈希冲突和已知上市区间之外的数据中止运行。

## 防重复与中断恢复

Windows 字节锁／POSIX flock 在整个运行期间持有，进程退出由操作系统释放。残留 `.operations.lock` 文件不是“任务仍在运行”的证据，不需要删除。

运行 ID 不允许路径分隔符。成功 ID 重复调用仅校验既有 artifacts.json 与产物并返回原报告，不重新下单或生成一个新收益结果；配置不一致拒绝。失败或中断 ID 不复用，目录保留，新尝试用新 ID。

新任务取得排他锁后，将没有终态的旧任务标为 INTERRUPTED。不会猜测某一步已完成后从中间自动继续；当前离线步骤从新目录重新执行。未来券商网络步骤必须使用券商可查询的请求 ID 和真实状态恢复，不能直接照搬这种重跑方式。

源代码、测试、依赖声明和输入在运行中变化会导致最终封存失败。依赖版本声明与环境指纹不是完整可执行虚拟机镜像；复现实验仍需相容 Python 和依赖。

## 本地健康告警

`operations.sqlite` 的 ALERT_OPENED / ALERT_ACKNOWLEDGED / ALERT_RESOLVED 是持久收件箱。相同作用域与同一问题不重复开告警；问题恢复后再出现会产生新的告警。确认不代表健康恢复。

```powershell
.venv\Scripts\python.exe -m wavequant.cli system-alert-ack --root results/operations_v1 --id <告警ID> --reason "已阅读，等待外部证券状态数据"
```

失败事件只保存异常类型与完成阶段，不把潜在含密钥的异常正文自动发往通知系统。详细错误保留在命令输出；无网络通知传输。状态检查会验证成功运行的产物哈希，并列出失败、未结束任务和告警。它是检查命令，不是后台心跳服务。

## 备份与恢复

```powershell
.venv\Scripts\python.exe -m wavequant.cli system-backup --root results/operations_v1 --destination results/operations_backup_20260908
.venv\Scripts\python.exe -m wavequant.cli system-restore --bundle results/operations_backup_20260908 --destination results/operations_restored_20260908
```

要求目标不存在、与源目录不重叠。链接、目录逃逸、未登记文件、哈希不符均拒绝。SQLite 使用 backup API 包含已提交 WAL 事务，不直接复制活跃主库。不删除或覆盖文件；失败备份目录也保留以供调查，下一次使用新路径。

已检查点化、没有 WAL 帧且源文件未变化的封存数据库，在 API 完整性检查后保留原始文件字节，避免 SQLite 重排页头破坏既有产物指纹；带 WAL 帧的活动库仍采用 API 快照。整套恢复验收同时检查账本事件链和各历史运行的原封存清单，不能仅以备份命令返回成功判定恢复通过。

多数据库一致性依赖所有相关写入者停下；operations 命令共同遵守同一锁，无法拦住外部脚本直接修改独立 SQLite 库。哈希用于发现损坏，不抵抗攻击者同时重写文件与清单。没有实现异地备份、文件系统权限管理或硬件密钥。

## 订单和账户增量

- PaperBridge 投递撤单请求；成交先于撤单完成时，不伪造撤单成功。
- 场所拒单可关闭剩余数量，已成交股份和费用继续留账；重复回报不重复记账。
- PaperVenue 和 OMS 分别接受有明确 known_at、来源、股份比例与净现金权益的公司行动，再做独立对账；未知配股支付、碎股和股息税仍不能推算。
- 活动委托未处理前不做公司行动；虚拟高低不是券商实际成本。
- `account-report` 按指定时刻重建 FIFO 成本、现金、冻结资金、费用、公司行动现金、分股已实现/未实现盈亏，并核对 NAV 守恒。买入费用进入成本，不能再次从归因中重复扣除。股份调整保留成本。

```powershell
.venv\Scripts\python.exe -m wavequant.cli account-report results/operations_v1/runs/acceptance_20260908/paper/account.sqlite --asof 2026-01-13T09:30:00+08:00 --output results/account_example.json
```

该例使用合成纸面账户，不是市场收益。缺少或过期持仓价格时净值与总盈亏返回 null；已知现金和历史费用仍可报告。本报告不是税务账单，不支持外部入出金、融资融券或多币种。

## 未包含的功能／外部条件

本版不承诺“所有可能的量化功能均已实现”。仍未具备：真实券商适配和权限、历史全市场 PIT 证券资料、实际次级路径、独立事件金标准、未污染前瞻样本、真实策略长期模拟运行、真实成交标定、后台监控与外部通知、Web 多用户权限、异地灾备、多币种与高频扩展。

这些项目在状态报告中保持 BLOCKED / NOT_ESTABLISHED / DISABLED / NOT_CONFIGURED；不能凭接口存在、测试通过或某次回测为正自动开启实盘。
