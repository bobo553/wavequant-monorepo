# WaveQuant Rust API

本地 Rust 后端骨架，使用 Axum + Tokio。Owner：WaveQuant 项目维护者。
开发状态及待验证项见 progress.md；架构与目录规则见 ../../../../docs/rust-backend-quant.md。

## 目录

- src/main.rs：配置、日志、监听与进程生命周期。
- src/bootstrap/：强类型启动参数与系统信号。
- src/presentation/：HTTP 路由与协议映射。
- 后续业务能力进入 src/modules/<业务名>/{presentation,application,domain,infrastructure}，有实际实现再增加模块。

## 启动

在仓库根目录执行：

```powershell
cargo run --locked -p wavequant-api-rs -- --listen 127.0.0.1:8010
```

默认 127.0.0.1:8010，只允许回环 IP 和非零端口。参数错误或端口占用使进程启动失败。
GET /health/live 返回 204，无响应体，无认证，只表示本地 HTTP 进程存活。
没有量化任务、数据库或缓存就绪承诺。未知路径返回框架默认 404。
结构化日志写入标准输出；Ctrl+C（Unix 另支持 SIGTERM）停止接收并最多等待 10 秒。

当前没有业务请求和前端消费者；增加业务 API 时以 @repo/contracts 为契约事实来源，
明确 Rust 映射或生成流程后再接入。配置使用 CLI 参数，没有新增环境变量。
新增环境变量时必须先扩展 @repo/env 与 .env.example。

## 验证记录

cargo check --locked --workspace、cargo clippy --locked --workspace --all-targets -- -D warnings、
cargo test --locked --workspace、cargo fmt --all -- --check，按实际提交风险执行。
2026-10-23 已用同版本 +stable 完成格式、Clippy编译和workspace测试；HTTP运行及停机未验证，详情见 progress.md。
