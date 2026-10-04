import assert from "node:assert/strict";
import test from "node:test";

import { hasCompletedVerificationEvidence } from "./completed-verification.mjs";

const passed = { command: "targeted check", status: "passed", summary: "定向检查通过。" };
const notRun = {
    command: "完整 Web 单测与浏览器验证",
    status: "not-run",
    summary: "按用户本轮开发验证节奏未运行，保留待验证记录。",
};

test("done preserves passed checks and explicitly documented checks that were not run", () => {
    assert.equal(hasCompletedVerificationEvidence([passed]), true);
    assert.equal(hasCompletedVerificationEvidence([passed, notRun]), true);
    assert.equal(hasCompletedVerificationEvidence([notRun, passed]), true);
});

test("done requires at least one actual passed check", () => {
    for (const verification of [undefined, null, {}, "passed", [], [notRun]]) {
        assert.equal(hasCompletedVerificationEvidence(verification), false);
    }
});

test("failed, pending and unknown check states block done even when another check passed", () => {
    for (const status of ["failed", "pending", "running", "skipped", "unknown", undefined]) {
        assert.equal(hasCompletedVerificationEvidence([passed, { ...notRun, status }]), false);
    }
});

test("a not-run check needs a nonempty reason instead of being disguised as passed", () => {
    for (const summary of [undefined, null, "", "   ", 42]) {
        assert.equal(hasCompletedVerificationEvidence([passed, { ...notRun, summary }]), false);
    }
});

test("malformed verification records block done without crashing", () => {
    for (const item of [null, undefined, {}, "passed", 42]) {
        assert.equal(hasCompletedVerificationEvidence([passed, item]), false);
    }
});
