import assert from "node:assert/strict";
import test from "node:test";
import { selectedRunningBacktestJob } from "../public/backtest-job-status.js";
import { runStockBacktest } from "../public/stock-backtest-runner.js";

const params = { symbol: "sh.600825", asof: "2026-10-13" };
const completed = { status: "completed", version: "v1", path: "/api/akshare-backtest", symbol: params.symbol,
    params, job: "completed", result_valid: true, result_available: true };
const running = { ...completed, status: "running", job: "older", params: { ...params, asof: "2026-10-12" } };

test("completed chart cannot inherit another cutoff's progress", () => {
    assert.equal(selectedRunningBacktestJob([running], params.symbol, "akshare", completed), null);
    const task = { ...completed, status: "running", params: { ...params, backtest_job: "new" } };
    assert.equal(selectedRunningBacktestJob([running], params.symbol, "akshare", task), null);
    task.waitingForJob = running.job;
    assert.equal(selectedRunningBacktestJob([running], params.symbol, "akshare", task), running);
});

test("matching completed cache wins over a running job for another cutoff", async () => {
    const result = { symbol: params.symbol, asof: params.asof, result_scope: "stock", backtest: {} };
    const task = { ...completed, status: "running", params: { ...params, backtest_job: "new" } };
    let calls = 0;
    assert.equal(await runStockBacktest(task, {
        jobs: [running], recent: [completed], progress: () => {},
        api: async (path, query) => {
            calls++;
            assert.equal(path, "/api/backtest-job");
            assert.equal(query.job, completed.job);
            return { status: "completed", result };
        },
    }), result);
    assert.equal(calls, 1);
    assert.equal(task.params.backtest_job, completed.job);
});

test("old strategy completion is ignored instead of being adopted as the new result", async () => {
    const task = { ...completed, version: "v2", params: { ...params, backtest_job: "new" } };
    const result = { current: true };
    assert.equal(await runStockBacktest(task, {
        recent: [completed], progress: () => {},
        api: async (path) => { assert.equal(path, task.path); return result; },
    }), result);
});
