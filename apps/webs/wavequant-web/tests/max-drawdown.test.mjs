import assert from "node:assert/strict";
import test from "node:test";

import { drawdownCandleRange, maxDrawdownInterval } from "../public/max-drawdown.js";

const curve = (values) =>
    values.map((value, index) => ({ time: `2026-01-${String(index + 2).padStart(2, "0")}`, value }));

test("locates the peak and trough that produce the deepest drawdown", () => {
    assert.deepEqual(maxDrawdownInterval(curve([1.1, 1.04, 1.2, 1.14, 0.9, 1.05]), "2026-01-01"), {
        from: "2026-01-04",
        to: "2026-01-06",
        initialPeak: false,
        drawdown: -0.25,
    });
});

test("uses the backtest start when initial capital is the peak", () => {
    const interval = maxDrawdownInterval(curve([0.95, 0.91, 0.97]), "2026-01-01");
    assert.equal(interval.from, "2026-01-01");
    assert.equal(interval.to, "2026-01-03");
    assert.equal(interval.initialPeak, true);
    assert.deepEqual(drawdownCandleRange(interval, curve([1, 1, 1])), { from: 0, to: 4 });
});

test("locates the date interval in K-line bars even when trading dates differ from curve indices", () => {
    const bars = [2, 3, 6, 9, 12, 15, 18, 21, 24].map((day) => ({
        time: `2026-01-${String(day).padStart(2, "0")}`,
    }));
    assert.deepEqual(drawdownCandleRange({ from: "2026-01-11", to: "2026-01-13" }, bars), {
        from: 1,
        to: 7,
    });
    assert.equal(drawdownCandleRange({ from: "2026-01-10", to: "2026-01-11" }, bars), null);
});

test("returns no interval for a flat or rising curve", () => {
    assert.equal(maxDrawdownInterval(curve([1, 1, 1.02]), "2026-01-01"), null);
    assert.equal(maxDrawdownInterval([], "2026-01-01"), null);
});
