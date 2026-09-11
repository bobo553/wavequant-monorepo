# WaveQuant Web

WaveQuant 的只读浏览器数据工作台。页面、交互与 Lightweight Charts 依赖由本 workspace 独立管理，通过同源 `/api/*` 契约访问 `wavequant-api`。

## 命令

```powershell
pnpm --filter wavequant-web lint
pnpm --filter wavequant-web typecheck
pnpm --filter wavequant-web test:unit
pnpm --filter wavequant-web build
```

完整本地工作台由 API workspace 启动：

```powershell
pnpm --filter wavequant-api dashboard
```

Web 构建输出位于 `dist/`，不提交仓库。
