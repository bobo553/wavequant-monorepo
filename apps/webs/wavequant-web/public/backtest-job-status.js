export const BACKTEST_STATUS_STALE_MS = 15_000;

export function selectedBacktestAction({ historical, canBacktest, autoBacktest, completed = false }) {
    return {
        run: canBacktest && (autoBacktest || historical),
        force: historical,
        direct: completed && !historical,
    };
}

export function selectedBacktestButton(status, showingCompletedResult) {
    if (status === "completed" && !showingCompletedResult) return { label: "已完成 · 查看回测", force: false };
    return {
        label:
            {
                pending: "待回测 · 运行当前股票回测",
                running: "回测中",
                unknown: "状态待确认",
                completed: "已完成 · 重新回测",
                historical: "已回测 · 待更新",
                failed: "失败 · 重新回测",
                unavailable: "无数据 · 暂不可回测",
            }[status] || "待回测 · 运行当前股票回测",
        force: true,
    };
}

export function historicalBacktestStatuses(statuses, recent) {
    const merged = { ...statuses };
    for (const record of recent || []) {
        if (
            record?.status === "completed" &&
            record.result_valid === true &&
            typeof record.symbol === "string" &&
            record.symbol &&
            !merged[record.symbol]
        ) {
            merged[record.symbol] = "historical";
        }
    }
    return merged;
}

export function adoptServerBacktestHistory(queue, recent, tasks) {
    if (!Array.isArray(recent) || !queue.strategyVersion) return false;
    queue.sync(queue.snapshot());
    for (const record of recent) {
        // A retained local result is still readable after the server drops its short-lived result copy.
        if (
            record?.status === "completed" &&
            record.result_valid === true &&
            !record.result_available &&
            record.params &&
            tasks.find(record.path, record.params, record.version)?.status === "completed"
        )
            continue;
        if (queue.adoptServerStatus(record)) return true;
    }
    return false;
}

export function runningBacktestStatuses(statuses, jobs, { unavailable = false } = {}) {
    const merged = Object.fromEntries(
        Object.entries(statuses).map(([symbol, status]) => [
            symbol,
            unavailable && status === "running" ? "unknown" : status,
        ]),
    );
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

export function formatBacktestProgress(percent) {
    return Number.isInteger(percent) && percent >= 0 && percent <= 99 ? `${percent}%` : "";
}
