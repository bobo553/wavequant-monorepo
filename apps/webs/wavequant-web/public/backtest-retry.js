const RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 6_000];

export function isRecoverableBacktestError(error) {
    if (error?.name === "AbortError" || error?.name === "TimeoutError") return false;
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

export async function retryBacktest(request, { signal, onRetry, delays = RETRY_DELAYS_MS, sleep = waitForRetry } = {}) {
    for (let attempt = 0; ; attempt++) {
        if (signal?.aborted) throw signal.reason;
        try {
            return await request();
        } catch (error) {
            if (signal?.aborted) throw signal.reason;
            if (attempt >= delays.length || !isRecoverableBacktestError(error)) throw error;
            onRetry?.(attempt + 1, delays.length);
            await sleep(delays[attempt], signal);
        }
    }
}
