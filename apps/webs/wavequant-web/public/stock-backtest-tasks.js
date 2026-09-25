import { backtestArgumentsKey } from "./watchlist-backtest-queue.js";

/** Keeps a stock's calculation alive when the visible chart changes. */
export class StockBacktestTasks {
    constructor({ run, onChange, createJobId = () => crypto.randomUUID() }) {
        Object.assign(this, { run, onChange, createJobId });
        this.tasks = new Map();
    }

    find(path, params, version = "") {
        return this.tasks.get(JSON.stringify([version, backtestArgumentsKey(path, params)]));
    }

    adoptCompleted(path, params, result, { version = "", jobId } = {}) {
        const key = JSON.stringify([version, backtestArgumentsKey(path, params)]);
        const previous = this.tasks.get(key);
        if (previous?.status === "running" || previous?.status === "completed") return previous;
        if (this.adoptedKey && this.adoptedKey !== key) this.tasks.delete(this.adoptedKey);
        const task = {
            key,
            version,
            path,
            params: { ...params, backtest_job: jobId || this.createJobId() },
            symbol: params.symbol,
            status: "completed",
            message: "回测已完成",
            startedAt: Date.now(),
            finishedAt: Date.now(),
            result,
            error: null,
            promise: Promise.resolve(result),
        };
        this.tasks.set(key, task);
        this.adoptedKey = key;
        this.onChange?.(task);
        return task;
    }

    start(path, params, { version = "", force = false, jobId } = {}) {
        const key = JSON.stringify([version, backtestArgumentsKey(path, params)]);
        const previous = this.tasks.get(key);
        if (previous?.status === "running" || (previous && !force)) return previous;
        if (this.adoptedKey === key) this.adoptedKey = null;
        const task = {
            key,
            version,
            path,
            params: { ...params, backtest_job: jobId || this.createJobId() },
            symbol: params.symbol,
            status: "running",
            message: "正在运行策略并生成成交账本…",
            startedAt: Date.now(),
            result: null,
            error: null,
        };
        this.tasks.set(key, task);
        this.onChange?.(task);
        task.promise = Promise.resolve()
            .then(() => this.run(task, (message) => {
                if (task.message === message) return;
                task.message = message;
                this.onChange?.(task);
            }))
            .then((result) => {
                task.status = "completed";
                task.result = result;
                task.finishedAt = Date.now();
                this.onChange?.(task);
                return result;
            }, (error) => {
                task.status = "failed";
                task.error = error;
                task.finishedAt = Date.now();
                this.onChange?.(task);
                throw error;
            });
        // A stock may be left before its caller awaits the result.
        task.promise.catch(() => {});
        return task;
    }
}
