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
    assert.match(result[9].text, /失败 · 已保存部分数据/);
    assert.match(result[16].text, /等待 · 尚无缓存/);
    assert.deepEqual(pipelineRows({ schema_version: 1, steps: steps.slice(1) }), []);
    assert.deepEqual(pipelineRows({ schema_version: 1, steps: steps.map((step) => ({ ...step, id: 1 })) }), []);
});
