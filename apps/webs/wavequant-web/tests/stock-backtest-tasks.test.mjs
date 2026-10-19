import assert from "node:assert/strict";
import test from "node:test";

import { StockBacktestTasks } from "../public/stock-backtest-tasks.js";

test("selecting a failed task retries with a new job and shares the in-flight retry", async () => {
    let attempts = 0;
    let jobs = 0;
    const tasks = new StockBacktestTasks({
        createJobId: () => `job-${++jobs}`,
        run: async () => {
            if (++attempts === 1) throw new Error("service unavailable");
            return { symbol: "sz.000678" };
        },
    });
    const params = { symbol: "sz.000678", asof: "2026-10-12" };
    const failed = tasks.start("/api/tdx-backtest", params, { version: "v1" });
    await assert.rejects(failed.promise, /service unavailable/);
    const retry = tasks.start("/api/tdx-backtest", params, { version: "v1" });
    assert.notEqual(retry.params.backtest_job, failed.params.backtest_job);
    assert.equal(tasks.start("/api/tdx-backtest", params, { version: "v1", force: true }), retry);
    assert.equal((await retry.promise).symbol, params.symbol);
    assert.equal(attempts, 2);
});

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

test("one stock runs one chain and only the latest queued settings survive", async () => {
    const calls = [];
    const releases = [];
    const tasks = new StockBacktestTasks({
        run: (task) => {
            calls.push(task);
            return new Promise((resolve) => releases.push(() => resolve(task.params.backtest_job)));
        },
        createJobId: (() => {
            let index = 0;
            return () => `job-${++index}`;
        })(),
    });
    const params = { symbol: "sz.000001", asof: "2026-09-24", start: "2020-01-01" };
    const first = tasks.start("/api/tdx-backtest", params, { version: "v1" });
    await Promise.resolve();
    const changedDate = tasks.start("/api/tdx-backtest", { ...params, start: "2021-01-01" }, { version: "v1" });
    const changedStrategy = tasks.start("/api/tdx-backtest", params, { version: "v2" });
    await assert.rejects(changedDate.promise, { code: "BACKTEST_REQUEST_SUPERSEDED" });
    assert.equal(calls.length, 1);
    assert.equal(changedStrategy.queued, true);
    releases.shift()();
    assert.equal(await first.promise, "job-1");
    await new Promise(setImmediate);
    assert.equal(calls.length, 2);
    assert.equal(calls[1], changedStrategy);
    assert.equal(tasks.find("/api/tdx-backtest", params, "v1"), undefined);
    releases.shift()();
    assert.equal(await changedStrategy.promise, "job-3");
    assert.equal(tasks.start("/api/tdx-backtest", params, { version: "v2" }), changedStrategy);
    assert.equal(changedStrategy.params.strategy_version, "v2");
});

test("returning to active settings cancels an obsolete queued configuration", async () => {
    let release;
    let calls = 0;
    const tasks = new StockBacktestTasks({ run: () => (++calls, new Promise((resolve) => { release = resolve; })) });
    const params = { symbol: "sh.600825", asof: "2026-10-13" };
    const active = tasks.start("/api/akshare-backtest", params);
    await Promise.resolve();
    const queued = tasks.start("/api/tdx-backtest", params);
    assert.equal(tasks.start("/api/akshare-backtest", params), active);
    await assert.rejects(queued.promise, { code: "BACKTEST_REQUEST_SUPERSEDED" });
    release({ symbol: params.symbol });
    await active.promise;
    await new Promise(setImmediate);
    assert.equal(calls, 1);
    assert.equal(tasks.chains.size, 0);
});

test("engine invalidation preserves the running stock lock until completion", async () => {
    let release;
    const calls = [];
    const tasks = new StockBacktestTasks({ run: (task) => {
        calls.push(task.version);
        return task.version === "v1" ? new Promise((resolve) => { release = resolve; }) : {};
    } });
    const params = { symbol: "sh.600825", asof: "2026-10-13" };
    const first = tasks.start("/api/akshare-backtest", params, { version: "v1" });
    await Promise.resolve();
    tasks.invalidateCompleted();
    const next = tasks.start("/api/akshare-backtest", params, { version: "v2" });
    assert.deepEqual(calls, ["v1"]);
    release({});
    await first.promise;
    await next.promise;
    assert.deepEqual(calls, ["v1", "v2"]);
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
