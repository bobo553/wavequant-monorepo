import assert from "node:assert/strict";
import test from "node:test";

import { reuseCompletedBacktest } from "../public/completed-backtest-result.js";
import { StockBacktestTasks } from "../public/stock-backtest-tasks.js";

test("selecting a completed stock reads its existing job without submitting a new backtest", async () => {
    let calculations = 0;
    const tasks = new StockBacktestTasks({ run: async () => (++calculations, {}) });
    const params = { symbol: "sh.601086", asof: "2026-09-24", start: "2018-01-02" };
    const result = { symbol: params.symbol, asof: params.asof, result_scope: "stock", backtest: {} };
    const requested = [];
    const selected = await reuseCompletedBacktest({
        tasks,
        path: "/api/akshare-backtest",
        params,
        version: "v1",
        jobId: "finished-job",
        fetchJob: async (job) => (requested.push(job), { status: "completed", result }),
    });
    assert.equal(selected.status, "completed");
    assert.deepEqual(requested, ["finished-job"]);
    assert.equal(calculations, 0);
    assert.equal(await selected.promise, result);
    assert.equal(
        await reuseCompletedBacktest({
            tasks,
            path: "/api/akshare-backtest",
            params,
            version: "v1",
            jobId: "finished-job",
            fetchJob: async () => { throw new Error("already cached"); },
        }),
        selected,
    );
});

test("an expired or mismatched completed job cannot be shown as a current result", async () => {
    const tasks = new StockBacktestTasks({ run: async () => ({}) });
    const params = { symbol: "sh.601086", asof: "2026-09-24" };
    const request = {
        tasks,
        path: "/api/akshare-backtest",
        params,
        version: "v1",
        jobId: "finished-job",
    };
    await assert.rejects(
        reuseCompletedBacktest({ ...request, fetchJob: async () => ({ status: "completed", result: { symbol: params.symbol, asof: "2026-09-23", result_scope: "stock", backtest: {} } }) }),
        { code: "BACKTEST_COMPLETED_RESULT_UNAVAILABLE" },
    );
    assert.equal(tasks.tasks.size, 0);
});
