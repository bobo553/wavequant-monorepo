# 页面旧构建误报 N 目标快照时钟

日期：2026-11-07。范围：MONOREPO-362，上证指数页面报错；仅修复本地前端运行产物。

## 原因与证据

用户提供的错误为连续多条 `N目标快照时钟无效`。本次重启恢复了 Rust v46 API，但复用了 `.codex-runtime/rust-production-web-a/wavequant-web/.next` 中较早的 Next 构建。

读取实际运行目录的 `.next/static/chunks/6079.51a7dfe694fe4a87.js` 可见：旧 bundle 对每个目标快照都要求 `squeezeIndex !== null` 且 `knownIndex >= squeezeIndex`。这会拒绝 Rust v45 起在 N 成立后、轧空／杀多确认前发布的一饱、二吐。

当前 `packages/contracts/src/modules/market/trend-n.schema.ts` 已实现正确边界：基础目标须晚于或等于 `attackIndex` 和 N 可知日，只有五顶／十满要求轧空／杀多确认；同时保留目标递增、行情范围、观测截止和达标时钟约束。`apps/webs/wavequant-web/tests/trend-state.test.mjs` 已有对应接受与拒绝用例，本轮不改断言或领域规则。

暂存目录的 `.runtime-contracts` 即使更新，已经打包到 `.next` 的契约仍不会随之变化。仅重启 `next start` 或仅重建 `public/app.js` 无法更新这份校验器。

## 修复范围

- 从当前仓库生成 `@repo/contracts` 运行产物，同步当前前端源码、公开资源及共享契约到原运行目录。
- 重建图表资源和完整 Next 页面包，再启动 3003；代理继续连接 8010 的 Rust API。
- 后端与量化计算继续使用 Rust；Python 代码保留，不修改、不启用旧 Python 后端或量化逻辑。既有行情 SDK 配置不在本次变更范围内。
- 保留数据路径、Redis 6380 和 `--no-watchlist-refresh`；不清空行情或计算缓存。

后续共享契约变更时，必须重建实际提供页面的 Next bundle，不能仅复制 `.runtime-contracts` 后复用旧 `.next`。

## 执行与待验证

必要启动脚本和日志位于 `.codex-runtime/restart-20261107/`。`pnpm --filter @repo/contracts build`、图表资源生成及 Next `build --webpack` 启动编译已完成；前端 PID 59132 在 3003 启动，继续连接原 Rust API PID 38884 / 8010。完整编译包含 Next 自带的 TypeScript 步骤，未额外运行独立检查。

本轮未运行测试、lint、独立类型检查、Git 检查、浏览器、股票计算或接口探针。用户刷新后的实际页面结果仍待确认，不将历史回归结果作为本轮证据；整体迁移继续为 `in-progress`。
