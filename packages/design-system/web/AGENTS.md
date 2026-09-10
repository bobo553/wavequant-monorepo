# Web 设计系统规则

本文件适用于 `packages/design-system/web`，继承根规则和 `docs/agent/frontend/rules.md`。

- 本包是全部浏览器 Web（包括 H5、官网、门户、业务应用和 Admin）UI 原语、设计令牌和通用交互能力的唯一事实来源，不依赖任何 `apps/*` workspace。
- shadcn/ui 组件只在本包生成、升级、封装和导出；应用不得复制一套 `components/ui`。
- 含接口、权限、路由、埋点或领域流程的组合组件留在对应应用。
- 目录和文件使用 kebab-case，组件符号使用 PascalCase；`index.ts` 只导出稳定公共 API。
- 保持 React Server Component 兼容、CSS Variables、CVA、`cn` 和 Radix 可访问性行为。
- 只有 Hook、浏览器 API 或事件状态确有需要时才添加 `"use client"`。
- 升级上游组件前比较本地改动；新增依赖时检查许可、体积、Server/Client 边界和 tree-shaking。

```bash
pnpm --filter @repo/design-system-web lint
pnpm --filter @repo/design-system-web typecheck
pnpm --filter @repo/design-system-web build
pnpm --filter h5 typecheck
pnpm --filter h5 test:unit
pnpm --filter h5 build
pnpm --filter admin typecheck
pnpm --filter admin test:unit
pnpm --filter admin build
```
