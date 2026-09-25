import assert from "node:assert/strict";
import test from "node:test";

import { IdleWatchlistBacktests } from "../public/watchlist-backtest-queue.js";

const result = (member) => ({
    symbol: member.symbol,
    asof: member.asof,
    result_scope: "stock",
    backtest: { status: "complete" },
});

function queue(options = {}) {
    let now = 0;
    let idle = true;
    let snapshot = {
        context: { run: "example", variant: "lecture_v3", source: "akshare" },
        members: [
            { symbol: "sz.000002", name: "列表首位", asof: "2026-09-24" },
            { symbol: "sz.000001", name: "列表次位", asof: "2026-09-24" },
        ],
    };
    const calls = [];
    const controller = new IdleWatchlistBacktests({
        snapshot: () => snapshot,
        version: options.version || (async () => ({ version: "v1" })),
        run: options.run || (async (member) => (calls.push(member.symbol), result(member))),
        isIdle: () => idle,
        onChange: () => {},
        now: () => now,
        versionRefreshMs: 30_000,
    });
    return {
        controller,
        calls,
        setIdle: (value) => (idle = value),
        setSnapshot: (value) => (snapshot = value),
        advance: (ms) => (now += ms),
    };
}

test("idle queue follows visible row order and waits for foreground activity to end", async () => {
    let finishFirst;
    const first = new Promise((resolve) => (finishFirst = resolve));
    const calls = [];
    const subject = queue({
        run: async (member) => {
            calls.push(member.symbol);
            if (calls.length === 1) await first;
            return result(member);
        },
    });
    const running = subject.controller.tick();
    await Promise.resolve();
    assert.deepEqual(calls, ["sz.000002"]);
    subject.setIdle(false);
    finishFirst();
    await running;
    await subject.controller.tick();
    assert.deepEqual(calls, ["sz.000002"]);
    assert.equal(subject.controller.state().completed, 1);
    subject.setIdle(true);
    await subject.controller.tick();
    assert.deepEqual(calls, ["sz.000002", "sz.000001"]);
    assert.equal(subject.controller.state().completed, 2);
});

test("strategy fingerprint change invalidates finished stocks and backtests from the top again", async () => {
    let version = "v1";
    const subject = queue({ version: async () => ({ version }) });
    await subject.controller.tick();
    await subject.controller.tick();
    assert.equal(subject.controller.state().completed, 2);
    version = "v2";
    subject.advance(30_001);
    await subject.controller.tick();
    assert.deepEqual(subject.calls, ["sz.000002", "sz.000001", "sz.000002"]);
    assert.equal(subject.controller.state().completed, 1);
    assert.equal(subject.controller.state().version, "v2");
});

test("one failed stock is reported and the queue continues after a pause", async () => {
    const subject = queue({
        run: async (member) => {
            if (member.symbol === "sz.000002") throw new Error("分钟历史缺失");
            return result(member);
        },
    });
    await subject.controller.tick();
    assert.equal(subject.controller.state().failed, 1);
    assert.equal(subject.controller.state().failures["sz.000002"], "分钟历史缺失");
    subject.controller.setEnabled(false);
    await subject.controller.tick();
    assert.equal(subject.controller.state().completed, 0);
    subject.controller.setEnabled(true);
    await subject.controller.tick();
    assert.equal(subject.controller.state().completed, 1);
});

test("changing list membership preserves completed results for unchanged stocks", async () => {
    const subject = queue();
    await subject.controller.tick();
    subject.setSnapshot({
        context: { run: "example", variant: "lecture_v3", source: "akshare", group: "focus" },
        members: [
            { symbol: "sz.000001", name: "列表次位", asof: "2026-09-24" },
            { symbol: "sz.000002", name: "列表首位", asof: "2026-09-24" },
        ],
    });
    await subject.controller.tick();
    assert.deepEqual(subject.calls, ["sz.000002", "sz.000001"]);
    assert.equal(subject.controller.state().completed, 2);
});

test("context edits discard an old in-flight result instead of crediting the new strategy", async () => {
    let finishOld;
    const old = new Promise((resolve) => (finishOld = resolve));
    let requests = 0;
    const subject = queue({
        run: async (member) => {
            requests++;
            if (requests === 1) await old;
            return result(member);
        },
    });
    const running = subject.controller.tick();
    await Promise.resolve();
    subject.setSnapshot({
        context: { run: "example", variant: "lecture_v3_c50", source: "akshare" },
        members: [{ symbol: "sz.000002", name: "列表首位", asof: "2026-09-24" }],
    });
    await subject.controller.tick();
    assert.equal(subject.controller.state().draining, true);
    assert.equal(subject.controller.state().active, null);
    finishOld();
    await running;
    assert.equal(subject.controller.state().completed, 0);
    await subject.controller.tick();
    assert.equal(requests, 2);
    assert.equal(subject.controller.state().completed, 1);
});

test("changing strategy aborts the old client wait and starts the new queue", async () => {
    let attempts = 0;
    const subject = queue({
        run: (member, _snapshot, active) => {
            attempts++;
            if (attempts > 1) return Promise.resolve(result(member));
            return new Promise((_, reject) => {
                active.controller.signal.addEventListener("abort", () => reject(new DOMException("stale", "AbortError")), {
                    once: true,
                });
            });
        },
    });
    const old = subject.controller.tick();
    await Promise.resolve();
    subject.setSnapshot({
        context: { run: "example", variant: "lecture_v3_c50", source: "akshare" },
        members: [{ symbol: "sz.000001", name: "新策略首位", asof: "2026-09-24" }],
    });
    await subject.controller.tick();
    await old;
    await subject.controller.tick();
    assert.equal(attempts, 2);
    assert.equal(subject.controller.state().completed, 1);
});

test("switching data source aborts an old queued calculation before its first request", async () => {
    const submitted = [];
    const subject = queue({
        run: (member, { context }, active) => {
            if (context.source === "akshare") {
                active.queued = true;
                return new Promise((_, reject) => {
                    active.controller.signal.addEventListener("abort", () => reject(new DOMException("stale", "AbortError")), {
                        once: true,
                    });
                });
            }
            submitted.push(context.source);
            return Promise.resolve(result(member));
        },
    });
    const old = subject.controller.tick();
    await Promise.resolve();
    assert.equal(subject.controller.active?.queued, true);
    subject.setSnapshot({
        context: { run: "example", variant: "lecture_v3", source: "tdx" },
        members: [{ symbol: "sz.000001", name: "通达信首位", asof: "2026-09-24" }],
    });
    await subject.controller.tick();
    await old;
    await subject.controller.tick();
    assert.deepEqual(submitted, ["tdx"]);
    assert.equal(subject.controller.state().completed, 1);
});

test("a successful HTTP body without a matching stock backtest is reported as a failure", async () => {
    const subject = queue({ run: async () => ({ status: "ok" }) });
    await subject.controller.tick();
    assert.equal(subject.controller.state().failed, 1);
    assert.match(subject.controller.state().failures["sz.000002"], /回测结果/);
});

test("transient failure retries after the first pass and a matching manual request reuses its job", async () => {
    let attempts = 0;
    const subject = queue({
        run: async (member, _snapshot, active) => {
            const params = { symbol: member.symbol, asof: member.asof, backtest_job: `job-${++attempts}` };
            active.job = { path: "/api/akshare-backtest", params };
            if (member.symbol === "sz.000002" && attempts === 1) throw new TypeError("network reset");
            return result(member);
        },
    });
    await subject.controller.tick();
    assert.equal(subject.controller.state().retrying, 1);
    await subject.controller.tick();
    assert.equal(subject.controller.state().completed, 1);
    subject.advance(5_001);
    await subject.controller.tick();
    assert.equal(attempts, 3);
    assert.equal(subject.controller.state().completed, 2);
    assert.equal(subject.controller.state().retrying, 0);

    let release;
    const pending = new Promise((resolve) => (release = resolve));
    const activeSubject = queue({
        run: async (member, _snapshot, active) => {
            active.job = {
                path: "/api/akshare-backtest",
                params: { symbol: member.symbol, asof: member.asof, backtest_job: "shared-job" },
            };
            await pending;
            return result(member);
        },
    });
    const running = activeSubject.controller.tick();
    await Promise.resolve();
    assert.equal(
        activeSubject.controller.matchingJobId("/api/akshare-backtest", {
            asof: "2026-09-24",
            symbol: "sz.000002",
            backtest_job: "new-id",
        }),
        "shared-job",
    );
    assert.equal(
        activeSubject.controller.matchingJobId("/api/akshare-backtest", {
            asof: "2026-09-23",
            symbol: "sz.000002",
        }),
        null,
    );
    release();
    await running;
});

test("a completed stock keeps its job identity for opening the real chart result", async () => {
    const completed = [];
    const subject = queue({
        run: async (member, _snapshot, active) => {
            active.job = {
                path: "/api/akshare-backtest",
                params: { symbol: member.symbol, asof: member.asof, backtest_job: "completed-job" },
            };
            return { ...result(member), orders: [{ status: "filled" }] };
        },
    });
    subject.controller.onCompleted = (view, active) => completed.push([view.symbol, active.job.params.backtest_job]);
    await subject.controller.tick();
    assert.equal(subject.controller.hasCompleted("sz.000002", "akshare"), true);
    assert.equal(subject.controller.state().fillCounts["sz.000002"], 1);
    assert.equal(subject.controller.matchingJobId("/api/akshare-backtest", {
        symbol: "sz.000002", asof: "2026-09-24",
    }), "completed-job");
    assert.deepEqual(completed, [["sz.000002", "completed-job"]]);
    subject.setSnapshot({
        context: { run: "example", variant: "lecture_v3_c50", source: "akshare" },
        members: [{ symbol: "sz.000002", asof: "2026-09-24" }],
    });
    subject.controller.sync(subject.controller.snapshot());
    assert.equal(subject.controller.hasCompleted("sz.000002", "akshare"), false);
    assert.equal(subject.controller.matchingJobId("/api/akshare-backtest", {
        symbol: "sz.000002", asof: "2026-09-24",
    }), null);
});

test("version lookup failure blocks dispatch and retries after the refresh interval", async () => {
    let checks = 0;
    const subject = queue({
        version: async () => {
            if (++checks === 1) throw new Error("服务不可用");
            return { version: "v1" };
        },
    });
    await subject.controller.tick();
    await subject.controller.tick();
    assert.equal(checks, 1);
    assert.deepEqual(subject.calls, []);
    assert.match(subject.controller.state().error, /服务不可用/);
    subject.advance(30_001);
    await subject.controller.tick();
    assert.equal(checks, 2);
    assert.deepEqual(subject.calls, ["sz.000002"]);
});

test("capacity and same-stock rejection defer without marking a watchlist stock failed", async () => {
    let now = 0;
    let calls = 0;
    const rejected = [];
    const controller = new IdleWatchlistBacktests({
        snapshot: () => ({
            context: { run: "example", variant: "lecture_v3", source: "akshare" },
            members: [{ symbol: "sz.000978", asof: "2026-09-24" }],
        }),
        version: async () => ({ version: "v1" }),
        run: async (member) => {
            calls++;
            if (calls < 3) throw Object.assign(new Error("rejected"), {
                code: calls === 1 ? "BACKTEST_CAPACITY" : "BACKTEST_SYMBOL_RUNNING",
            });
            return result(member);
        },
        isIdle: () => true,
        onChange: () => {},
        onRejected: (error) => rejected.push(error.code),
        now: () => now,
    });
    await controller.tick();
    assert.equal(controller.state().failed, 0);
    assert.deepEqual(controller.state().statuses, {});
    await controller.tick();
    assert.equal(calls, 1);
    now += 5_000;
    await controller.tick();
    assert.equal(controller.state().failed, 0);
    now += 5_000;
    await controller.tick();
    assert.equal(controller.state().completed, 1);
    assert.deepEqual(rejected, ["BACKTEST_CAPACITY", "BACKTEST_SYMBOL_RUNNING"]);
});
