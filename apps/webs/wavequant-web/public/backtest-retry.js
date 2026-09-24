const RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 6_000];
const TIMEOUT_RETRY_DELAY_MS = 5_000;

export function isRecoverableBacktestError(error) {
    if (error?.name === "AbortError" || error?.name === "TimeoutError") return false;
    if (error?.code === "BACKTEST_TIMEOUT") return true;
    if (error instanceof TypeError) return true;
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
    { signal, onRetry, delays = RETRY_DELAYS_MS, timeoutRetryDelay = TIMEOUT_RETRY_DELAY_MS, sleep = waitForRetry } = {},
) {
    let transientRetries = 0;
    let timeoutRetries = 0;
    for (;;) {
        if (signal?.aborted) throw signal.reason;
        try {
            return await request();
        } catch (error) {
            if (signal?.aborted) throw signal.reason;
            if (!isRecoverableBacktestError(error)) throw error;
            if (error?.code === "BACKTEST_TIMEOUT") {
                if (timeoutRetries >= 1) throw error;
                timeoutRetries++;
                onRetry?.(timeoutRetries, 1, error);
                await sleep(timeoutRetryDelay, signal);
                continue;
            }
            if (transientRetries >= delays.length) throw error;
            onRetry?.(transientRetries + 1, delays.length, error);
            await sleep(delays[transientRetries++], signal);
        }
    }
}
