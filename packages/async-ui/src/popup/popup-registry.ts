import type { ComponentType } from "react";

import type {
    AsyncViewComponent,
    AsyncViewFallbackProps,
    AsyncViewLoadOptions,
    AsyncViewResource,
    PopupRuntimeControls,
    PreloadPolicy,
} from "../types";
import { defineAsyncView } from "../view/define-async-view";

type AsyncModule = Readonly<Record<string, unknown>>;

/** 通过动态 import 注册弹框时可声明的加载、资源和调度信息。 */
export interface PopupRegistration<Module extends AsyncModule = AsyncModule> extends AsyncViewLoadOptions {
    readonly loader: () => Promise<Module>;
    readonly id?: string;
    readonly chunkName?: string;
    readonly source?: string;
    readonly preload?: PreloadPolicy;
    readonly budget?: AsyncViewResource["budget"];
    readonly priority?: number;
    readonly exportName?: string;
    readonly resolve?: (module: Module) => ComponentType<object>;
    readonly fallback?: ComponentType<AsyncViewFallbackProps>;
}

/** 已经由 `defineAsyncView` 包装过的高级注册形式，适合复用共享异步视图。 */
export interface AdvancedPopupRegistration<Props extends object = object, Result = unknown> {
    readonly view: AsyncViewComponent<Props & PopupRuntimeControls<Result>>;
    readonly priority?: number;
}

type EntryLoader<Entry> = Entry extends { readonly loader: infer Loader }
    ? Loader
    : Entry extends (...args: never[]) => unknown
      ? Entry
      : never;
type LoadedModule<Entry> = EntryLoader<Entry> extends () => Promise<infer Module> ? Awaited<Module> : never;
type ModuleView<Module, Entry> = Entry extends { readonly resolve: (...args: never[]) => infer View }
    ? View
    : Entry extends { readonly exportName: infer Name extends string }
      ? Module extends Record<Name, infer View>
          ? View
          : never
      : Module extends { default: infer View }
        ? View
        : never;
type RuntimeProps<Entry> = Entry extends { readonly view: AsyncViewComponent<infer Props> }
    ? Props
    : ModuleView<LoadedModule<Entry>, Entry> extends ComponentType<infer Props>
      ? Props
      : never;

export type PopupResultOfEntry<Entry> = RuntimeProps<Entry> extends PopupRuntimeControls<infer Result> ? Result : never;

/** 从组件 props 中移除框架注入的 close，得到业务调用方真正需要传入的参数。 */
export type PopupPropsOfEntry<Entry> = RuntimeProps<Entry> extends object ? Omit<RuntimeProps<Entry>, "close"> : never;

/** 注册表归一化后的不可变定义，也是 PopupManager 唯一接受的运行时结构。 */
export interface PopupDefinition<Props extends object = object, Result = unknown> {
    readonly key: string;
    readonly priority: number;
    readonly resource: Readonly<AsyncViewResource>;
    readonly view: AsyncViewComponent<Props & PopupRuntimeControls<Result>>;
}

export type PopupRegistry<Input extends Record<string, unknown>> = Readonly<{
    [Key in keyof Input]: PopupDefinition<PopupPropsOfEntry<Input[Key]>, PopupResultOfEntry<Input[Key]>>;
}>;

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null;
}

function isAsyncView(value: unknown): value is AsyncViewComponent<object> {
    if (typeof value !== "function") {
        return false;
    }
    const candidate = value as Partial<AsyncViewComponent<object>>;
    return (
        typeof candidate.preload === "function" && typeof candidate.reset === "function" && isRecord(candidate.resource)
    );
}

/**
 * 把 loader 简写、完整 loader 配置或既有 AsyncView 统一为类型安全注册表。
 *
 * 函数只创建描述对象，不执行任何 loader；同时提前拒绝重复资源 id/chunkName，避免
 * 运行时缓存串线，或构建清单无法将 Chunk 唯一映射回业务资源。
 */
export function definePopupRegistry<const Input extends Record<string, unknown>>(input: Input): PopupRegistry<Input> {
    const output: Record<string, PopupDefinition> = {};
    const ids = new Set<string>();
    const chunkNames = new Set<string>();

    for (const [key, rawEntry] of Object.entries(input)) {
        const entry = isRecord(rawEntry) ? rawEntry : undefined;
        const advancedView = entry?.view;
        let view: AsyncViewComponent<object>;
        let priority: number;

        if (isAsyncView(advancedView)) {
            view = advancedView;
            priority = (entry?.priority as number | undefined) ?? 100;
        } else {
            const loader = typeof rawEntry === "function" ? rawEntry : entry?.loader;
            if (typeof loader !== "function") {
                throw new TypeError(`Popup registry entry "${key}" is missing a loader or AsyncView`);
            }
            const id = (entry?.id as string | undefined) ?? key;
            // 所有注册形式最终都收敛到 AsyncView，加载、重试和元数据语义只维护一份。
            view = defineAsyncView({
                id,
                chunkName: (entry?.chunkName as string | undefined) ?? id,
                source: (entry?.source as string | undefined) ?? key,
                preload: entry?.preload as PreloadPolicy | undefined,
                budget: entry?.budget as AsyncViewResource["budget"],
                loader: loader as () => Promise<AsyncModule>,
                exportName: entry?.exportName as string | undefined,
                resolve: entry?.resolve as ((module: AsyncModule) => ComponentType<object>) | undefined,
                fallback: entry?.fallback as ComponentType<AsyncViewFallbackProps> | undefined,
                timeoutMs: entry?.timeoutMs as number | undefined,
                retry: entry?.retry as number | undefined,
                shouldRetry: entry?.shouldRetry as AsyncViewLoadOptions["shouldRetry"],
            });
            priority = (entry?.priority as number | undefined) ?? 100;
        }

        if (!Number.isFinite(priority)) {
            throw new RangeError(`Popup registry entry "${key}" priority must be finite`);
        }
        if (ids.has(view.resource.id)) {
            throw new Error(`Duplicate async resource id: ${view.resource.id}`);
        }
        if (chunkNames.has(view.resource.chunkName)) {
            throw new Error(`Duplicate async chunk name: ${view.resource.chunkName}`);
        }
        ids.add(view.resource.id);
        chunkNames.add(view.resource.chunkName);

        output[key] = Object.freeze({
            key,
            priority,
            resource: view.resource,
            view,
        }) as unknown as PopupDefinition;
    }

    return Object.freeze(output) as PopupRegistry<Input>;
}

/** 兼容偏好 create 命名的调用方，与 definePopupRegistry 完全等价。 */
export const createPopupRegistry = definePopupRegistry;

/** 提取供预加载策略和 Webpack 构建治理复用的纯资源清单。 */
export function getPopupRegistryResources<Registry extends Readonly<Record<string, unknown>>>(
    registry: Registry,
): readonly Readonly<AsyncViewResource>[] {
    return Object.freeze(
        Object.values(registry).map((definition) => (definition as PopupDefinition<object, unknown>).resource),
    );
}

export type PopupRegistryKey<Registry> = Extract<keyof Registry, string>;

export type PopupProps<Registry, Key extends keyof Registry> =
    Registry[Key] extends PopupDefinition<infer Props, unknown> ? Props : never;

export type PopupResult<Registry, Key extends keyof Registry> =
    Registry[Key] extends PopupDefinition<infer _Props, infer Result> ? Result : never;
