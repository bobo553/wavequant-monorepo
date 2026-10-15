import assert from "node:assert/strict";
import test from "node:test";

import {
    BACKTEST_STATUS_STALE_MS,
    backtestProgress,
    adoptServerBacktestHistory,
    expiredBacktestSnapshot,
    formatBacktestElapsed,
    formatBacktestProgress,
    formatBacktestStage,
    historicalBacktestStatuses,
    runningBacktestStatuses,
    selectedBacktestAction,
    selectedBacktestButton,
    selectedBacktestStatus,
    selectedRunningBacktestJob,
    visibleWatchlistBacktestState,
} from "../public/backtest-job-status.js";
import { StockBacktestTasks } from "../public/stock-backtest-tasks.js";
import { IdleWatchlistBacktests, watchlistBacktestRequest } from "../public/watchlist-backtest-queue.js";

test("selected stock exposes background progress before a page request is attached", () => {
    const running = { job: "jiuyang-running", symbol: "sz.002242", path: "/api/tdx-backtest", status: "running",
        processed_sessions: 850, total_sessions: 2118, processed_asof: "2021-07-01" };
    const jobs = [
        { ...running, job: "old-completed", status: "completed" },
        { ...running, job: "another-stock", symbol: "sh.600812" },
        running,
    ];
    const selected = selectedRunningBacktestJob(jobs, "sz.002242", "tdx");
    assert.equal(selected, running);
    assert.match(formatBacktestStage(selected), /已处理850\/2118个交易日/);
    assert.match(formatBacktestStage(selected), /截至2021-07-01/);
    assert.match(formatBacktestStage(selected), /交易日进度 40\.1%$/);
});

test("selected progress cannot leak from another source or survive a completed job", () => {
    const running = { job: "jiuyang-running", symbol: "sz.002242", path: "/api/tdx-backtest", status: "running" };
    assert.equal(selectedRunningBacktestJob([running], "sz.002242", "akshare"), null);
    assert.equal(selectedRunningBacktestJob([running], "sh.600812", "tdx"), null);
    assert.equal(selectedRunningBacktestJob([running], "sz.002242", null), null);
    assert.equal(selectedRunningBacktestJob([{ ...running, status: "completed" }], "sz.002242", "tdx"), null);
    assert.equal(selectedRunningBacktestJob([], "sz.002242", "tdx"), null);
});

test("progress presents the actual stage even while the overall percentage stays unchanged", () => {
    assert.equal(formatBacktestStage({ progress_stage: "一级趋势", progress_percent: 35 }), "当前步骤：一级趋势 · 35%");
    assert.equal(formatBacktestStage({ progress_stage: "二级趋势", progress_percent: 35 }), "当前步骤：二级趋势 · 35%");
    assert.equal(formatBacktestStage({ progress_stage: "读取行情", progress_percent: 0 }), "当前步骤：读取行情 · 0%");
    assert.equal(formatBacktestStage({ progress_stage: "  ", progress_percent: 100 }), "");
    assert.equal(formatBacktestStage({ progress_stage: null, progress_percent: "35" }), "");
    assert.equal(formatBacktestStage(null), "");
});

test("market progress uses processed sessions without the overall task weight", () => {
    const job = { progress_stage: "变化联动 · 已处理57/2118个交易日 · 截至2020-01-01", progress_percent: 35 };
    assert.deepEqual(backtestProgress(job), { percent: 2.7, scope: "sessions" });
    assert.match(formatBacktestStage(job), /交易日进度 2\.7%$/);
    assert.equal(backtestProgress({ ...job, progress_stage: "已处理2118/2118个交易日" }).percent, 100);
    assert.equal(backtestProgress({ ...job, progress_stage: "已处理99999/100000个交易日" }).percent, 99.9);
    assert.equal(backtestProgress({ ...job, progress_stage: "已处理57/0个交易日" }), null);
    assert.equal(backtestProgress({ ...job, progress_stage: "已处理58/57个交易日" }), null);
});

test("completed snapshot counters take precedence over ahead-of-chart stage text", () => {
    const job = { processed_sessions: 57, total_sessions: 2118, processed_asof: "2020-01-01",
        progress_stage: "已处理500/2118个交易日", progress_percent: 60 };
    assert.deepEqual(backtestProgress(job), { percent: 2.7, scope: "sessions" });
    assert.match(formatBacktestStage(job), /57\/2118/);
    assert.match(formatBacktestStage(job), /2020-01-01/);
    assert.doesNotMatch(formatBacktestStage(job), /500\/2118/);
});

test("selected update and failure cannot be masked by historical watchlist metadata", () => {
    assert.equal(selectedBacktestStatus({ fallback: "historical", requestStatus: "updating" }), "updating");
    assert.equal(selectedBacktestStatus({ fallback: "historical", requestStatus: "updating", taskStatus: "running" }), "running");
    assert.equal(selectedBacktestStatus({ fallback: "historical", requestStatus: "failed" }), "failed");
    assert.equal(selectedBacktestStatus({ fallback: "historical", taskStatus: "failed" }), "failed");
    assert.equal(selectedBacktestStatus({ fallback: "historical", requestStatus: "completed", taskStatus: "completed" }), "completed");
    assert.equal(selectedBacktestStatus({ fallback: "historical", taskStatus: "running", unavailable: true }), "unknown");
    assert.equal(selectedBacktestStatus({ fallback: "historical" }), "historical");
});

test("selecting a stale backtest starts a fresh calculation even from the symbol selector", () => {
    assert.deepEqual(selectedBacktestAction({ historical: true, canBacktest: true, autoBacktest: false }), {
        run: true,
        force: true,
        direct: false,
    });
    assert.deepEqual(selectedBacktestAction({ historical: true, canBacktest: false, autoBacktest: true }), {
        run: false,
        force: true,
        direct: false,
    });
});

test("selection keeps completed result reuse and ordinary selector browsing", () => {
    assert.deepEqual(selectedBacktestAction({ historical: false, canBacktest: true, autoBacktest: true }), {
        run: true,
        force: false,
        direct: false,
    });
    assert.deepEqual(selectedBacktestAction({ historical: false, canBacktest: true, autoBacktest: false }), {
        run: false,
        force: false,
        direct: false,
    });
    assert.deepEqual(
        selectedBacktestAction({ historical: false, canBacktest: true, autoBacktest: true, completed: true }),
        { run: true, force: false, direct: true },
    );
    assert.deepEqual(
        selectedBacktestAction({ historical: false, canBacktest: true, autoBacktest: false, completed: true }),
        { run: false, force: false, direct: true },
    );
});

test("a finished badge opens its result before offering a fresh calculation", () => {
    assert.deepEqual(selectedBacktestButton("completed", false), {
        label: "已完成 · 查看回测",
        force: false,
    });
    assert.deepEqual(selectedBacktestButton("completed", true), {
        label: "已完成 · 重新回测",
        force: true,
    });
    assert.deepEqual(selectedBacktestButton("failed", false), {
        label: "失败 · 重新回测",
        force: true,
    });
});

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

test("an older server completion is identified without claiming the current run finished", () => {
    const recent = [
        { status: "completed", symbol: "sz.300154", result_valid: true, version: "old" },
        { status: "completed", symbol: "sz.000001", result_valid: false },
    ];
    assert.deepEqual(historicalBacktestStatuses({}, recent), { "sz.300154": "historical" });
    assert.deepEqual(historicalBacktestStatuses({ "sz.300154": "running" }, recent), { "sz.300154": "running" });
});

test("backtest badges only use jobs from the selected market source", () => {
    const recent = [
        { path: "/api/tdx-backtest", status: "completed", symbol: "sz.000001", result_valid: true },
        { path: "/api/akshare-backtest", status: "completed", symbol: "sh.601086", result_valid: true },
    ];
    const jobs = [
        { path: "/api/tdx-backtest", status: "running", symbol: "sh.601086" },
        { path: "/api/akshare-backtest", status: "running", symbol: "sz.000001" },
    ];
    const akshare = runningBacktestStatuses(historicalBacktestStatuses({}, recent, "akshare"), jobs, {
        source: "akshare",
    });
    const tdx = runningBacktestStatuses(historicalBacktestStatuses({}, recent, "tdx"), jobs, {
        source: "tdx",
    });
    assert.deepEqual(akshare, { "sh.601086": "historical", "sz.000001": "running" });
    assert.deepEqual(tdx, { "sz.000001": "historical", "sh.601086": "running" });
});

test("a completed watchlist result stays completed across queue and server status refreshes", () => {
    const queueState = {
        source: "akshare",
        statuses: { "sh.601086": "completed" },
        ready: true,
        failures: {},
        fillCounts: { "sh.601086": 2 },
        returns: { "sh.601086": { rate: 0.1, amount: 10_000 } },
    };
    const recent = [{ path: "/api/akshare-backtest", symbol: "sh.601086", status: "completed", result_valid: true }];
    for (const [refresh, history] of [
        ["queue change", []],
        ["server poll", recent],
    ]) {
        const state = visibleWatchlistBacktestState("akshare", queueState, history);
        assert.equal(state.sameSource, true, refresh);
        assert.equal(state.statuses["sh.601086"], "completed", refresh);
        assert.equal(state.fillCounts["sh.601086"], 2, refresh);
    }
    const otherSource = visibleWatchlistBacktestState("tdx", queueState, recent);
    assert.equal(otherSource.sameSource, false);
    assert.equal(otherSource.statuses["sh.601086"], undefined);
});

test("an expired server copy cannot displace a completed local result or recurse through status updates", async () => {
    const context = {
        run: "example",
        variant: "lecture_v3",
        scenario: "base",
        source: "akshare",
        start: "2018-01-01",
        volume_filter: "false",
        net_reward_risk_filter: "false",
        shallow_base_breakout_enabled: "true",
        initial_capital: 100_000,
        max_position_weight: 1,
    };
    const member = { symbol: "sh.601086", name: "国芳集团", asof: "2026-09-24" };
    const request = watchlistBacktestRequest(member, context);
    const record = {
        ...request,
        symbol: member.symbol,
        job: "completed-job",
        version: "v1",
        status: "completed",
        result_valid: true,
        result_available: false,
    };
    const result = {
        symbol: member.symbol,
        asof: member.asof,
        result_scope: "stock",
        backtest: {},
        orders: [{ status: "filled" }],
        markers: [{ id: "fill-1", kind: "fill" }],
    };
    let queue;
    let updates = 0;
    const tasks = new StockBacktestTasks({ run: async () => result });
    const syncLocalResult = () => {
        for (const task of tasks.tasks.values()) {
            if (task.status === "completed" && queue.statuses.get(task.symbol) !== "completed")
                queue.adoptCompleted(task.path, task.params, task.result, task.version);
        }
    };
    queue = new IdleWatchlistBacktests({
        snapshot: () => ({ context, members: [member] }),
        version: async () => ({ version: "v1" }),
        run: async () => result,
        isIdle: () => false,
        onChange: () => {
            assert.ok(++updates < 10, "status updates must settle without recursion");
            if (adoptServerBacktestHistory(queue, [record], tasks)) return;
            syncLocalResult();
        },
    });
    queue.setEnabled(false);
    await queue.ensureVersion();
    assert.equal(queue.state().statuses[member.symbol], "historical");

    const task = tasks.adoptCompleted(request.path, request.params, result, {
        version: "v1",
        jobId: "local-job",
    });
    syncLocalResult();

    assert.equal(queue.state().statuses[member.symbol], "completed");
    assert.equal(queue.state().fillCounts[member.symbol], 1);
    assert.equal(queue.matchingJobId(request.path, request.params), "local-job");
    assert.deepEqual((await task.promise).markers, result.markers);
    assert.ok(updates < 10);
});

test("a failed status poll expires old server running badges without claiming spare capacity", () => {
    const snapshot = { active: 1, max_active: 4, jobs: [{ status: "running", symbol: "sz.300562" }] };
    assert.equal(expiredBacktestSnapshot(snapshot, 1_000, 1_000 + BACKTEST_STATUS_STALE_MS - 1), snapshot);
    assert.deepEqual(expiredBacktestSnapshot(snapshot, 1_000, 1_000 + BACKTEST_STATUS_STALE_MS), {
        active: null,
        max_active: 4,
        jobs: [],
        unavailable: true,
    });
    assert.deepEqual(
        runningBacktestStatuses({ "sz.300562": "running", "sz.000001": "completed" }, [], { unavailable: true }),
        { "sz.300562": "unknown", "sz.000001": "completed" },
    );
});

test("server elapsed time is readable and unavailable values are omitted", () => {
    assert.equal(formatBacktestElapsed(8.9), "已运行 8 秒");
    assert.equal(formatBacktestElapsed(125), "已运行 2 分钟");
    assert.equal(formatBacktestElapsed(3_661), "已运行 1 小时 1 分钟");
    assert.equal(formatBacktestElapsed(undefined), "");
    assert.equal(formatBacktestElapsed(-1), "");
});

test("running progress accepts only an actual server percentage", () => {
    assert.equal(formatBacktestProgress(0), "0%");
    assert.equal(formatBacktestProgress(87), "87%");
    assert.equal(formatBacktestProgress(undefined), "");
    assert.equal(formatBacktestProgress(100), "");
    assert.equal(formatBacktestProgress(42.5), "");
});
