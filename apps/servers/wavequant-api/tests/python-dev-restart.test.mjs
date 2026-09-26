import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
    createPythonSourceMonitor,
    createPythonSupervisor,
    shouldAutoRestartPython,
} from "../scripts/python-dev-restart.mjs";

const longRunning = ["-m", "wavequant_api.cli", "--api-only", "--port", "9365"];

test("only local long-running Python services enable source reload", () => {
    assert.equal(shouldAutoRestartPython(longRunning, {}), true);
    assert.equal(shouldAutoRestartPython(["-m", "wavequant_api.cli", "--watch-signals"], {}), true);
    assert.equal(shouldAutoRestartPython(["-m", "wavequant_api.cli", "--watch-structures"], {}), true);
    assert.equal(shouldAutoRestartPython(["-m", "wavequant_api.cli", "--port", "8765"], {}), true);
    assert.equal(shouldAutoRestartPython(["-m", "wavequant_api.cli", "--init-database"], {}), false);
    assert.equal(shouldAutoRestartPython(["-m", "pytest"], {}), false);
    assert.equal(shouldAutoRestartPython(longRunning, { NODE_ENV: "production" }), false);
    assert.equal(shouldAutoRestartPython(longRunning, { NODE_ENV: "staging" }), false);
});

test("source monitor waits for stable Python changes and ignores generated files", () => {
    const root = mkdtempSync(join(tmpdir(), "wavequant-source-watch-"));
    const moduleDirectory = join(root, "wavequant");
    mkdirSync(moduleDirectory);
    const modulePath = join(moduleDirectory, "strategy.py");
    const changes = [];
    writeFileSync(modulePath, "version = 1\n");
    const monitor = createPythonSourceMonitor([root], (fingerprint) => changes.push(fingerprint));
    try {
        monitor.check();
        writeFileSync(join(moduleDirectory, "strategy.pyc"), "ignored");
        monitor.check();
        assert.equal(changes.length, 0);

        writeFileSync(modulePath, "version = 2\n");
        monitor.check();
        writeFileSync(modulePath, "version = 3\n");
        monitor.check();
        assert.equal(changes.length, 0);
        monitor.check();
        assert.equal(changes.length, 1);
        monitor.check();
        assert.equal(changes.length, 1);

        writeFileSync(join(moduleDirectory, "new_rule.py"), "enabled = True\n");
        monitor.check();
        monitor.check();
        assert.equal(changes.length, 2);
    } finally {
        monitor.stop();
        rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 50 });
    }
});

test("source edit replaces the owned service process and shutdown does not leave it running", async () => {
    const root = mkdtempSync(join(tmpdir(), "wavequant-process-watch-"));
    const modulePath = join(root, "strategy.py");
    const grandchildPidPath = join(root, "grandchild.pid");
    const serviceScript = `const {spawn}=require("node:child_process");const {writeFileSync}=require("node:fs");const child=spawn(process.execPath,["-e","setInterval(() => {}, 1000)"],{stdio:"ignore"});writeFileSync(${JSON.stringify(grandchildPidPath)},String(child.pid));setInterval(() => {}, 1000);`;
    writeFileSync(modulePath, "version = 1\n");
    const supervisor = createPythonSupervisor({
        executable: process.execPath,
        args: ["-e", serviceScript],
        cwd: root,
        sourceRoots: [root],
        pollIntervalMs: 30,
        restartDelayMs: 50,
        log: () => {},
    });
    try {
        supervisor.start();
        const originalPid = supervisor.pid;
        assert.ok(originalPid);
        await waitUntil(() => existsSync(grandchildPidPath), 5_000);
        const originalGrandchildPid = Number(readFileSync(grandchildPidPath, "utf8"));
        writeFileSync(modulePath, "version = 2\n");
        await waitUntil(() => supervisor.pid && supervisor.pid !== originalPid, 5_000);
        assert.equal(isRunning(originalPid), false);
        await waitUntil(() => !isRunning(originalGrandchildPid), 5_000);
        const replacementPid = supervisor.pid;
        await waitUntil(() => Number(readFileSync(grandchildPidPath, "utf8")) !== originalGrandchildPid, 5_000);
        const replacementGrandchildPid = Number(readFileSync(grandchildPidPath, "utf8"));
        await supervisor.stop();
        assert.equal(isRunning(replacementPid), false);
        assert.equal(isRunning(replacementGrandchildPid), false);
    } finally {
        await supervisor.stop();
        rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 50 });
    }
});

test("failed launch retries with a delay and stops retrying on shutdown", async () => {
    const root = mkdtempSync(join(tmpdir(), "wavequant-failed-launch-"));
    const starts = [];
    const supervisor = createPythonSupervisor({
        executable: join(root, "missing-python"),
        args: ["-m", "wavequant_api.cli", "--api-only"],
        cwd: root,
        sourceRoots: [root],
        pollIntervalMs: 30,
        restartDelayMs: 80,
        log: (message) => {
            if (message.includes("Python service started")) starts.push(Date.now());
        },
    });
    try {
        supervisor.start();
        await waitUntil(() => starts.length >= 2, 5_000);
        assert.ok(starts[1] - starts[0] >= 70);
        await supervisor.stop();
        const countAfterStop = starts.length;
        await new Promise((resolve) => setTimeout(resolve, 150));
        assert.equal(starts.length, countAfterStop);
    } finally {
        await supervisor.stop();
        rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 50 });
    }
});

function isRunning(pid) {
    try {
        process.kill(pid, 0);
        return true;
    } catch {
        return false;
    }
}

async function waitUntil(condition, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (!condition()) {
        if (Date.now() >= deadline) throw new Error("timed out waiting for Python service restart");
        await new Promise((resolve) => setTimeout(resolve, 25));
    }
}
