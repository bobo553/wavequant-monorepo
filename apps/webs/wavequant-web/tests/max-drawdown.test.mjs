import assert from "node:assert/strict";
import test from "node:test";

import { drawdownLogicalRange, maxDrawdownInterval } from "../public/max-drawdown.js";

const curve = (values) =>
    values.map((value, index) => ({ time: `2026-01-${String(index + 2).padStart(2, "0")}`, value }));

test("locates the peak and trough that produce the deepest drawdown", () => {
    assert.deepEqual(maxDrawdownInterval(curve([1.1, 1.04, 1.2, 1.14, 0.9, 1.05]), "2026-01-01"), {
        from: "2026-01-04",
        to: "2026-01-06",
        fromIndex: 2,
        toIndex: 4,
        initialPeak: false,
        drawdown: -0.25,
    });
});

test("uses the backtest start when initial capital is the peak", () => {
    const interval = maxDrawdownInterval(curve([0.95, 0.91, 0.97]), "2026-01-01");
    assert.equal(interval.from, "2026-01-01");
    assert.equal(interval.to, "2026-01-03");
    assert.equal(interval.initialPeak, true);
    assert.deepEqual(drawdownLogicalRange(interval, 3), { from: 0, to: 3 });
});

test("returns no interval for a flat or rising curve", () => {
    assert.equal(maxDrawdownInterval(curve([1, 1, 1.02]), "2026-01-01"), null);
    assert.equal(maxDrawdownInterval([], "2026-01-01"), null);
});
