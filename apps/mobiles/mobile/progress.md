# Progress

## Current State

Expo 应用已迁移到 `apps/mobiles/mobile`，workspace 名保持 `mobile`。

## Completed

- 更新 Metro workspaceRoot 和 NativeWind 设计系统扫描路径。
- 保留原有移动端源码、资源与测试。
- 对齐 React DOM 19.1 类型依赖，并将组件单测改为无渲染器的行为测试，移除已弃用的 `react-test-renderer`。

## Verification

- `pnpm verify`：通过移动端 lint、类型检查和单元测试。
- 移动端 11 条单元测试通过，且不再产生 React 渲染器弃用和 `act` 环境告警。

## Risks and Next Steps

- 原生构建仍需在 Android/iOS 工具链可用时验证。
