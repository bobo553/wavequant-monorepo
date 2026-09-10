# NestJS 与 TypeScript 专项规范

本文件适用于 `apps/servers/*` 中所有 NestJS、Node.js 与 TypeScript 服务端代码，继承 `docs/agent/backend/rules.md`。涉及 HTTP、数据、安全、异步处理或服务拆分时，再组合对应专项规则。

## Workspace 与修改准备

- 先读取目标服务 `package.json`、`nest-cli.json`、TypeScript 配置、最近的 `AGENTS.md`、配置入口和测试配置，确认脚本与模块边界。
- 修改前检索 Provider Token、Module imports/exports、调用方、接口实现、异常映射和相邻测试；不要凭目录名推测依赖注入关系。
- 只有当前 workspace 依赖确实变化时才修改其 `package.json`，内部包统一使用 `workspace:*`，同步审阅 `pnpm-lock.yaml`。
- 生成文件只由权威 Schema 或生成器更新；不得手改生成代码掩盖版本或配置问题。

## 类型与运行时边界

- TypeScript 保持 strict；公共用例、端口、DTO 映射和复杂返回值显式声明类型，局部简单值使用可靠推断。
- 外部输入、JSON、环境变量、数据库原始结果和第三方响应先以 `unknown` 接收，再用 Zod、DTO Validation 或类型守卫校验。
- 禁止使用 `any`、`@ts-ignore`、非空断言和强制类型断言掩盖错误；第三方兼容例外必须局部隔离并解释原因。
- `null`、`undefined`、缺省字段与空集合的语义保持明确；不得用可选链或默认值隐藏必填数据缺失。
- 跨 Web、Mobile、API 的结构优先来自 `@repo/contracts`；Nest DTO 只承担协议适配和 Swagger/Validation 所需职责。

## NestJS Module 与依赖注入

- 每个业务模块拥有明确公开边界；Module 只装配依赖，不承载业务规则、环境分支或运行时副作用。
- Controller 保持薄，只负责协议和用例调用；Service 名称表达 application 用例或基础设施职责，避免巨型通用 Service。
- Provider 使用构造函数注入；Token 使用稳定 Symbol、类或集中常量，禁止散落魔法字符串和 Service Locator。
- Module 只导出真实消费者所需 Provider；避免 `forwardRef`，出现循环依赖时优先重划所有权、提取稳定端口或事件。
- 请求作用域 Provider 会放大实例和上下文成本，只有确有隔离需求并经过测量时使用；默认使用单例且不得保存请求级可变状态。
- 启动副作用放在明确生命周期 Hook，并保证重复启动、失败回滚和优雅关闭；不要在模块顶层执行网络或数据库操作。

## 异步、RxJS 与资源生命周期

- 所有 Promise 必须 `await`、返回或显式管理；禁止 floating promise、空 `catch` 和用 fire-and-forget 承担可靠业务副作用。
- 并行任务使用有界并发并保留输入顺序/失败语义；不要对无界输入直接 `Promise.all`。
- `AbortSignal`、请求关闭事件或等效机制向下游传播取消；定时器、订阅、流和连接必须有所有者和清理路径。
- 仅在流式、多事件或取消组合确有收益时使用 RxJS；Observable 在边界转换清楚，订阅必须回收，不能混用两套错误语义。
- CPU 密集工作、图片处理和大批计算不得阻塞事件循环；根据容量证据拆分到 Worker Thread、独立进程或有界任务队列。

## 错误、日志与进程边界

- 预期业务拒绝使用稳定错误类型/错误码；异常过滤器统一映射 HTTP，不能在各 Controller 重复拼响应。
- 捕获错误必须处理、补充上下文或重新抛出并保留 `cause`；禁止依赖错误字符串分类。
- `process.exit`、未捕获异常处理和启动失败只在最外层进程边界决定；domain、application 和 Provider 返回/抛出可分类错误。
- 结构化日志在入口、任务消费者或其他责任边界记录一次，携带 trace/request ID；不打印完整配置、Payload、SQL 参数或凭据。
- 优雅停机启用 Nest shutdown hooks，按顺序停止接流量、停止拉取任务、等待有界在途工作、关闭数据库和其他连接。

## 测试与性能

- 纯业务逻辑直接实例化测试；只有验证 Nest 装配、Decorator、Guard、Pipe、Interceptor 或 Filter 时才使用 TestingModule。
- Provider Fake 围绕端口行为构建；数据库约束、TypeORM 映射、事务和 SQL 必须用真实测试数据库或集成环境验证。
- 测试不得依赖执行顺序、真实时间、真实网络或共享可变全局；时间、随机和外部端口可注入并显式清理。
- 性能优化先用 event-loop delay、CPU profile、heap、数据库指标或可复现基准定位；不凭直觉增加缓存、并发或对象池。
- 先运行定向测试，再运行 `pnpm --filter api lint`、`typecheck`、`test:unit` 和 `build`；数据库或协议边界变化追加 `test:e2e`。
