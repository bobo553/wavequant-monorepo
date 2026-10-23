# Rust 后端与量化工程

Owner：WaveQuant 项目维护者。适用：MONOREPO-361 工程初始化。
验证状态：2026-10-23 提交阶段格式、Clippy全部目标编译与3项领域测试通过；HTTP运行/停机和release构建未验证，详见各工程 progress.md。

## 决策

沿用仓库按运行职责分类的目录规则，以 Cargo workspace 管理 Rust 包：

```text
Cargo.toml                  # workspace、依赖、lint 与发布构建参数
Cargo.lock                  # 两个应用统一的依赖锁
rust-toolchain.toml         # 固定 Rust 1.98.1
apps/servers/
  wavequant-api-rs/          # Axum + Tokio HTTP 进程
    src/bootstrap/
    src/presentation/
  wavequant-quant-rs/        # 独立量化批处理进程与内部 library
    src/modules/market_data/
      domain.rs
      application.rs
    src/infrastructure/
    src/presentation/
    examples/
    tests/
```

API 处理协议与进程生命周期，量化进程隔离未来 CPU 密集计算。
当前两者不互相引用，也没有网络任务协议；仅建立独立演进的应用入口。
不提前创建数据库、队列或多个微服务。pnpm/Turbo 继续管理 Node 工作区，
Cargo 直接管理 Rust，无伪 package.json、Node 锁文件或重复构建缓存。

采用 edition 2024 / resolver 3，统一禁止 unsafe，拒绝生产代码 unwrap/expect；
固定编译器版本并提交 Cargo.lock，部署构建使用 --locked。
Axum/Tokio 承担 HTTP 与异步 IO，Serde 仅用于量化文件输入适配层。
这些依赖采用 MIT/Apache-2.0 双许可；正式引入生产前仍需按锁定依赖进行供应链复核。
没有新增业务 API、外部服务凭据或环境变量。

## 分层与后续扩展

领域能力按模块组织，依赖保持 presentation → application → domain ← infrastructure。
模块边界优先使用普通 Rust 模块；稳定复用与独立编译需求出现后才拆 crate。
不为每一层创建空 crate，也不把尚无消费者的业务骨架提取为共享包。

后续依次接入：行情来源及身份元数据、统一趋势核心、策略/风控、因果回测与账本、
任务协议与 API 消费。每一步先登记 Feature，再对齐现有 Python 行为与差分验收。
当前行情 inspect 只是本地输入检查，不承诺与现有 Python 行情格式兼容。

## 运行与恢复边界

当前限本机开发，无生产 SLO 或容量承诺。API 默认回环 8010，无外部依赖；
端口冲突启动失败，停机最多等待 10 秒。量化 CLI 限单个 16 MiB 文件，无后台重试，
失败非零退出；没有持久化任务，修正输入后可显式重新运行。
无自动生产发布、数据库迁移或旧服务流量切换。撤回新进程即可停止本次新增入口。

根 Harness 的源码增加 Cargo.toml 工程发现，仍保持禁用，不执行。
两个工程各自维护 progress.md。未修改现有 CI 行为。

## 官方参考

- [Cargo workspaces](https://doc.rust-lang.org/cargo/reference/workspaces.html)：共享依赖、锁文件与 workspace 配置。
- [Cargo package layout](https://doc.rust-lang.org/cargo/guide/project-layout.html)：library、binary、tests、examples。
- [Axum](https://docs.rs/axum/0.8.9/axum/)：HTTP 路由与 Tokio 集成。
- [Tokio](https://docs.rs/tokio/1.54.1/tokio/)：异步运行时；CPU 密集任务需与 IO 调度分离。
