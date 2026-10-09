MONOREPO-305（2026-10-09）：WatchlistSettingsSchema.context新增n_target_trend_confirmation_enabled，沿用请求协议字符串true/false枚举，缺字段默认false。共享契约构建及Web消费者类型、保存/恢复、后台队列定向回归通过；任务Web依赖指向本任务契约产物，主检出依赖未修改。

MONOREPO-298（2026-10-08）：新增WatchlistSnapshotSchema、WatchlistSettingsSchema、WatchlistDocumentSchema与类型，统一自选股票、分类、顺序、修订、回测来源/参数及暂停字段。Web运行时复用Zod校验，服务端输入另有边界和数量校验；契约构建、Web与Nest API消费者类型通过，9项Web存储/契约与后台观察回归通过。无新依赖。

# Progress

## Current State

`@repo/contracts` 是跨应用数据结构的唯一事实来源。

## Completed

- 纳入 workspace 级进度与 Harness 管理。

## Verification

- `pnpm verify`：lint、类型检查、测试和包构建通过。

## Risks and Next Steps

- 契约变化需同步验证 API、Web 与 Mobile 消费者。

- MONOREPO-153：新增LimitUpStockSchema/LimitUpLadderSchema及推导类型，Web复用验证API响应的日期、来源、连板数和可空数值。包构建和消费者Web类型/构建、真实接口浏览器解析通过。
