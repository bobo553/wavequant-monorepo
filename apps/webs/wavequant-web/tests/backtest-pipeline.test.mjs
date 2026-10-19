import assert from "node:assert/strict";
import test from "node:test";
import { pipelineRows } from "../public/backtest-pipeline.js";

test("24 steps keep empty completed data distinct from pending and failed cache writes", () => {
    const steps = Array.from({ length: 24 }, (_, index) => ({
        id: index + 1, title: `步骤${index + 1}`, status: "pending", cache: "none", rows: 0,
    }));
    Object.assign(steps[3], { status: "completed", cache: "stored" });
    Object.assign(steps[9], { status: "failed", cache: "partial" });
    const result = pipelineRows({ schema_version: 1, steps });
    assert.equal(result.length, 24);
    assert.match(result[3].text, /完成 · 已落盘 · 0 条/);
    assert.match(result[9].text, /失败 · 缓存不完整/);
    assert.match(result[16].text, /等待 · 尚无缓存/);
    assert.deepEqual(pipelineRows({ schema_version: 1, steps: steps.slice(1) }), []);
    assert.deepEqual(pipelineRows({ schema_version: 1, steps: steps.map((step) => ({ ...step, id: 1 })) }), []);
});

test("shared spans cannot be mistaken for measured computation or geometric point counts", () => {
    const steps = Array.from({ length: 24 }, (_, index) => ({
        id: index + 1, title: `步骤${index + 1}`, status: "running", cache: "buffering", rows: 39,
        elapsed_seconds: 1763.4,
    }));
    Object.assign(steps[16], { encoding_seconds: 2.25, write_seconds: 0.5,
        encoded_bytes: 2 * 1024 * 1024, reused_bytes: 3 * 1024 * 1024 });
    const result = pipelineRows({ schema_version: 1, steps });
    assert.match(result[16].text, /39 个变化版本 · 共享跨度 1763.4 秒/);
    assert.match(result[16].text, /累计编码 2.25 秒 · 累计写入 0.50 秒/);
    assert.match(result[16].text, /编码内容 2.00 MiB · 复用编码 3.00 MiB/);
    assert.match(result[17].text, /39 条六态事件/);
    assert.doesNotMatch(result[17].text, /累计编码|累计写入/);
});
