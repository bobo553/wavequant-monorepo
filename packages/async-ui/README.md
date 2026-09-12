# @repo/async-ui

面向 Web React 应用的异步视图与弹框调度基础包。它只负责资源加载、请求调度、渲染宿主和关闭结果，不替代业务
`Dialog`、`Drawer` 或 Error Boundary。

## 能力边界

- 注册阶段只保存动态 `import()` loader，不下载低频组件。
- Registry key、业务 Props 与 `close(result)` 结果保持端到端类型推导。
- `popup()` 使用数字越小越优先的稳定串行队列；`open()` 显式绕过队列并允许叠加。
- 默认单次加载超时 8 秒、失败后重试 1 次；`failed` 是终态，只有 `resetLoad()` 才能重新请求。
- 取消和 Host 最终卸载都让业务 Promise 返回 `undefined`，内部冻结哨兵不会创建 `Error` 堆栈。
- `@repo/async-ui/webpack` 是独立 Node 构建入口，不进入浏览器运行时包。

已经发出的动态 `import()` 不会被中止；预加载策略只记录元数据，不会自行监听启动、接口响应或用户意图；组件
渲染后的业务异常仍应由应用自己的 Error Boundary 处理。

## 注册与使用

业务弹框只声明业务字段和关闭结果，`close` 由 `PopupHost` 最后注入：

```tsx
import type { PopupViewProps } from "@repo/async-ui";

interface CouponPopupProps {
    couponId: string;
}

type CouponResult = "used" | "cancelled";

export default function CouponPopup({ couponId, close }: PopupViewProps<CouponPopupProps, CouponResult>) {
    return (
        <div>
            <span>{couponId}</span>
            <button onClick={() => close("used")}>使用</button>
            <button onClick={() => close("cancelled")}>取消</button>
        </div>
    );
}
```

```tsx
"use client";

import { PopupHost, PreloadPolicy, createPopupManager, definePopupRegistry } from "@repo/async-ui";

const registry = definePopupRegistry({
    CouponPopup: {
        loader: () =>
            import(
                /* webpackChunkName: "CouponPopup" */
                "./CouponPopup"
            ),
        source: "src/popups/CouponPopup.tsx",
        priority: 20,
        preload: PreloadPolicy.USER_INTENT,
        budget: { gzipBytes: 24_000 },
    },
});

export const popupManager = createPopupManager(registry, {
    timeoutMs: 8_000,
    retry: 1,
    failureMode: "resolve-undefined",
    hooks: {
        onVisible: (context) => console.info("popup visible", context.key),
    },
});

export function PopupRoot() {
    return <PopupHost manager={popupManager} />;
}

export async function openCoupon() {
    const result = await popupManager.popup("CouponPopup", {
        couponId: "demo-coupon",
    });
    return result; // "used" | "cancelled" | undefined
}
```

没有业务入参的组件使用 `PopupViewProps<NoPopupProps, Result>`，调用时可直接
`popupManager.popup("NoticePopup")`。若要从外部取消加载中的请求，应传入稳定 `instanceId`，再调用
`cancel(instanceId)`；取消结果是 `undefined`。

`preload(key)`、`preloadByPolicy(policy)` 和 `resetLoad(key)` 由业务时机显式调用。`timeoutMs <= 0` 表示禁用
单次加载超时。命名导出或共享异步视图可分别使用 `exportName`、`resolve` 或高级 `{ view, priority }` 注册。

## Webpack 构建治理

```ts
import { getPopupRegistryResources } from "@repo/async-ui";
import { withAsyncUIWebpack } from "@repo/async-ui/webpack";

export default {
    webpack(config) {
        return withAsyncUIWebpack(config, {
            mode: "report",
            resources: getPopupRegistryResources(registry),
        });
    },
};
```

插件生成 `async-ui-manifest.json`，检查资源 ID 与 chunk 重名、asset 缺失、异步资源进入 initial graph、源码
静态穿透和 gzip 预算。`report` 写入 warning，基线稳定后可切换为 `enforce` 写入 error。

`webpackChunkName` 是编译期语法，运行时包无法自动生成。需要稳定 chunk 名、清单或预算治理时，它必须与资源的
`chunkName` 一致。Next.js 的 Turbopack 构建不会运行这个 Webpack 插件，应在明确使用 Webpack 的生产构建门禁中
校验。

## 验证

```bash
pnpm --filter @repo/async-ui lint
pnpm --filter @repo/async-ui typecheck
pnpm --filter @repo/async-ui test:unit
pnpm --filter @repo/async-ui build
```
