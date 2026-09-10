# Progress

## Current State

Web 设计系统向 H5、浏览器业务应用和 Admin 提供通用 shadcn 风格 UI 原语与样式。

## Completed

- 新增就近 `AGENTS.md` 规则。
- 纳入 workspace 级进度与 Harness 管理。
- 为 PC Web 模板新增 Badge、Card、Input、Separator 和 Skeleton 原语。
- 规则明确覆盖全部 PC Web 消费者，Admin 业务组合组件继续留在应用内部。

## Verification

- `pnpm verify`：lint、类型检查、测试和包构建通过。
- `pnpm --filter @repo/design-system-web lint && pnpm --filter @repo/design-system-web typecheck && pnpm --filter @repo/design-system-web build`：新增原语后通过。
- Web 规则优化后，设计系统及 `h5`、`admin` 两个消费者的 lint、类型检查和生产构建通过。

## Risks and Next Steps

- 组件变更需至少验证 `h5`、`admin` 两个现有消费者和可访问性。
- 业务组件继续放入 app 的 feature 目录，避免设计系统承载业务语义。
