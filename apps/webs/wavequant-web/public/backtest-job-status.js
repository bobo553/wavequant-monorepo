import { backtestArgumentsKey } from "./watchlist-backtest-queue.js";

export const BACKTEST_STATUS_STALE_MS = 15_000;

export function selectedBacktestStatus({ fallback = "pending", requestStatus, taskStatus, unavailable = false }) {
    if (requestStatus === "failed" || taskStatus === "failed") return "failed";
    if (taskStatus === "running") return unavailable ? "unknown" : "running";
    if (requestStatus === "updating") return "updating";
    if (requestStatus === "completed" && taskStatus === "completed") return "completed";
    return fallback;
}

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
                updating: "正在更新当前股票…",
                unknown: "状态待确认",
                completed: "已完成 · 重新回测",
                historical: "已回测 · 待更新",
                failed: "失败 · 重新回测",
                unavailable: "无数据 · 暂不可回测",
            }[status] || "待回测 · 运行当前股票回测",
        force: true,
    };
}

export const backtestRecordMatchesSource = (record, source) => !source || record?.path === `/api/${source}-backtest`;

export function selectedRunningBacktestJob(jobs, symbol, source, task = null) {
    if (!symbol || !source) return null;
    if (task && task.status !== "running") return null;
    return (jobs || []).find((job) => job.status === "running"
        && (job.symbol || job.params?.symbol) === symbol
        && (task?.waitingForJob === job.job || (backtestRecordMatchesSource(job, source)
            && (!task || task.params.backtest_job === job.job || (task.version === job.version
                && backtestArgumentsKey(task.path, task.params)
                    === backtestArgumentsKey(job.path, job.params || {})))))) || null;
}

export function historicalBacktestStatuses(statuses, recent, source = null) {
    const merged = { ...statuses };
    for (const record of recent || []) {
        if (
            backtestRecordMatchesSource(record, source) &&
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

export function visibleWatchlistBacktestState(source, queueState, recent) {
    const sameSource = Boolean(source && queueState.source === source);
    const selected = sameSource
        ? queueState
        : { statuses: {}, ready: false, failures: {}, fillCounts: {}, returns: {} };
    return {
        ...selected,
        sameSource,
        statuses: source ? historicalBacktestStatuses(selected.statuses, recent, source) : selected.statuses,
    };
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

export function runningBacktestStatuses(statuses, jobs, { unavailable = false, source = null } = {}) {
    const merged = Object.fromEntries(
        Object.entries(statuses).map(([symbol, status]) => [
            symbol,
            unavailable && status === "running" ? "unknown" : status,
        ]),
    );
    for (const job of jobs || []) {
        if (!backtestRecordMatchesSource(job, source) || job?.status !== "running") continue;
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

/** analysis口径不包含取数；直接使用后端时钟，避免把取数等待显示为已运行0秒。 */
export function formatBacktestTiming(job) {
    if (job?.timing_scope !== "analysis") return formatBacktestElapsed(job?.elapsed_seconds);
    const fields = [["取数", job.data_request_seconds], ["分析", job.analysis_elapsed_seconds ?? job.elapsed_seconds], ["后端总计", job.total_elapsed_seconds]];
    return fields.filter(([, seconds]) => Number.isFinite(seconds) && seconds >= 0)
        .map(([label, seconds]) => label + " " + seconds.toFixed(3) + " 秒").join(" · ");
}

export function formatBacktestProgress(percent) {
    return Number.isInteger(percent) && percent >= 0 && percent <= 99 ? `${percent}%` : "";
}

export function backtestProgress(job) {
    if (Number.isSafeInteger(job?.processed_sessions) && Number.isSafeInteger(job?.total_sessions)
        && job.total_sessions > 0 && job.processed_sessions >= 0 && job.processed_sessions <= job.total_sessions) {
        const percent = job.processed_sessions === job.total_sessions ? 100
            : Math.min(99.9, Math.round(job.processed_sessions * 1000 / job.total_sessions) / 10);
        return { percent, scope: "sessions" };
    }
    const stage = typeof job?.progress_stage === "string" ? job.progress_stage : "";
    // 兼容正在运行的服务：日期计数已由后端上报，不能再套用总任务的阶段权重。
    const sessions = stage.match(/已处理\s*(\d+)\s*\/\s*(\d+)\s*个交易日/);
    if (sessions) {
        const processed = Number(sessions[1]);
        const total = Number(sessions[2]);
        if (!Number.isSafeInteger(processed) || !Number.isSafeInteger(total) || total <= 0 || processed > total)
            return null;
        const percent = processed === total ? 100 : Math.min(99.9, Math.round(processed * 1000 / total) / 10);
        return { percent, scope: "sessions" };
    }
    return formatBacktestProgress(job?.progress_percent) ? { percent: job.progress_percent, scope: "overall" } : null;
}

export function formatBacktestJobProgress(job) {
    const progress = backtestProgress(job);
    return progress ? `${progress.percent}%` : "";
}

export function formatBacktestStage(job) {
    const backendStage = typeof job?.progress_stage === "string" ? job.progress_stage.trim() : "";
    const detail = Number.isSafeInteger(job?.processed_sessions) && job.total_sessions > 0
        ? `已处理${job.processed_sessions}/${job.total_sessions}个交易日 · 截至${job.processed_asof}` : "";
    const stage = detail && /已处理\d+\/\d+个交易日/.test(backendStage)
        ? backendStage.replace(/已处理\d+\/\d+个交易日(?:\s*·\s*截至[^·]*)?/, detail)
        : [backendStage, detail].filter(Boolean).join(" · ");
    const progress = backtestProgress(job);
    const percent = progress ? `${progress.scope === "sessions" ? "交易日进度 " : ""}${progress.percent}%` : "";
    return [stage ? `当前步骤：${stage}` : "", percent].filter(Boolean).join(" · ");
}

export function formatCompletedBacktestElapsed(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    return `回测耗时 ${hours ? `${hours} 小时 ` : ""}${minutes ? `${minutes} 分钟 ` : ""}${remainder} 秒`;
}

export function completedBacktestElapsed(task, recent) {
    if (task?.status !== "completed") return "";
    const record = recent?.find((item) => item.status === "completed"
        && item.job === task.params.backtest_job && item.path === task.path
        && item.symbol === task.symbol && item.version === task.version
        && backtestArgumentsKey(item.path, item.params || {}) === backtestArgumentsKey(task.path, task.params));
    if (record?.timing_scope === "analysis") return formatBacktestTiming(record);
    if (task.result?.performance?.timing_scope === "analysis") return formatBacktestTiming(task.result.performance);
    return formatCompletedBacktestElapsed(record?.elapsed_seconds ?? task.elapsedSeconds);
}
