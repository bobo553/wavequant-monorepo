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
