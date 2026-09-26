/** Read a finished job without submitting another calculation. */
export async function reuseCompletedBacktest({ tasks, path, params, version = "", jobId, fetchJob }) {
    const cached = tasks.find(path, params, version);
    if (cached?.status === "completed" || cached?.status === "running") return cached;
    const response = await fetchJob(jobId);
    const result = response?.result;
    if (
        response?.status !== "completed" ||
        result?.symbol !== params.symbol ||
        result?.asof !== params.asof ||
        result?.result_scope !== "stock" ||
        !result?.backtest ||
        result.backtest.status === "data_unavailable"
    ) {
        const error = new Error("已完成的回测结果不可读取，请手动重新运行");
        error.code = "BACKTEST_COMPLETED_RESULT_UNAVAILABLE";
        throw error;
    }
    return tasks.adoptCompleted(path, params, result, { version, jobId });
}
