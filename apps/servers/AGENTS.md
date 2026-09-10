# 服务端目录规则

本文件适用于 `apps/servers/*`，继承根 `AGENTS.md`。所有服务端任务同时读取 `docs/agent/backend/rules.md` 和 `docs/agent/backend/nestjs-rules.md`，再按任务加载专项规则。

## 目录职责

- API、网关、定时任务、消息消费者和后台作业放入 `apps/servers/<workspace>`。
- 当前 `apps/servers/api` 使用 NestJS、TypeScript、TypeORM、PostgreSQL、Zod 和 Vitest，workspace 名为 `api`。
- 用户界面进入 `apps/webs/*` 或 `apps/mobiles/*`，可独立发布的开发工具进入 `apps/tools/*`，跨应用库进入 `packages/*`。

## 实现规则

- 保持 `presentation → application → domain ← infrastructure` 依赖方向。
- 跨端输入输出先更新 `@repo/contracts`，环境变量先更新 `@repo/env` 与 `.env.example`。
- Controller 保持薄；业务规则进入 Domain，流程编排进入 Application，数据库和外部系统实现进入 Infrastructure。
- 数据库迁移、Webhook、队列和任务需要显式处理事务、幂等、重试和失败恢复。
- 日志与错误响应不得泄露凭据、个人信息、SQL 或内部堆栈。

## 验证要求

```bash
pnpm --filter api lint
pnpm --filter api typecheck
pnpm --filter api test:unit
pnpm --filter api test:e2e
pnpm --filter api build
```
