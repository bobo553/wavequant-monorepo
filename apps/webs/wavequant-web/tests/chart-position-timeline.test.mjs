import assert from "node:assert/strict";
import test from "node:test";

import { chartPositionStart, chartPositionTimeline, chartPositionValue } from "../public/chart-position-timeline.js";

function fixture(processed = 6) {
    const market = Array.from({ length: 10 }, (_, index) => ({ time: `2026-01-${String(index + 1).padStart(2, "0")}` }));
    return { symbol: "sh.600812", timeframe: "1d", preview_job: "job", processed, total: 10,
        bars: market.slice(0, processed), market_bars: market, asof: market[processed - 1].time,
        backtest: { strategy: { buy_interval_level: 2 } },
        audit: [
            { event: "trading_interval_opened", timestamp: market[3].time, bar_index: 3,
                start_low_time: market[1].time, trend_level: 2, source_path: "p" },
            { event: "trading_interval_closed", timestamp: market[7].time, start_index: 3,
                trend_level: 2, source_path: "p", display_end_inclusive: true },
        ],
    };
}

test("completed trading days and known qualification share the full market axis", () => {
    const view = fixture();
    const timeline = chartPositionTimeline(view);
    assert.equal(timeline.progress, 60);
    assert.deepEqual(timeline.ranges, [{ start: "2026-01-02", end: "2026-01-06", left: 10, width: 50 }]);
    assert.equal(chartPositionTimeline(view), timeline, "panning reuses the same snapshot without rescanning audit");
    assert.deepEqual(chartPositionTimeline(fixture(3)).ranges, [], "an unconfirmed window must not appear");
    assert.equal(chartPositionTimeline(fixture(9)).ranges[0].width, 70, "closed interval does not continue into later history");
});

test("empty, pending, other level and completed snapshots reset the timeline correctly", () => {
    assert.equal(chartPositionTimeline(null).progress, 0);
    const view = fixture();
    const pending = chartPositionTimeline({ ...view, result_scope: "stock-pending", backtest: null });
    assert.equal(pending.progress, 0);
    assert.deepEqual(pending.ranges, []);
    assert.deepEqual(chartPositionTimeline({ ...view, backtest: { strategy: { buy_interval_level: 1 } } }).ranges, []);
    const completed = { ...view, preview_job: null, bars: view.market_bars, asof: view.market_bars.at(-1).time };
    assert.equal(chartPositionTimeline(completed).progress, 100);
    assert.equal(chartPositionTimeline(completed).ranges[0].width, 70);
});

test("slider locates a window center on the full history while keyboard navigation keeps its start", () => {
    const viewport = { from: 20, to: 60, span: 40, maxStart: 160 };
    const value = chartPositionValue(viewport, 200);
    assert.equal(value, 40.5);
    assert.equal(chartPositionStart(value, viewport), 20);
    assert.equal(chartPositionStart(0, viewport), 0);
    assert.equal(chartPositionStart(200, viewport), 160);
});
