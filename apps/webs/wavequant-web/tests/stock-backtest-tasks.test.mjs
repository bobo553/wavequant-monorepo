import assert from "node:assert/strict";
import test from "node:test";

import { StockBacktestTasks } from "../public/stock-backtest-tasks.js";

test("switching stocks retains each calculation and returning reuses its task", async () => {
    const pending = new Map();
    const calls = [];
    const tasks = new StockBacktestTasks({
        createJobId: () => `job-${calls.length + 1}`,
        run: (task) => {
            calls.push(task);
            return new Promise((resolve) => pending.set(task.symbol, resolve));
        },
    });
    const a = { symbol: "sz.000001", asof: "2026-09-24", start: "2020-01-01" };
    const b = { ...a, symbol: "sz.000002" };
    const first = tasks.start("/api/tdx-backtest", a);
    const second = tasks.start("/api/tdx-backtest", b);
    await Promise.resolve();
    assert.equal(tasks.start("/api/tdx-backtest", a), first);
    assert.equal(first.status, "running");
    assert.equal(second.status, "running");
    assert.equal(calls.length, 2);
    pending.get(a.symbol)({ symbol: a.symbol });
    assert.equal((await first.promise).symbol, a.symbol);
    assert.equal(tasks.start("/api/tdx-backtest", a), first);
    assert.equal(tasks.start("/api/tdx-backtest", a, { force: true }).params.backtest_job, "job-3");
    pending.get(b.symbol)({ symbol: b.symbol });
    await second.promise;
});

test("changing parameters or strategy version creates a fresh calculation", async () => {
    const tasks = new StockBacktestTasks({
        run: async (task) => task.params.backtest_job,
        createJobId: (() => {
            let index = 0;
            return () => `job-${++index}`;
        })(),
    });
    const params = { symbol: "sz.000001", asof: "2026-09-24", start: "2020-01-01" };
    const first = tasks.start("/api/tdx-backtest", params, { version: "v1" });
    const changedDate = tasks.start("/api/tdx-backtest", { ...params, start: "2021-01-01" }, { version: "v1" });
    const changedStrategy = tasks.start("/api/tdx-backtest", params, { version: "v2" });
    assert.notEqual(first, changedDate);
    assert.notEqual(first, changedStrategy);
    assert.deepEqual(await Promise.all([first.promise, changedDate.promise, changedStrategy.promise]), [
        "job-1",
        "job-2",
        "job-3",
    ]);
});

test("a selected automatic result is adopted without starting a second calculation", async () => {
    let requests = 0;
    const tasks = new StockBacktestTasks({ run: async () => (++requests, {}), createJobId: () => "new-job" });
    const params = { symbol: "sz.000001", asof: "2026-09-24", start: "2020-01-01" };
    const result = { symbol: params.symbol, orders: [{ status: "filled" }] };
    const adopted = tasks.adoptCompleted("/api/akshare-backtest", params, result, { version: "v1", jobId: "auto-job" });
    assert.equal(tasks.start("/api/akshare-backtest", params, { version: "v1" }), adopted);
    assert.equal((await adopted.promise).orders.length, 1);
    assert.equal(requests, 0);
    const fresh = tasks.start("/api/akshare-backtest", params, { version: "v2" });
    assert.equal(fresh.params.backtest_job, "new-job");
    await fresh.promise;
    assert.equal(requests, 1);
});
