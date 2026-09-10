# Progress

## Current State

共享 Vitest 配置覆盖基础、Next.js、NestJS 与 Expo。

## Completed

- 纳入 workspace 级进度与 Harness 管理。
- 使用 Vite 8 原生 `resolve.tsconfigPaths`，移除冗余路径插件。
- NestJS 测试显式关闭 Oxc，避免与 SWC 转换器冲突。

## Verification

- `pnpm verify`：所有已配置单元测试的 workspace 均通过。
- 全量单测运行不再出现路径插件或 Oxc 配置弃用告警。

## Risks and Next Steps

- 修改运行环境时需验证对应消费者测试。
