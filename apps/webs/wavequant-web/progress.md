# Progress

## Current State

- `MONOREPO-011` 已完成：本 workspace 是 WaveQuant 独立浏览器应用边界。

## Completed

- 静态页面、交互模块、第三方声明和浏览器测试已从工具 workspace 迁入本 workspace。
- Lightweight Charts 与 Playwright 依赖改由 `wavequant-web` 独立声明。
- 新增可复现静态构建脚本，输出自包含页面与 vendor 资源。

## Verification

- Web 语法、Prettier、37 项单元测试与自包含静态构建通过。
- API 跨 workspace 测试验证默认页面、Lightweight Charts 和第三方声明可被正确提供。
- 根 `pnpm harness:check` 与 `pnpm verify` 通过。

## Risks and Next Steps

- 页面仍使用同源 `/api/*`，独立域名部署时需要反向代理或显式 API Client 配置。
