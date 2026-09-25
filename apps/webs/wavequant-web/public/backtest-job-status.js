export function runningBacktestStatuses(statuses, jobs) {
    const merged = { ...statuses };
    for (const job of jobs || []) {
        if (job?.status !== "running") continue;
        const symbol = job.symbol || job.params?.symbol;
        if (typeof symbol === "string" && symbol) merged[symbol] = "running";
    }
    return merged;
}
