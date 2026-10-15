import assert from "node:assert/strict";
import test from "node:test";

import { runStockBacktest } from "../public/stock-backtest-runner.js";

const taskFor = () => ({ path: "/api/akshare-backtest", symbol: "sh.600812", version: "v1",
    params: { symbol: "sh.600812", asof: "2026-10-09", buy_interval_level: "2", backtest_job: "requested" } });
const busy = (same_request, running_job = "existing") => Object.assign(new Error("stock already has a running backtest"),
    { code: "BACKTEST_SYMBOL_RUNNING", same_request, running_job });
const options = { progress: () => {}, sleep: async () => {} };

test("same-request collision follows the server job and never submits twice", async () => {
    const task = taskFor();
    let submissions = 0;
    let polls = 0;
    const result = { symbol: task.symbol, run_id: "shared" };
    assert.equal(await runStockBacktest(task, { ...options, api: async (path, params) => {
        if (path === task.path) { submissions++; throw busy(true); }
        assert.equal(params.job, "existing");
        return ++polls === 1 ? { status: "running" } : { status: "completed", result };
    } }), result);
    assert.equal(submissions, 1);
    assert.equal(task.params.backtest_job, "existing");
});

test("a known identical job is followed before sending any calculation request", async () => {
    const task = taskFor();
    const result = { run_id: "shared" };
    assert.equal(await runStockBacktest(task, { ...options,
        jobs: [{ job: "existing", symbol: task.symbol, path: task.path, params: task.params,
            version: task.version, status: "running" }],
        api: async (path, params) => {
            assert.equal(path, "/api/backtest-job");
            assert.equal(params.job, "existing");
            return { status: "completed", result };
        },
    }), result);
});

test("different settings wait for release then submit the unchanged requested settings", async () => {
    const task = taskFor();
    const params = { ...task.params };
    let submissions = 0;
    let polls = 0;
    const result = { run_id: "current-settings" };
    const messages = [];
    assert.equal(await runStockBacktest(task, { ...options, progress: (message) => messages.push(message),
        api: async (path, request) => {
            if (path === task.path) {
                assert.deepEqual(request, params);
                if (++submissions === 1) throw busy(false);
                assert.equal(polls, 2);
                return result;
            }
            assert.equal(task.waitingForJob, "existing");
            assert.equal(request.job, "existing");
            return ++polls === 1 ? { status: "running" }
                : { status: "completed", result: { run_id: "wrong-settings" } };
        },
    }), result);
    assert.equal(submissions, 2);
    assert.equal(task.waitingForJob, null);
    assert.ok(messages.some((message) => message.includes("等待")));
});

for (const ending of ["failed", "missing"]) {
    test(`a blocking job that is ${ending} does not fail the new settings`, async () => {
        const task = taskFor();
        let submissions = 0;
        const result = { run_id: "current-settings" };
        assert.equal(await runStockBacktest(task, { ...options, api: async (path) => {
            if (path === task.path) {
                if (++submissions === 1) throw busy(false);
                return result;
            }
            if (ending === "missing") throw Object.assign(new Error("gone"), { httpStatus: 404 });
            return { status: "failed", error: "old settings failed" };
        } }), result);
    });
}

test("another stock job claiming the released slot is handled without adopting its settings", async () => {
    const task = taskFor();
    let submissions = 0;
    const followed = [];
    assert.deepEqual(await runStockBacktest(task, { ...options, api: async (path, params) => {
        if (path === task.path) {
            if (++submissions <= 2) throw busy(false, `existing-${submissions}`);
            return { run_id: "current-settings" };
        }
        followed.push(params.job);
        return { status: "completed", result: { run_id: params.job } };
    } }), { run_id: "current-settings" });
    assert.deepEqual(followed, ["existing-1", "existing-2"]);
    assert.equal(task.params.backtest_job, "requested");
});

test("a long request polls its own task and propagates a genuine calculation failure", async () => {
    const task = taskFor();
    let submissions = 0;
    await assert.rejects(runStockBacktest(task, { ...options, api: async (path, params) => {
        if (path === task.path) {
            submissions++;
            throw Object.assign(new Error("timeout"), { code: "BACKTEST_TIMEOUT" });
        }
        assert.equal(params.job, "requested");
        return { status: "failed", error: "复权数据缺失" };
    } }), { code: "BACKTEST_JOB_FAILED", message: "复权数据缺失" });
    assert.equal(submissions, 1);
});

test("a lost active task preserves the failure instead of submitting a new calculation", async () => {
    const task = taskFor();
    let submissions = 0;
    await assert.rejects(runStockBacktest(task, { ...options, api: async (path) => {
        if (path === task.path) {
            submissions++;
            throw Object.assign(new Error("running"), { code: "BACKTEST_TIMEOUT" });
        }
        throw Object.assign(new Error("gone"), { httpStatus: 404 });
    } }), { code: "BACKTEST_JOB_MISSING" });
    assert.equal(submissions, 1);
});

test("waiting is cancellable and has one deadline across collisions", async () => {
    const controller = new AbortController();
    let clock = 0;
    const api = async (path) => {
        if (path === "/api/akshare-backtest") throw busy(false);
        return { status: "running" };
    };
    await assert.rejects(runStockBacktest(taskFor(), { ...options, api, signal: controller.signal,
        sleep: async () => controller.abort() }), { name: "AbortError" });
    await assert.rejects(runStockBacktest(taskFor(), { ...options, api, now: () => clock, maxWaitMs: 10,
        sleep: async () => { clock += 5; } }), { code: "BACKTEST_JOB_WAIT_EXPIRED" });
});
