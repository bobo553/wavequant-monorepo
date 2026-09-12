import { type ComponentType, createElement, useEffect, useSyncExternalStore } from "react";

import { LoadController } from "../core/load-controller";
import { type AsyncViewComponent, type AsyncViewLoadOptions, type AsyncViewOptions, PreloadPolicy } from "../types";

function resolveModule<Props extends object, Module>(
    module: Module,
    options: AsyncViewOptions<Props, Module>,
): ComponentType<Props> {
    if (options.resolve) {
        return options.resolve(module);
    }

    const candidate = options.exportName
        ? (module as Record<string, unknown>)[options.exportName]
        : (module as { default?: unknown }).default;

    if (typeof candidate !== "function") {
        const exportLabel = options.exportName ?? "default";
        throw new TypeError(`Async view module export "${exportLabel}" is not a React component`);
    }

    return candidate as ComponentType<Props>;
}

type DefaultModuleProps<Module> = Module extends { default: ComponentType<infer Props extends object> }
    ? Props
    : object;

/**
 * 把动态模块定义成可渲染、可预加载、可诊断的 React 组件。
 *
 * 定义阶段不会执行 loader。服务端快照始终从 idle 开始，真正加载由客户端 effect 或
 * 显式 preload 触发，因此 SSR 不会意外执行浏览器专用模块，也不会产生水合状态偏差。
 */
export function defineAsyncView<Module, Props extends object = DefaultModuleProps<Module>>(
    options: AsyncViewOptions<Props, Module>,
): AsyncViewComponent<Props> {
    const id = options.resource?.id ?? options.id;
    const source = options.resource?.source ?? options.source;
    if (!id?.trim() || !source?.trim()) {
        throw new TypeError("Async view id and source are required");
    }
    const resource = Object.freeze({
        id,
        chunkName: options.resource?.chunkName ?? options.chunkName ?? id,
        source,
        preload: options.resource?.preload ?? options.preload ?? PreloadPolicy.ON_DEMAND,
        budget:
            options.resource?.budget || options.budget
                ? Object.freeze({ ...(options.resource?.budget ?? options.budget) })
                : undefined,
    });
    const controller = new LoadController(async () => resolveModule(await options.loader(), options), options);

    const AsyncView = ((props: Props) => {
        // getServerSnapshot 与客户端初始 idle 快照一致，保证 SSR/hydration 输出稳定。
        const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);

        useEffect(() => {
            if (snapshot.status === "idle") {
                void controller.load().catch(() => undefined);
            }
        }, [snapshot.status]);

        if (snapshot.status === "ready") {
            return createElement(snapshot.value, props);
        }

        if (!options.fallback) {
            return null;
        }

        const retry = async (): Promise<void> => {
            // 失败状态会被控制器缓存，用户重试必须显式 reset 后再发起新一轮加载。
            if (controller.getStatus() === "failed") {
                controller.reset();
                await controller.load(options).then(() => undefined);
            }
        };

        return createElement(options.fallback, {
            status: snapshot.status === "failed" ? "failed" : "loading",
            error: snapshot.status === "failed" ? snapshot.error : undefined,
            retry,
        });
    }) as unknown as AsyncViewComponent<Props>;

    // 使用不可写静态属性，确保资源身份不会在运行期间被业务代码篡改。
    Object.defineProperties(AsyncView, {
        id: { value: resource.id, enumerable: true },
        resource: { value: resource, enumerable: true },
        preload: {
            value: (loadOptions?: AsyncViewLoadOptions) => controller.load(loadOptions ?? options),
            enumerable: true,
        },
        reset: { value: () => controller.reset(), enumerable: true },
        getStatus: { value: () => controller.getStatus(), enumerable: true },
        getSnapshot: { value: () => controller.getSnapshot(), enumerable: true },
    });
    AsyncView.displayName = `AsyncView(${resource.id})`;

    return AsyncView;
}
