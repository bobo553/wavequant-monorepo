import assert from "node:assert/strict";
import test from "node:test";
import { createEngineReloadMonitor } from "../public/engine-reload.js";

test("settings changes and the initial engine do not reload; a new engine reloads once", async () => {
    let response = { engine_version: "old", version: "settings-a" };
    const reloads = [];
    const monitor = createEngineReloadMonitor({ readVersion: async () => response, onReload: (value) => reloads.push(value) });
    await monitor.check();
    response = { engine_version: "old", version: "settings-b" };
    await monitor.check();
    assert.deepEqual(reloads, []);
    response = { engine_version: "new", version: "settings-b" };
    await monitor.check();
    await monitor.check();
    assert.deepEqual(reloads, ["new"]);
    monitor.stop();
});

test("outage and invalid readiness responses retain the baseline until new engine is ready", async () => {
    let response = { engine_version: "old", version: "v1" };
    const reloads = [];
    const unavailable = [];
    const monitor = createEngineReloadMonitor({
        readVersion: async () => { if (response instanceof Error) throw response; return response; },
        onReload: (value) => reloads.push(value),
        onUnavailable: (error) => unavailable.push(error),
    });
    await monitor.check();
    response = Object.assign(new Error("reloading"), { httpStatus: 503 });
    await monitor.check();
    response = { engine_version: "new" };
    await monitor.check();
    assert.deepEqual(reloads, []);
    assert.equal(unavailable.length, 1);
    response = { engine_version: "old", version: "v1" };
    await monitor.check();
    assert.deepEqual(reloads, []);
    response = { engine_version: "new", version: "v2" };
    await monitor.check();
    assert.deepEqual(reloads, ["new"]);
    monitor.stop();
});

test("polling never overlaps and leaving the page cancels a late reload", async () => {
    let resolveRequest;
    let calls = 0;
    let requestSignal;
    const reloads = [];
    const monitor = createEngineReloadMonitor({
        readVersion: (signal) => {
            calls++;
            requestSignal = signal;
            return calls === 1 ? { engine_version: "old", version: "v1" }
                : new Promise((resolve) => { resolveRequest = resolve; });
        },
        onReload: (value) => reloads.push(value),
    });
    await monitor.check();
    const pending = monitor.check();
    await monitor.check();
    assert.equal(calls, 2);
    monitor.stop();
    assert.equal(requestSignal.aborted, true);
    resolveRequest({ engine_version: "new", version: "v2" });
    await pending;
    assert.deepEqual(reloads, []);
});
