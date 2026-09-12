import { DEFAULT_LOAD_RETRY, DEFAULT_LOAD_TIMEOUT_MS } from "../constants";
import type { AsyncViewLoadOptions, LoadSnapshot, LoadStatus } from "../types";

/** 单次资源加载超过上限时的可识别错误。 */
export class AsyncViewTimeoutError extends Error {
    public constructor(readonly timeoutMs: number) {
        super(`Async view load timed out after ${timeoutMs}ms`);
        this.name = "AsyncViewTimeoutError";
    }
}

/**
 * 所有重试均失败后的统一错误边界。
 * 原始异常保存在 cause，调用方不必处理各浏览器不同的动态导入错误格式。
 */
export class AsyncViewLoadError extends Error {
    public override readonly cause: unknown;

    public constructor(attempts: number, cause: unknown) {
        super(`Async view failed after ${attempts} attempt(s)`);
        this.name = "AsyncViewLoadError";
        this.cause = cause;
    }
}

/** 判断错误是否属于常见的临时性 Chunk/网络加载故障。 */
export function isRetryableAsyncViewError(error: unknown): boolean {
    if (error instanceof AsyncViewTimeoutError) {
        return true;
    }

    return (
        error instanceof Error &&
        (error.name === "ChunkLoadError" ||
            /loading (css )?chunk|dynamic import|failed to fetch dynamically imported module|importing a module script/i.test(
                error.message,
            ))
    );
}

export const defaultShouldRetry = isRetryableAsyncViewError;

/**
 * 管理一个异步资源的完整加载生命周期。
 *
 * 控制器会缓存 ready/failed 终态，并复用进行中的 Promise，从而保证多个组件或预加载
 * 请求不会重复触发同一个动态 import。失败必须通过 reset 显式恢复，防止渲染循环自动重试。
 */
export class LoadController<Value> {
    private snapshot: LoadSnapshot<Value> = Object.freeze({ status: "idle", attempts: 0 });
    private loadingPromise: Promise<Value> | undefined;
    private readonly listeners = new Set<() => void>();
    private readonly timeoutMs: number;
    private readonly retry: number;
    private readonly shouldRetry: (error: unknown, attempt: number) => boolean;

    public constructor(
        private readonly loader: () => Promise<Value>,
        options: AsyncViewLoadOptions = {},
    ) {
        this.timeoutMs = options.timeoutMs ?? DEFAULT_LOAD_TIMEOUT_MS;
        this.retry = options.retry ?? DEFAULT_LOAD_RETRY;
        this.shouldRetry = options.shouldRetry ?? defaultShouldRetry;

        if (!Number.isFinite(this.timeoutMs)) {
            throw new RangeError("timeoutMs must be a finite number");
        }
        if (!Number.isInteger(this.retry) || this.retry < 0) {
            throw new RangeError("retry must be a non-negative integer");
        }
    }

    /** 订阅状态变化，签名与 React `useSyncExternalStore` 直接兼容。 */
    public readonly subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    };

    public readonly getSnapshot = (): LoadSnapshot<Value> => this.snapshot;

    public getStatus(): LoadStatus {
        return this.snapshot.status;
    }

    /**
     * 加载资源并返回共享 Promise。
     * 方法级配置只影响本轮加载，不会改变构造时默认值。
     */
    public load(options: AsyncViewLoadOptions = {}): Promise<Value> {
        if (this.snapshot.status === "ready") {
            return Promise.resolve(this.snapshot.value);
        }
        if (this.snapshot.status === "failed") {
            return Promise.reject(this.snapshot.error);
        }
        if (this.loadingPromise) {
            return this.loadingPromise;
        }

        const timeoutMs = options.timeoutMs ?? this.timeoutMs;
        const retry = options.retry ?? this.retry;
        if (!Number.isFinite(timeoutMs)) {
            throw new RangeError("timeoutMs must be a finite number");
        }
        if (!Number.isInteger(retry) || retry < 0) {
            throw new RangeError("retry must be a non-negative integer");
        }

        this.update(Object.freeze({ status: "loading", attempts: 0 }));
        this.loadingPromise = this.runAttempts(options).finally(() => {
            this.loadingPromise = undefined;
        });
        return this.loadingPromise;
    }

    /** 清除 ready/failed 缓存；进行中的任务不可重置，以免出现两个并发写入者。 */
    public reset(): void {
        if (this.snapshot.status === "loading") {
            throw new Error("Cannot reset an async view while it is loading");
        }
        this.update(Object.freeze({ status: "idle", attempts: 0 }));
    }

    private async runAttempts(options: AsyncViewLoadOptions): Promise<Value> {
        const timeoutMs = options.timeoutMs ?? this.timeoutMs;
        const retry = options.retry ?? this.retry;
        const shouldRetry = options.shouldRetry ?? this.shouldRetry;
        let attempt = 0;
        let lastError: unknown;

        while (attempt <= retry) {
            attempt += 1;
            this.update(Object.freeze({ status: "loading", attempts: attempt }));

            try {
                const value = await this.loadWithTimeout(timeoutMs);
                this.update(Object.freeze({ status: "ready", attempts: attempt, value }));
                return value;
            } catch (error) {
                lastError = error;
                if (attempt <= retry && shouldRetry(error, attempt)) {
                    continue;
                }

                const loadError = new AsyncViewLoadError(attempt, lastError);
                this.update(Object.freeze({ status: "failed", attempts: attempt, error: loadError }));
                throw loadError;
            }
        }

        throw new Error("Unreachable async view load state");
    }

    private async loadWithTimeout(timeoutMs: number): Promise<Value> {
        // 延迟到微任务执行 loader，使同步抛错也统一进入 Promise 错误链。
        const loading = Promise.resolve().then(this.loader);
        if (timeoutMs <= 0) {
            return loading;
        }

        let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
        const timeout = new Promise<never>((_, reject) => {
            timeoutHandle = setTimeout(() => reject(new AsyncViewTimeoutError(timeoutMs)), timeoutMs);
        });

        try {
            return await Promise.race([loading, timeout]);
        } finally {
            if (timeoutHandle !== undefined) {
                clearTimeout(timeoutHandle);
            }
        }
    }

    private update(snapshot: LoadSnapshot<Value>): void {
        // 先替换完整不可变快照，再通知订阅者，避免观察到半更新状态。
        this.snapshot = snapshot;
        for (const listener of this.listeners) {
            listener();
        }
    }
}
