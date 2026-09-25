import assert from "node:assert/strict";
import test from "node:test";

import { runningBacktestStatuses } from "../public/backtest-job-status.js";

test("server running jobs override stale local watchlist and stock badges", () => {
    const local = { "sz.000978": "failed", "sh.601086": "completed" };
    const jobs = [
        { status: "running", symbol: "sz.000978", job: "running-1" },
        { status: "running", params: { symbol: "sz.000002" }, job: "running-2" },
        { status: "completed", symbol: "sh.601086", job: "finished" },
    ];
    assert.deepEqual(runningBacktestStatuses(local, jobs), {
        "sz.000978": "running",
        "sh.601086": "completed",
        "sz.000002": "running",
    });
    assert.equal(local["sz.000978"], "failed");
});
