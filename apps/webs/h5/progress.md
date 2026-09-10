# Progress

## Current State

Next.js 应用已重命名为 `apps/webs/h5`，workspace 名为 `h5`，面向用户活动页、分享页和移动浏览器业务页面。

## Completed

- 更新 Docker、文档和 workspace 路径。
- 保留原有 Web 源码与测试。
- 与 Admin 共用 Web 设计系统，但使用移动优先的信息架构和交互，不套用后台 App Shell。
- 目录、workspace 名、Docker、脚手架和文档已从 `web` 同步重命名为 `h5`。

## Verification

- `pnpm verify`：通过 H5 lint、类型检查、单元测试和 Next.js 生产构建。
- Web 规则优化后，H5 lint、类型检查、4 个单元测试和生产构建通过。
- 重命名后 H5 与 Admin 的相关门禁、CLI dry-run 及全仓 `pnpm verify` 通过。

## Risks and Next Steps

- 实现具体 PC 页面时需按 workspace 声明的视口矩阵在真实浏览器验证布局、键盘、Console 与 Network。
