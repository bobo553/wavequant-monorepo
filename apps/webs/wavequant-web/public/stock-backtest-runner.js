import { retryBacktest, waitForBacktestJob } from "./backtest-retry.js";
import { backtestArgumentsKey } from "./watchlist-backtest-queue.js";

/** Reuse identical jobs; a different configuration must wait without adopting its result. */
export async function runStockBacktest(task, {
    api, progress, jobs = [], recent = [], signal, now = Date.now,
    maxWaitMs = 24 * 60 * 60 * 1000, pollDelay, sleep,
}) {
    const deadline = now() + maxWaitMs;
    const request = () => api(task.path, task.params, signal);
    const onRetry = (attempt, total) => progress(`状态查询暂不可用，重试中（${attempt}/${total}）…`);
    let following = false;
    const matches = (job) => job.version === task.version && job.params
        && backtestArgumentsKey(job.path, job.params) === backtestArgumentsKey(task.path, task.params);
    const completed = recent.find((job) => job.status === "completed" && job.result_valid
        && job.result_available && matches(job));
    if (completed) {
        progress("正在读取匹配当前设置的回测缓存…");
        try {
            const cached = await api("/api/backtest-job", { job: completed.job }, signal);
            if (cached.status === "completed" && cached.result?.result_scope === "stock"
                && cached.result.symbol === task.symbol && cached.result.asof === task.params.asof
                && cached.result.backtest && cached.result.backtest.status !== "data_unavailable") {
                task.params.backtest_job = completed.job;
                return cached.result;
            }
        } catch (error) {
            signal?.throwIfAborted();
            // Evicted results fall through to the persisted artifact cache in the worker.
            if (error.httpStatus !== 404) throw error;
        }
    }
    const existing = jobs.find((job) => job.status === "running" && job.symbol === task.symbol);
    const follow = (jobId) => {
        task.params.backtest_job = jobId;
        task.waitingForJob = null;
        following = true;
        progress("相同参数的回测已在运行，正在同步任务状态…");
    };
    const wait = (jobId) => {
        task.waitingForJob = jobId;
        following = false;
        progress("该股票已有其他设置的回测，正在等待；结束后自动运行当前设置。");
    };
    if (existing?.job) {
        if (matches(existing))
            follow(existing.job);
        else wait(existing.job);
    }
    for (;;) {
        signal?.throwIfAborted();
        if (now() >= deadline) {
            const error = new Error("回测任务仍在后台运行，已停止自动等待；请稍后重新选择当前股票");
            error.code = "BACKTEST_JOB_WAIT_EXPIRED";
            throw error;
        }
        const waiting = task.waitingForJob;
        try {
            if (following || waiting) {
                const jobId = waiting || task.params.backtest_job;
                const result = await waitForBacktestJob(() => api("/api/backtest-job", { job: jobId }, signal), request, {
                    signal, maxMissingResubmits: 0, maxWaitMs: deadline - now(), now, pollDelay, sleep, onRetry,
                    onPending: () => progress(waiting
                        ? "该股票已有其他设置的回测，正在等待；结束后自动运行当前设置。"
                        : "服务器仍在计算，正在查询任务状态…"),
                });
                if (!waiting) return result;
            } else {
                return await retryBacktest(request, { signal, onRetry, sleep });
            }
        } catch (error) {
            signal?.throwIfAborted();
            if (error.code === "BACKTEST_SYMBOL_RUNNING" && error.running_job) {
                if (error.same_request === true) follow(error.running_job);
                else wait(error.running_job);
                continue;
            }
            if (error.code === "BACKTEST_TIMEOUT") {
                following = true;
                progress("服务器仍在计算，正在查询任务状态…");
                continue;
            }
            if (waiting && ["BACKTEST_JOB_FAILED", "BACKTEST_JOB_MISSING"].includes(error.code)) {
                // The blocking job's result and failure belong to its own settings.
            } else throw error;
        }
        task.waitingForJob = null;
        following = false;
        progress("正在提交当前设置的回测…");
    }
}
