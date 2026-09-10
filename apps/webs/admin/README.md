# Admin

`admin` 是仓库内面向 B 端业务的 Next.js 16 管理后台模板，默认运行在 `http://localhost:3002`。

## Commands

```bash
pnpm --filter admin dev
pnpm --filter admin lint
pnpm --filter admin typecheck
pnpm --filter admin test:unit
pnpm --filter admin test:e2e
pnpm --filter admin build
```

## Structure

- `src/app`：路由、布局和页面级状态。
- `src/features`：按业务域组织的仪表盘与管理功能。
- `src/shared`：应用壳、导航配置和跨功能组件。
- `@repo/design-system-web`：共享 shadcn 风格 UI 原语。

实现参考了 `next-shadcn-dashboard-starter` 的信息架构与交互模式，并按本仓库规则重新设计。第三方许可见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。
