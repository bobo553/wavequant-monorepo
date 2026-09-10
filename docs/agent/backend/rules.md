# 后端工程规范

本文件是 `apps/servers/*` 的后端规则入口。当前生产服务端基线为 NestJS、TypeScript、TypeORM、PostgreSQL、Zod 与 Vitest。所有服务端任务先读取“基础规范”，再按任务只加载必要专项。

## 读取路由

| 任务类型                                          | 额外读取文件                                            |
| ------------------------------------------------- | ------------------------------------------------------- |
| 任意 NestJS API、Worker 或后台任务                | 本文件“基础规范”与 `docs/agent/backend/nestjs-rules.md` |
| 新增/拆分服务、模块边界、分层或端口/适配器        | `docs/agent/backend/architecture-rules.md`              |
| HTTP、OpenAPI、DTO、错误、分页、版本、序列化      | `docs/agent/backend/api-rules.md`                       |
| PostgreSQL、迁移、事务、Redis、搜索、文件存储     | `docs/agent/backend/data-rules.md`                      |
| 异步任务、消息、Outbox、限流、背压                | `docs/agent/backend/reliability-rules.md`               |
| 登录、权限、Token、OAuth、Webhook、秘密或敏感输入 | `docs/agent/backend/security-rules.md`                  |
| 容量、技术选型、分布式一致性或高可用设计          | `docs/agent/architecture/rules.md`                      |
| 埋点事件、分析模型、ETL/CDC 或数据实验            | `docs/agent/data-warehouse/rules.md`                    |
| 探针、可观测、容器、发布或 IaC                    | `docs/agent/operations/rules.md`                        |

跨领域任务只组合真实涉及的文件。例如外部 Webhook 需要 API、数据、可靠性和安全规则；普通领域用例不加载全部后端专项。发生冲突时，根 `AGENTS.md`、最近的目录级 `AGENTS.md` 和更严格的专项规则优先。

## 基础规范

### 任务准备与影响面

- 修改前确认目标 workspace、入口、Module、配置、README、依赖与现有测试；命令从仓库根目录执行，并通过 `pnpm --filter <workspace>` 限定范围。
- 沿 Controller、application、domain、出站适配器和下游依赖梳理真实调用链、错误语义与测试覆盖，优先使用 `rg` 检索现有实现。
- 识别 OpenAPI、数据库迁移、ORM Metadata、模板或其他生成物；只修改权威定义并使用仓库固定命令重建。
- 公共 API、消息、数据库 Schema、配置格式和错误语义默认是兼容契约；变更前列出生产者、消费者、部署顺序与回滚影响。
- 只处理当前功能所需模块，不混入工具链升级、全仓重构或无关依赖整理。

### 服务内模块边界

- 依赖方向保持 `presentation → application → domain ← infrastructure`；domain 不依赖 NestJS、TypeORM、HTTP、数据库或第三方 SDK。
- 按业务能力组织 `src/modules/<module>`；模块通过公开用例、端口或版本化事件协作，禁止跨模块深层导入和绕过用例直接写数据。
- `presentation` 负责协议解析、认证入口、输入校验、DTO 映射与状态码；不承载业务规则、SQL 或事务编排。
- `application` 负责用例、权限策略协调、事务边界与端口调用；不返回 ORM Entity 或泄漏 HTTP 细节。
- `domain` 保存实体、值对象、领域服务、不变量和端口；`infrastructure` 实现 Repository、Provider、ORM 映射和外部适配器。
- DTO、领域对象、持久化模型和 Provider 模型分别定义并在边界显式映射；简单 CRUD 不机械增加空壳层，复杂业务不把不变量塞进 Controller。

### 配置、错误与代码卫生

- 配置由 `@repo/env` 或服务内强类型 Schema 一次校验；新增变量同步 `.env.example`、部署配置和测试，服务端秘密不得进入客户端前缀。
- 业务错误使用稳定领域错误或错误码，由全局异常过滤器映射为安全响应；基础设施异常和堆栈不得直接返回客户端。
- 同一错误只在承担处理决策的边界记录一次；日志包含 request/trace ID 和稳定字段，但不得记录 Token、Cookie、个人信息或完整请求体。
- 时间、随机数、ID、外部 API 和存储通过可替换边界注入，使测试确定且不依赖真实基础设施。
- 临时日志、调试端点、注释掉的代码、无责任信息的 TODO 与无范围的类型/规则绕过不得进入主分支。

### 数据、事务与外部副作用

- application 用例拥有事务边界并保持短小；事务中不等待用户、调用外部网络或执行无上限批处理。
- 数据库约束守住最终不变量；并发写入显式选择唯一约束、原子条件更新、乐观锁或行锁。
- 数据库提交与消息/外部调用不得做脆弱双写；需要可靠通知时使用 Outbox、幂等消费者和对账。
- 外部 Client/Adapter 统一管理 base URL、认证、超时、取消、响应大小、错误映射和遥测；未知结果的副作用操作先查询状态。

### 基础测试与验证

- domain 与 application 用快速单元测试覆盖成功、拒绝、边界、重复请求和依赖失败。
- Controller 映射、认证、校验、事务、Repository 与序列化使用集成或 E2E 测试；数据库语义不能只靠 Mock 证明。
- 缺陷修复先建立可失败的回归用例；竞态、重复投递、超时和迁移必须覆盖相应失败模式。
- 最小门禁为 `pnpm --filter api lint`、`typecheck`、`test:unit` 和 `build`；真实数据库边界追加 `test:e2e`。
