import { POPUP_CANCELLED } from "../constants";
import { PriorityQueue } from "../core/priority-queue";
import type {
    AsyncViewComponent,
    AsyncViewLoadOptions,
    NoPopupProps,
    PopupCallOptions,
    PopupCloseReason,
    PopupLifecycleHooks,
    PopupManagerOptions,
    PopupRequestContext,
    PreloadPolicy,
} from "../types";
import type { PopupDefinition, PopupProps, PopupRegistryKey, PopupResult } from "./popup-registry";
import { PopupStore } from "./popup-store";

type RegistryShape = Readonly<Record<string, unknown>>;

type PopupArguments<Props extends object> = Props extends NoPopupProps
    ? [props?: Props, options?: PopupCallOptions]
    : [props: Props, options?: PopupCallOptions];

interface RequestState<Result> {
    readonly context: PopupRequestContext<string>;
    readonly cancellation: Promise<typeof POPUP_CANCELLED>;
    cancel(): void;
    readonly result: Promise<Result | undefined>;
    resolve(value: Result | undefined): void;
    reject(error: unknown): void;
    phase: "queued" | "loading" | "visible" | "settled";
}

/**
 * 为一次弹框请求建立两个独立 Promise：
 * cancellation 负责中断加载等待，result 则承载最终业务结果，避免把内部控制信号泄漏给调用方。
 */
function createRequestState<Result>(context: PopupRequestContext<string>): RequestState<Result> {
    let signalCancel!: () => void;
    let resolveResult!: (value: Result | undefined) => void;
    let rejectResult!: (error: unknown) => void;

    return {
        context,
        cancellation: new Promise((resolve) => {
            signalCancel = () => resolve(POPUP_CANCELLED);
        }),
        cancel: () => signalCancel(),
        result: new Promise((resolve, reject) => {
            resolveResult = resolve;
            rejectResult = reject;
        }),
        resolve: (value) => resolveResult(value),
        reject: (error) => rejectResult(error),
        phase: "queued",
    };
}

/**
 * 协调弹框的注册资源、加载队列、可见实例和业务 Promise。
 *
 * `popup` 经过稳定优先级队列，适合互斥或有顺序要求的提示；`open` 立即开始加载，适合
 * 可并发展示的界面。两者返回值都在组件调用 close 后结算，并共享相同的取消与容错语义。
 */
export class PopupManager<Registry extends RegistryShape> {
    public readonly popups: Readonly<{ [Key in keyof Registry]: Key }>;
    public readonly resources: readonly Readonly<PopupDefinition["resource"]>[];
    public readonly store = new PopupStore();

    private readonly queue = new PriorityQueue();
    private readonly views = new Map<string, AsyncViewComponent<Record<string, unknown>>>();
    private readonly requests = new Map<string, RequestState<unknown>>();
    private readonly loadOptions: Readonly<AsyncViewLoadOptions>;
    private readonly options: Required<Pick<PopupManagerOptions, "failureMode" | "createInstanceId" | "now">> &
        PopupManagerOptions;
    private sequence = 0;
    private hostOwner: symbol | undefined;
    private hostGeneration = 0;

    public constructor(
        public readonly registry: Registry,
        options: PopupManagerOptions = {},
    ) {
        this.options = {
            ...options,
            failureMode: options.failureMode ?? "resolve-undefined",
            now: options.now ?? Date.now,
            createInstanceId:
                options.createInstanceId ?? ((key, sequence) => `${String(key)}:${this.options.now()}:${sequence}`),
        };

        const popupKeys: Record<string, string> = {};
        const resources = [];
        const loadOptions: AsyncViewLoadOptions = {
            timeoutMs: options.timeoutMs,
            retry: options.retry,
            shouldRetry: options.shouldRetry,
        };
        this.loadOptions = Object.freeze(loadOptions);

        for (const [key, definition] of Object.entries(registry)) {
            popupKeys[key] = key;
            const popupDefinition = definition as unknown as PopupDefinition<object, unknown>;
            resources.push(popupDefinition.resource);
            this.views.set(key, popupDefinition.view as unknown as AsyncViewComponent<Record<string, unknown>>);
        }
        this.popups = Object.freeze(popupKeys) as Readonly<{ [Key in keyof Registry]: Key }>;
        this.resources = Object.freeze(resources);
    }

    /** 按优先级串行开始加载弹框，数值较小的 priority 先执行。 */
    public popup<Key extends PopupRegistryKey<Registry>>(
        key: Key,
        ...args: PopupArguments<PopupProps<Registry, Key>>
    ): Promise<PopupResult<Registry, Key> | undefined> {
        return this.request(key, "popup", args[0] as PopupProps<Registry, Key> | undefined, args[1]);
    }

    /** 跳过调度队列并立即开始加载，仍由 PopupHost 统一渲染。 */
    public open<Key extends PopupRegistryKey<Registry>>(
        key: Key,
        ...args: PopupArguments<PopupProps<Registry, Key>>
    ): Promise<PopupResult<Registry, Key> | undefined> {
        return this.request(key, "open", args[0] as PopupProps<Registry, Key> | undefined, args[1]);
    }

    /** 关闭指定可见实例，并将 result 作为调用 Promise 的结果。 */
    public close(instanceId: string, result?: unknown): boolean {
        return this.settle(instanceId, "close", result, false);
    }

    public closeInstance(instanceId: string, result?: unknown): boolean {
        return this.close(instanceId, result);
    }

    public closeAll(): number {
        // closeAll 只处理已经可见的实例；排队或加载中的请求需要显式 cancelAll。
        const instanceIds = [...this.requests.values()]
            .filter(({ phase }) => phase === "visible")
            .map(({ context }) => context.instanceId);
        for (const instanceId of instanceIds) {
            this.settle(instanceId, "close-all", undefined, false);
        }
        return instanceIds.length;
    }

    public cancel(instanceId: string): boolean {
        const request = this.requests.get(instanceId);
        if (!request || request.phase === "settled") {
            return false;
        }

        if (request.phase === "queued") {
            this.queue.cancel(instanceId);
        }
        // 即使任务已进入加载阶段，也可通过竞速信号立即结束等待，不依赖 import 支持 AbortSignal。
        request.cancel();
        return this.settle(instanceId, "cancel", undefined, false);
    }

    /** 取消排队、加载中和可见的全部请求；业务侧统一得到 undefined。 */
    public cancelAll(reason: Extract<PopupCloseReason, "cancel" | "host-detached"> = "cancel"): number {
        this.queue.cancelAll();
        const instanceIds = [...this.requests.keys()];
        for (const instanceId of instanceIds) {
            const request = this.requests.get(instanceId);
            request?.cancel();
            this.settle(instanceId, reason, undefined, false);
        }
        return instanceIds.length;
    }

    public preload<Key extends PopupRegistryKey<Registry>>(
        key: Key,
    ): Promise<AsyncViewComponent<Record<string, unknown>>> {
        return this.getView(key)
            .preload(this.loadOptions)
            .then(() => this.getView(key));
    }

    public async preloadByPolicy(policy: PreloadPolicy): Promise<void> {
        const keys = Object.entries(this.registry)
            .filter(([, definition]) => (definition as PopupDefinition<object, unknown>).resource.preload === policy)
            .map(([key]) => key);
        await Promise.all(keys.map((key) => this.preload(key as PopupRegistryKey<Registry>)));
    }

    public resetLoad<Key extends PopupRegistryKey<Registry>>(key: Key): void {
        this.getView(key).reset();
    }

    public readonly subscribe = this.store.subscribe;
    public readonly getSnapshot = this.store.getSnapshot;
    public readonly getServerSnapshot = this.store.getServerSnapshot;

    /**
     * 绑定唯一 PopupHost，并返回卸载函数。
     * 卸载清理延迟到微任务，允许 React StrictMode 的 effect 探测立即重新挂载同一宿主。
     */
    public attachHost(owner: symbol): () => void {
        if (this.hostOwner && this.hostOwner !== owner) {
            throw new Error("A PopupManager can only be attached to one PopupHost");
        }
        this.hostOwner = owner;
        this.hostGeneration += 1;
        const generation = this.hostGeneration;

        return () => {
            if (this.hostOwner !== owner) {
                return;
            }
            this.hostOwner = undefined;
            queueMicrotask(() => {
                if (!this.hostOwner && this.hostGeneration === generation) {
                    this.cancelAll("host-detached");
                }
            });
        };
    }

    private request<Key extends PopupRegistryKey<Registry>>(
        key: Key,
        mode: "popup" | "open",
        props: PopupProps<Registry, Key> | undefined,
        callOptions: PopupCallOptions | undefined,
    ): Promise<PopupResult<Registry, Key> | undefined> {
        if (props && Object.hasOwn(props, "close")) {
            return Promise.reject(new TypeError('Popup props reserve the "close" key'));
        }

        const definition = this.registry[key] as unknown as PopupDefinition<object, unknown>;
        if (!definition) {
            return Promise.reject(new Error(`Unknown popup key: ${String(key)}`));
        }
        const sequence = this.sequence++;
        const instanceId = callOptions?.instanceId ?? this.options.createInstanceId(key, sequence);
        if (this.requests.has(instanceId)) {
            return Promise.reject(new Error(`Popup instance id already exists: ${instanceId}`));
        }
        const priority = callOptions?.priority ?? definition.priority;
        if (!Number.isFinite(priority)) {
            return Promise.reject(new RangeError("Popup priority must be finite"));
        }

        const context: PopupRequestContext<string> = Object.freeze({
            key,
            instanceId,
            priority,
            requestedAt: this.options.now(),
            mode,
        });
        const state = createRequestState<PopupResult<Registry, Key>>(context);
        this.requests.set(instanceId, state as RequestState<unknown>);
        this.callHook("onRequest", context);

        const execute = () => this.execute(state, props ?? ({} as PopupProps<Registry, Key>));
        if (mode === "popup") {
            const task = this.queue.enqueue(instanceId, priority, execute);
            task.promise.catch((error: unknown) => {
                if (error !== POPUP_CANCELLED && state.phase !== "settled") {
                    state.reject(error);
                    state.phase = "settled";
                    this.requests.delete(instanceId);
                }
            });
        } else {
            void execute().catch((error: unknown) => {
                if (state.phase !== "settled") {
                    state.reject(error);
                    state.phase = "settled";
                    this.requests.delete(instanceId);
                }
            });
        }

        return state.result;
    }

    private async execute<Key extends PopupRegistryKey<Registry>>(
        state: RequestState<PopupResult<Registry, Key>>,
        props: PopupProps<Registry, Key>,
    ): Promise<PopupResult<Registry, Key> | undefined> {
        if (state.phase === "settled") {
            throw POPUP_CANCELLED;
        }
        state.phase = "loading";

        const view = this.getView(state.context.key);
        try {
            // 动态 import 本身通常不可取消，因此仅中断等待；迟到结果仍由 AsyncView 安全缓存。
            const loaded = await Promise.race([view.preload(this.loadOptions), state.cancellation]);
            if (loaded === POPUP_CANCELLED) {
                throw POPUP_CANCELLED;
            }
        } catch (error) {
            if (error === POPUP_CANCELLED) {
                throw error;
            }
            this.callHook("onLoadError", state.context, error, view.getSnapshot().attempts);
            state.phase = "settled";
            this.requests.delete(state.context.instanceId);
            if (this.options.failureMode === "resolve-undefined") {
                // 默认把非关键 UI 加载失败降级为空结果，避免形成未处理 Promise rejection。
                state.resolve(undefined);
                return undefined;
            }
            state.reject(error);
            throw error;
        }

        state.phase = "visible";
        this.store.add({
            context: state.context,
            View: view,
            props: props as Readonly<Record<string, unknown>>,
        });
        this.callHook("onVisible", state.context);
        return state.result;
    }

    private settle(instanceId: string, reason: PopupCloseReason, result: unknown, reject: boolean): boolean {
        const state = this.requests.get(instanceId);
        if (!state || state.phase === "settled") {
            return false;
        }

        this.store.remove(instanceId);
        state.phase = "settled";
        this.requests.delete(instanceId);
        if (reject) {
            state.reject(POPUP_CANCELLED);
        } else {
            state.resolve(result);
        }
        this.callHook("onClose", state.context, reason, result);
        return true;
    }

    private getView(key: PropertyKey): AsyncViewComponent<Record<string, unknown>> {
        const view = this.views.get(String(key));
        if (!view) {
            throw new Error(`Unknown popup key: ${String(key)}`);
        }
        return view;
    }

    private callHook(hook: "onRequest", context: PopupRequestContext): void;
    private callHook(hook: "onLoadError", context: PopupRequestContext, error: unknown, attempts: number): void;
    private callHook(hook: "onVisible", context: PopupRequestContext): void;
    private callHook(hook: "onClose", context: PopupRequestContext, reason: PopupCloseReason, result: unknown): void;
    private callHook(hook: keyof PopupLifecycleHooks, ...args: unknown[]): void {
        const callback = this.options.hooks?.[hook] ?? this.options[hook];
        if (typeof callback !== "function") {
            return;
        }

        try {
            (callback as (...values: unknown[]) => void)(...args);
        } catch (error) {
            // 业务观测逻辑与状态机隔离，钩子异常只能进入诊断出口。
            try {
                this.options.onHookError?.(error, hook);
            } catch {
                // 生命周期钩子不得破坏弹框状态机。
            }
        }
    }
}

export function createPopupManager<Registry extends RegistryShape>(
    registry: Registry,
    options?: PopupManagerOptions,
): PopupManager<Registry> {
    return new PopupManager(registry, options);
}
