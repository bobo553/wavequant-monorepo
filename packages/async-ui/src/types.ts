import type { ComponentType } from "react";

declare const noPopupProps: unique symbol;

/**
 * 表示弹框没有业务入参。
 *
 * 该品牌类型使 `popup(key)` 可以省略 props，同时不会把任意空对象都误判为无参弹框。
 */
export type NoPopupProps = {
    readonly [noPopupProps]?: never;
};

export interface PopupRuntimeControls<Result> {
    /** 关闭当前实例，并把结果传回 `popup/open` 返回的 Promise。 */
    close(result?: Result): void;
}

export type PopupViewProps<Props extends object, Result> = Props & PopupRuntimeControls<Result>;

/**
 * 资源预加载时机。值是稳定的清单协议字段，名称则表达业务触发场景。
 * 构建报告和运行时应复用这里的值，避免各应用自行定义不兼容枚举。
 */
export const PreloadPolicy = Object.freeze({
    APP_START: "bootstrap",
    AFTER_SERVER_RESPONSE: "server-driven",
    USER_INTENT: "intent",
    ON_DEMAND: "rare",
} as const);

export type PreloadPolicy = (typeof PreloadPolicy)[keyof typeof PreloadPolicy];

export interface AsyncViewBudget {
    /** 该资源关联 JS/CSS 产物的 gzip 总预算，单位为字节。 */
    readonly gzipBytes?: number;
}

/** 构建期和运行时共享的异步资源身份及治理元数据。 */
export interface AsyncViewResource {
    readonly id: string;
    readonly chunkName: string;
    readonly source: string;
    readonly preload: PreloadPolicy;
    readonly budget?: AsyncViewBudget;
}

export interface AsyncViewLoadOptions {
    /** 单次尝试的超时毫秒数；小于等于 0 表示关闭超时。 */
    readonly timeoutMs?: number;
    /** 首次加载之后允许的额外重试次数。 */
    readonly retry?: number;
    /** 决定某次失败是否可重试；attempt 从 1 开始。 */
    readonly shouldRetry?: (error: unknown, attempt: number) => boolean;
}

export type LoadStatus = "idle" | "loading" | "ready" | "failed";

/**
 * 加载器的不可变状态快照。
 * 使用可辨识联合保证 ready/failed 状态下才能访问 value/error。
 */
export type LoadSnapshot<Value> =
    | Readonly<{ status: "idle"; attempts: 0 }>
    | Readonly<{ status: "loading"; attempts: number }>
    | Readonly<{ status: "ready"; attempts: number; value: Value }>
    | Readonly<{ status: "failed"; attempts: number; error: unknown }>;

export interface AsyncViewFallbackProps {
    readonly status: "loading" | "failed";
    readonly error?: unknown;
    /** failed 状态下显式清空失败缓存并重新加载。 */
    readonly retry: () => Promise<void>;
}

/** 定义异步组件所需的运行时加载配置和构建期资源描述。 */
export interface AsyncViewOptions<Props extends object, Module> extends AsyncViewLoadOptions {
    readonly resource?: AsyncViewResource;
    readonly id?: string;
    readonly chunkName?: string;
    readonly source?: string;
    readonly preload?: PreloadPolicy;
    readonly budget?: AsyncViewBudget;
    /** 动态 import 函数；定义组件和注册弹框时都不会提前执行。 */
    readonly loader: () => Promise<Module>;
    readonly exportName?: string;
    readonly resolve?: (module: Module) => ComponentType<Props>;
    readonly fallback?: ComponentType<AsyncViewFallbackProps>;
}

/**
 * 保留 React 组件调用能力，并附带可用于预加载、重置和诊断的静态方法。
 */
export type AsyncViewComponent<Props extends object> = ComponentType<Props> & {
    readonly id: string;
    readonly resource: Readonly<AsyncViewResource>;
    preload(options?: AsyncViewLoadOptions): Promise<ComponentType<Props>>;
    reset(): void;
    getStatus(): LoadStatus;
    getSnapshot(): LoadSnapshot<ComponentType<Props>>;
};

export type PopupFailureMode = "resolve-undefined" | "reject";

export interface PopupRequestContext<Key extends PropertyKey = PropertyKey> {
    readonly key: Key;
    readonly instanceId: string;
    readonly priority: number;
    readonly requestedAt: number;
    /** popup 表示进入串行优先级队列，open 表示立即开始加载。 */
    readonly mode: "popup" | "open";
}

/** PopupManager 的容错、标识生成和可观测性配置。 */
export interface PopupManagerOptions {
    readonly timeoutMs?: number;
    readonly retry?: number;
    readonly shouldRetry?: (error: unknown, attempt: number) => boolean;
    /**
     * 默认 resolve-undefined，把加载失败视为界面不可用；reject 适合必须由业务显式处理的流程。
     */
    readonly failureMode?: PopupFailureMode;
    readonly hooks?: PopupLifecycleHooks;
    readonly createInstanceId?: (key: string, sequence: number) => string;
    readonly now?: () => number;
    readonly onRequest?: (context: PopupRequestContext) => void;
    readonly onLoadError?: (context: PopupRequestContext, error: unknown, attempts: number) => void;
    readonly onVisible?: (context: PopupRequestContext) => void;
    readonly onClose?: (context: PopupRequestContext, reason: PopupCloseReason, result: unknown) => void;
    /** 生命周期钩子异常的隔离出口；它自身抛错也不会改变弹框状态。 */
    readonly onHookError?: (error: unknown, hook: string) => void;
}

export interface PopupLifecycleHooks {
    readonly onRequest?: (context: PopupRequestContext) => void;
    readonly onLoadError?: (context: PopupRequestContext, error: unknown, attempts: number) => void;
    readonly onVisible?: (context: PopupRequestContext) => void;
    readonly onClose?: (context: PopupRequestContext, reason: PopupCloseReason, result: unknown) => void;
}

export type PopupCloseReason = "close" | "close-all" | "cancel" | "host-detached";

export type PopupCallOptions = Readonly<{
    priority?: number;
    instanceId?: string;
}>;
