export const BACKTEST_STATUS_STALE_MS = 15_000;

export function historicalBacktestStatuses(statuses, recent) {
    const merged = { ...statuses };
    for (const record of recent || []) {
        if (record?.status === "completed" && record.result_valid === true &&
            typeof record.symbol === "string" && record.symbol && !merged[record.symbol]) {
            merged[record.symbol] = "historical";
        }
    }
    return merged;
}

export function runningBacktestStatuses(statuses, jobs, { unavailable = false } = {}) {
    const merged = Object.fromEntries(Object.entries(statuses).map(([symbol, status]) => [
        symbol, unavailable && status === "running" ? "unknown" : status,
    ]));
    for (const job of jobs || []) {
        if (job?.status !== "running") continue;
        const symbol = job.symbol || job.params?.symbol;
        if (typeof symbol === "string" && symbol) merged[symbol] = "running";
    }
    return merged;
}

export function expiredBacktestSnapshot(snapshot, lastSuccessAt, now = Date.now()) {
    if (now - lastSuccessAt < BACKTEST_STATUS_STALE_MS) return snapshot;
    return { active: null, max_active: snapshot.max_active, jobs: [], unavailable: true };
}

export function formatBacktestElapsed(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    const elapsed = Math.floor(seconds);
    if (elapsed < 60) return `已运行 ${elapsed} 秒`;
    const minutes = Math.floor(elapsed / 60);
    if (minutes < 60) return `已运行 ${minutes} 分钟`;
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return `已运行 ${hours} 小时${remainingMinutes ? ` ${remainingMinutes} 分钟` : ""}`;
}
