import assert from "node:assert/strict";
import test from "node:test";

import { isRecoverableBacktestError, retryBacktest, waitForBacktestJob } from "../public/backtest-retry.js";

test("only transient strategy reload and service errors are retried", () => {
    assert.equal(isRecoverableBacktestError(new Error("运行中策略代码已变更，请重启服务后重试")), true);
    assert.equal(isRecoverableBacktestError(new Error("服务暂时不可用（HTTP 500 Internal Server Error）")), true);
    assert.equal(isRecoverableBacktestError(new TypeError("Failed to fetch")), true);
    assert.equal(isRecoverableBacktestError(Object.assign(new Error("service busy"), { httpStatus: 503 })), true);
    assert.equal(isRecoverableBacktestError(Object.assign(new Error("at capacity"), { httpStatus: 503, code: "BACKTEST_CAPACITY" })), false);
    assert.equal(isRecoverableBacktestError(Object.assign(new Error("stock busy"), { httpStatus: 409, code: "BACKTEST_SYMBOL_RUNNING" })), false);
    assert.equal(isRecoverableBacktestError(new Error("HTTP 400 invalid symbol")), false);
    assert.equal(isRecoverableBacktestError(new Error("首次回测计算超时")), false);
    assert.equal(
        isRecoverableBacktestError(Object.assign(new Error("回测计算仍未完成"), { code: "BACKTEST_TIMEOUT" })),
        false,
    );
    assert.equal(
        isRecoverableBacktestError(Object.assign(new Error("failed"), { code: "BACKTEST_JOB_FAILED" })),
        false,
    );
    assert.equal(isRecoverableBacktestError(new DOMException("cancelled", "AbortError")), false);
});

test("a timed-out cold backtest switches to its job instead of repeating the long request", async () => {
    let requests = 0;
    let polls = 0;
    const result = await retryBacktest(
        async () => {
            requests++;
            throw Object.assign(new Error("still computing"), { code: "BACKTEST_TIMEOUT" });
        },
        {
            onTimeout: () =>
                waitForBacktestJob(
                    async () =>
                        ++polls === 1
                            ? { status: "running" }
                            : { status: "completed", result: { run_id: "completed-cache" } },
                    async () => {
                        throw new Error("must not resubmit while job exists");
                    },
                    { sleep: async () => {} },
                ),
        },
    );
    assert.deepEqual(result, { run_id: "completed-cache" });
    assert.equal(requests, 1);
    assert.equal(polls, 2);

    const controller = new AbortController();
    polls = 0;
    await assert.rejects(
        waitForBacktestJob(
            async () => {
                polls++;
                return { status: "running" };
            },
            async () => {
                throw new Error("must not resubmit");
            },
            { signal: controller.signal, sleep: async () => controller.abort() },
        ),
        { name: "AbortError" },
    );
    assert.equal(polls, 1);
});

test("a lost job resubmits the same request once and resumes polling", async () => {
    let polls = 0;
    let resubmits = 0;
    const view = await waitForBacktestJob(
        async () => {
            polls++;
            if (polls === 1) throw Object.assign(new Error("unknown job"), { httpStatus: 404 });
            return { status: "completed", result: { run_id: "restored" } };
        },
        async () => {
            resubmits++;
            throw Object.assign(new Error("still computing"), { code: "BACKTEST_TIMEOUT" });
        },
        { sleep: async () => {} },
    );
    assert.deepEqual(view, { run_id: "restored" });
    assert.equal(polls, 2);
    assert.equal(resubmits, 1);

    await assert.rejects(
        waitForBacktestJob(
            async () => {
                throw Object.assign(new Error("unknown job"), { httpStatus: 404 });
            },
            async () => {
                throw Object.assign(new Error("still computing"), { code: "BACKTEST_TIMEOUT" });
            },
            { sleep: async () => {} },
        ),
        { code: "BACKTEST_JOB_MISSING" },
    );
});

test("a failed job terminates with the server's safe error", async () => {
    await assert.rejects(
        waitForBacktestJob(
            async () => ({ status: "failed", error: "复权因子不可用", http_status: 422 }),
            async () => {
                throw new Error("must not resubmit failed job");
            },
            { sleep: async () => {} },
        ),
        { code: "BACKTEST_JOB_FAILED", message: "复权因子不可用", httpStatus: 422 },
    );
});

test("a running job stops polling after the wait budget without resubmitting", async () => {
    let clock = 0;
    let polls = 0;
    await assert.rejects(
        waitForBacktestJob(
            async () => {
                polls++;
                return { status: "running" };
            },
            async () => {
                throw new Error("must not resubmit a running job");
            },
            { now: () => clock, maxWaitMs: 10, sleep: async () => (clock += 5) },
        ),
        { code: "BACKTEST_JOB_WAIT_EXPIRED" },
    );
    assert.equal(polls, 2);
});

test("current-stock backtest waits through a restart and returns the new result", async () => {
    let requests = 0;
    const delays = [];
    const retryCounts = [];
    const result = await retryBacktest(
        async () => {
            requests++;
            if (requests === 1) throw new Error("运行中策略代码已变更，请重启服务后重试");
            if (requests === 2) throw new TypeError("Failed to fetch");
            return { run_id: "new-engine" };
        },
        {
            delays: [100, 200],
            sleep: async (delay) => delays.push(delay),
            onRetry: (attempt) => retryCounts.push(attempt),
        },
    );
    assert.deepEqual(result, { run_id: "new-engine" });
    assert.equal(requests, 3);
    assert.deepEqual(delays, [100, 200]);
    assert.deepEqual(retryCounts, [1, 2]);
});

test("retry is bounded and switching stocks cancels the waiting request", async () => {
    let requests = 0;
    await assert.rejects(
        retryBacktest(
            async () => {
                requests++;
                throw new Error("HTTP 503");
            },
            { delays: [1, 1], sleep: async () => {} },
        ),
        /HTTP 503/,
    );
    assert.equal(requests, 3);

    const controller = new AbortController();
    requests = 0;
    await assert.rejects(
        retryBacktest(
            async () => {
                requests++;
                throw new TypeError("Failed to fetch");
            },
            {
                signal: controller.signal,
                delays: [100],
                sleep: async () => controller.abort(),
            },
        ),
        { name: "AbortError" },
    );
    assert.equal(requests, 1);
});
