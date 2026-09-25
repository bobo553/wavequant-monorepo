const RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 6_000];
const JOB_POLL_DELAY_MS = 2_000;
const MAX_JOB_WAIT_MS = 30 * 60_000;

export function isRecoverableBacktestError(error) {
    if (error?.name === "AbortError" || error?.name === "TimeoutError") return false;
    if (["BACKTEST_JOB_FAILED", "BACKTEST_JOB_MISSING", "BACKTEST_CAPACITY", "BACKTEST_SYMBOL_RUNNING"].includes(error?.code)) return false;
    if (error?.code === "BACKTEST_POLL_TIMEOUT") return true;
    if (error instanceof TypeError) return true;
    if ([500, 502, 503, 504].includes(error?.httpStatus)) return true;
    const message = String(error?.message || "");
    return message.includes("策略代码已变更") || /HTTP\s+(?:500|502|503|504)\b/.test(message);
}

function waitForRetry(delayMs, signal) {
    if (signal?.aborted) return Promise.reject(signal.reason);
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            signal?.removeEventListener("abort", abort);
            resolve();
        }, delayMs);
        function abort() {
            clearTimeout(timer);
            reject(signal.reason);
        }
        signal?.addEventListener("abort", abort, { once: true });
    });
}

export async function retryBacktest(
    request,
    { signal, onRetry, onTimeout, delays = RETRY_DELAYS_MS, sleep = waitForRetry } = {},
) {
    let transientRetries = 0;
    for (;;) {
        if (signal?.aborted) throw signal.reason;
        try {
            return await request();
        } catch (error) {
            if (signal?.aborted) throw signal.reason;
            if (error?.code === "BACKTEST_TIMEOUT") {
                if (onTimeout) return onTimeout(error);
                throw error;
            }
            if (!isRecoverableBacktestError(error)) throw error;
            if (transientRetries >= delays.length) throw error;
            onRetry?.(transientRetries + 1, delays.length, error);
            await sleep(delays[transientRetries++], signal);
        }
    }
}

/** A timed-out GET may still be computing on the server. Follow its queryable job instead of starting another computation. */
export async function waitForBacktestJob(
    poll,
    resubmit,
    {
        signal,
        onPending,
        onRestart,
        onRetry,
        pollDelay = JOB_POLL_DELAY_MS,
        maxWaitMs = MAX_JOB_WAIT_MS,
        maxMissingResubmits = 1,
        now = Date.now,
        sleep = waitForRetry,
    } = {},
) {
    let missingResubmits = 0;
    const deadline = now() + maxWaitMs;
    for (;;) {
        if (signal?.aborted) throw signal.reason;
        if (now() >= deadline) {
            const expired = new Error("回测任务仍在后台运行，已停止自动等待；请稍后重新运行当前股票回测");
            expired.code = "BACKTEST_JOB_WAIT_EXPIRED";
            throw expired;
        }
        let job;
        try {
            job = await retryBacktest(poll, { signal, onRetry });
        } catch (error) {
            if (signal?.aborted) throw signal.reason;
            if (error?.httpStatus !== 404) throw error;
            if (missingResubmits >= maxMissingResubmits) {
                const missing = new Error("回测任务已丢失，请重新运行当前股票回测");
                missing.code = "BACKTEST_JOB_MISSING";
                throw missing;
            }
            missingResubmits++;
            onRestart?.(missingResubmits, maxMissingResubmits);
            try {
                return await retryBacktest(resubmit, { signal, onRetry });
            } catch (submitError) {
                if (submitError?.code !== "BACKTEST_TIMEOUT") throw submitError;
                onPending?.();
                continue;
            }
        }
        if (job?.status === "completed" && job.result && typeof job.result === "object") return job.result;
        if (job?.status === "failed") {
            const failure = new Error(typeof job.error === "string" && job.error ? job.error : "回测任务失败");
            failure.code = "BACKTEST_JOB_FAILED";
            if (Number.isInteger(job.http_status)) failure.httpStatus = job.http_status;
            throw failure;
        }
        if (job?.status !== "running") throw new Error("回测任务状态格式异常");
        onPending?.();
        await sleep(pollDelay, signal);
    }
}
