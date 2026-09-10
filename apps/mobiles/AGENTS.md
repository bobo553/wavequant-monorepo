# 移动端目录规则

本文件适用于 `apps/mobiles/*`，继承根 `AGENTS.md` 和 `docs/agent/frontend/rules.md`。

## 目录职责

- 移动 App、小程序、移动 H5 和移动端页面放入 `apps/mobiles/<workspace>`。
- 当前 `apps/mobiles/mobile` 使用 Expo、React Native、Expo Router、NativeWind 和 Vitest，workspace 名为 `mobile`。
- PC Web 进入 `apps/webs/*`，服务端进入 `apps/servers/*`，工具进入 `apps/tools/*`。

## 实现规则

- 路由和页面放在 `src/app`，领域功能放在 `src/features`，跨功能能力放在 `src/shared`。
- UI 原语复用 `@repo/design-system-mobile`，接口字段复用 `@repo/contracts`。
- 样式优先使用 NativeWind 与设计令牌；导航、权限、推送和平台 API 留在应用层。
- 页面覆盖加载、空数据、错误、无权限和重复提交，并验证安全区、键盘、弱网和平台差异。

## 验证要求

```bash
pnpm --filter mobile lint
pnpm --filter mobile typecheck
pnpm --filter mobile test:unit
```

原生依赖、Metro 或 Expo 配置变化时，至少验证受影响平台的启动或打包。
