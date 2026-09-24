import assert from "node:assert/strict";
import test from "node:test";

import { isRecoverableBacktestError, retryBacktest } from "../public/backtest-retry.js";

test("only transient strategy reload and service errors are retried", () => {
    assert.equal(isRecoverableBacktestError(new Error("运行中策略代码已变更，请重启服务后重试")), true);
    assert.equal(isRecoverableBacktestError(new Error("服务暂时不可用（HTTP 500 Internal Server Error）")), true);
    assert.equal(isRecoverableBacktestError(new TypeError("Failed to fetch")), true);
    assert.equal(isRecoverableBacktestError(new Error("HTTP 400 invalid symbol")), false);
    assert.equal(isRecoverableBacktestError(new Error("首次回测计算超时")), false);
    assert.equal(
        isRecoverableBacktestError(Object.assign(new Error("回测计算仍未完成"), { code: "BACKTEST_TIMEOUT" })),
        true,
    );
    assert.equal(isRecoverableBacktestError(new DOMException("cancelled", "AbortError")), false);
});

test("a timed-out cold backtest rereads the same result once and remains cancellable", async () => {
    let requests = 0;
    const retries = [];
    const result = await retryBacktest(
        async () => {
            if (++requests === 1) throw Object.assign(new Error("still computing"), { code: "BACKTEST_TIMEOUT" });
            return { run_id: "completed-cache" };
        },
        { sleep: async () => {}, onRetry: (...args) => retries.push(args), timeoutRetryDelay: 1 },
    );
    assert.deepEqual(result, { run_id: "completed-cache" });
    assert.equal(requests, 2);
    assert.equal(retries[0][2].code, "BACKTEST_TIMEOUT");

    const controller = new AbortController();
    requests = 0;
    await assert.rejects(
        retryBacktest(
            async () => {
                requests++;
                throw Object.assign(new Error("still computing"), { code: "BACKTEST_TIMEOUT" });
            },
            { signal: controller.signal, sleep: async () => controller.abort(), timeoutRetryDelay: 1 },
        ),
        { name: "AbortError" },
    );
    assert.equal(requests, 1);
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
