import { backtestArgumentsKey } from "./watchlist-backtest-queue.js";

/** Keeps a stock's calculation alive when the visible chart changes. */
export class StockBacktestTasks {
    constructor({ run, onChange, createJobId = () => crypto.randomUUID(), maxCompleted = 8 }) {
        Object.assign(this, { run, onChange, createJobId, maxCompleted });
        this.tasks = new Map();
        this.chains = new Map();
    }

    find(path, params, version = "") {
        return this.tasks.get(JSON.stringify([version, backtestArgumentsKey(path, params)]));
    }

    adoptCompleted(path, params, result, { version = "", jobId } = {}) {
        const key = JSON.stringify([version, backtestArgumentsKey(path, params)]);
        const previous = this.tasks.get(key);
        if (previous?.status === "running" || previous?.status === "completed") return previous;
        const task = {
            key,
            version,
            path,
            params: { ...params, ...(version ? { strategy_version: version } : {}), backtest_job: jobId || this.createJobId() },
            symbol: params.symbol,
            status: "completed",
            message: "计算已完成",
            startedAt: Date.now(),
            finishedAt: Date.now(),
            result,
            error: null,
            promise: Promise.resolve(result),
        };
        this.tasks.set(key, task);
        this.pruneCompleted();
        this.onChange?.(task);
        return task;
    }

    invalidateCompleted() {
        // In-flight workers retain their symbol lock until they actually finish.
        for (const [key, task] of this.tasks)
            if (task.status !== "running") this.tasks.delete(key);
    }

    pruneCompleted() {
        const completed = [...this.tasks.values()].filter((task) => task.status === "completed")
            .sort((left, right) => right.finishedAt - left.finishedAt);
        for (const task of completed.slice(this.maxCompleted)) this.tasks.delete(task.key);
    }

    supersede(task) {
        if (!task) return;
        task.status = "superseded";
        task.finishedAt = Date.now();
        task.error = Object.assign(new Error("回测设置已更新，等待任务已替换为最新设置"), {
            code: "BACKTEST_REQUEST_SUPERSEDED",
        });
        this.tasks.delete(task.key);
        task.reject(task.error);
        this.onChange?.(task);
    }

    start(path, params, { version = "", force = false, jobId } = {}) {
        const key = JSON.stringify([version, backtestArgumentsKey(path, params)]);
        const previous = this.tasks.get(key);
        const symbolKey = params.symbol.toLowerCase();
        const chain = this.chains.get(symbolKey);
        if (previous?.status === "running" || (previous?.status === "completed" && !force)) {
            if (chain?.pending && chain.pending !== previous) {
                this.supersede(chain.pending);
                chain.pending = null;
            }
            return previous;
        }
        // A changed strategy may not reuse this stock's old result, even after switching back.
        for (const [oldKey, task] of this.tasks)
            if (task.symbol.toLowerCase() === symbolKey && task.version !== version && task.status !== "running")
                this.tasks.delete(oldKey);
        const task = {
            key,
            version,
            path,
            params: { ...params, ...(version ? { strategy_version: version } : {}), backtest_job: jobId || this.createJobId() },
            symbol: params.symbol,
            status: "running",
            message: "正在读取行情并计算趋势…",
            startedAt: Date.now(),
            result: null,
            error: null,
        };
        task.promise = new Promise((resolve, reject) => Object.assign(task, { resolve, reject }));
        // Selection can change before its caller awaits this task.
        task.promise.catch(() => {});
        this.tasks.set(key, task);
        if (chain) {
            this.supersede(chain.pending);
            chain.pending = task;
            task.queued = true;
            task.waitingForJob = chain.active.params.backtest_job;
            task.message = "该股票正在回测，结束后仅处理最新设置，并优先读取缓存。";
            this.onChange?.(task);
        } else {
            const next = { active: task, pending: null };
            this.chains.set(symbolKey, next);
            this.execute(task, symbolKey, next);
        }
        return task;
    }

    execute(task, symbolKey, chain) {
        task.queued = false;
        task.waitingForJob = null;
        this.onChange?.(task);
        void Promise.resolve()
            .then(() => this.run(task, (message) => {
                if (task.message === message) return;
                task.message = message;
                this.onChange?.(task);
            }))
            .then((result) => {
                task.status = "completed";
                task.result = result;
                task.finishedAt = Date.now();
                this.pruneCompleted();
                this.onChange?.(task);
                task.resolve(result);
            }, (error) => {
                task.status = "failed";
                task.error = error;
                task.finishedAt = Date.now();
                this.onChange?.(task);
                task.reject(error);
            })
            .finally(() => {
                const next = chain.pending;
                chain.pending = null;
                if (next) {
                    chain.active = next;
                    if (task.version !== next.version) this.tasks.delete(task.key);
                    this.execute(next, symbolKey, chain);
                } else this.chains.delete(symbolKey);
            });
    }
}
