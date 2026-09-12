import { POPUP_CANCELLED } from "../constants";

interface QueueEntry<Result> {
    readonly id: string;
    readonly priority: number;
    readonly sequence: number;
    readonly run: () => Promise<Result>;
    readonly resolve: (value: Result) => void;
    readonly reject: (reason: unknown) => void;
}

export interface QueueTask<Result> {
    readonly id: string;
    readonly promise: Promise<Result>;
    cancel(): boolean;
}

/**
 * 面向弹框加载的稳定串行优先级队列。
 *
 * 数值越小优先级越高；相同优先级按入队顺序执行。每个任务会持续到弹框业务 Promise
 * 结算，因此 `popup` 默认保证前一个弹框关闭后才开始下一个，避免多个互斥提示重叠。
 */
export class PriorityQueue {
    private readonly entries: QueueEntry<unknown>[] = [];
    private sequence = 0;
    private scheduled = false;
    private running = false;

    public get size(): number {
        return this.entries.length;
    }

    /** 入队后在下一微任务开始消费，给同一同步调用栈中的任务一次统一排序机会。 */
    public enqueue<Result>(id: string, priority: number, run: () => Promise<Result>): QueueTask<Result> {
        if (!Number.isFinite(priority)) {
            throw new RangeError("priority must be a finite number");
        }
        if (this.entries.some((entry) => entry.id === id)) {
            throw new Error(`Queue task id already exists: ${id}`);
        }

        let resolvePromise!: (value: Result) => void;
        let rejectPromise!: (reason: unknown) => void;
        const promise = new Promise<Result>((resolve, reject) => {
            resolvePromise = resolve;
            rejectPromise = reject;
        });
        const entry: QueueEntry<Result> = {
            id,
            priority,
            sequence: this.sequence++,
            run,
            resolve: resolvePromise,
            reject: rejectPromise,
        };
        this.entries.push(entry as QueueEntry<unknown>);
        this.schedule();

        return {
            id,
            promise,
            cancel: () => this.cancel(id),
        };
    }

    /** 仅取消尚未开始的任务；正在运行的取消由 PopupManager 的 cancellation 信号处理。 */
    public cancel(id: string): boolean {
        const index = this.entries.findIndex((entry) => entry.id === id);
        if (index < 0) {
            return false;
        }

        const [entry] = this.entries.splice(index, 1);
        entry?.reject(POPUP_CANCELLED);
        return true;
    }

    public cancelAll(): number {
        const cancelled = this.entries.splice(0);
        for (const entry of cancelled) {
            entry.reject(POPUP_CANCELLED);
        }
        return cancelled.length;
    }

    private schedule(): void {
        if (this.scheduled || this.running) {
            return;
        }
        this.scheduled = true;
        // 微任务调度避免 enqueue 同步触发业务代码，也确保同批任务先完成稳定排序。
        queueMicrotask(() => {
            this.scheduled = false;
            void this.drain();
        });
    }

    private async drain(): Promise<void> {
        if (this.running) {
            return;
        }
        this.running = true;

        try {
            while (this.entries.length > 0) {
                this.entries.sort((left, right) => left.priority - right.priority || left.sequence - right.sequence);
                const entry = this.entries.shift();
                if (!entry) {
                    continue;
                }

                try {
                    entry.resolve(await entry.run());
                } catch (error) {
                    entry.reject(error);
                }
            }
        } finally {
            this.running = false;
            if (this.entries.length > 0) {
                this.schedule();
            }
        }
    }
}
