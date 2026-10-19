import assert from "node:assert/strict";
import test from "node:test";
import { EligibilityBoundaryOverlay, eligibilityBoundaryRanges } from "../public/eligibility-boundary-overlay.js";

const dates = ["2019-03-04", "2019-08-06", "2019-08-20", "2020-10-23", "2020-10-26"];
function fixture(level = 2) {
    return { symbol: "sh.600812", timeframe: "1d", asof: dates.at(-1),
        bars: dates.map((time) => ({ time })), backtest: { strategy: { buy_interval_level: level } },
        audit: [
            { event: "trend_lifecycle_state", timestamp: dates[0], state: "空翻多", trend_level: level },
            { event: "trading_interval_opened", timestamp: dates[2], bar_index: 397,
                start_low_time: dates[1], trend_level: level, source_path: "p" },
            { event: "trading_interval_closed", timestamp: dates[4], start_index: 397,
                trend_level: level, source_path: "p", display_end_inclusive: false },
        ] };
}
for (const level of [1, 2, 3]) {
    test(`level ${level}: blue columns mark permission clocks, not flip or structural low`, () => {
        const view = fixture(level);
        assert.deepEqual(eligibilityBoundaryRanges(view).map((range) => [range.start, range.kinds]), [
            [dates[2], ["交易资格确认日"]], [dates[4], ["交易资格结束日"]],
        ]);
        assert.deepEqual(eligibilityBoundaryRanges(view, dates[1]), []);
        const active = eligibilityBoundaryRanges(view, dates[3]);
        assert.equal(active.length, 1);
        assert.equal(active[0].from, 2);
        assert.equal(active[0].to, 2);
        const overlay = new EligibilityBoundaryOverlay();
        overlay.setData(view);
        assert.equal(overlay.labelAt(4), "交易资格结束日");
        assert.equal(overlay.contains(0), false);
        assert.equal(overlay.contains(1), false);
        // 结束当日无买入资格，仍须标记真实关闭日。
        assert.equal(overlay.contains(4), true);
    });
}
test("selection, causal cutoff and cropped history stay aligned with event dates", () => {
    const view = fixture();
    assert.deepEqual(eligibilityBoundaryRanges({ ...view, backtest: { strategy: { buy_interval_level: 1 } } }), []);
    assert.deepEqual(eligibilityBoundaryRanges({ ...view, symbol: "sz.000678",
        audit: view.audit.map((event) => ({ ...event, symbol: view.symbol })) }), []);
    assert.deepEqual(eligibilityBoundaryRanges({ ...view, timeframe: "1m" }), []);
    const cropped = eligibilityBoundaryRanges({ ...view, bars: view.bars.slice(3) });
    assert.equal(cropped.length, 1);
    assert.equal(cropped[0].from, 1);
    assert.equal(cropped[0].start, dates[4]);
    assert.deepEqual(eligibilityBoundaryRanges({ ...view, audit: [view.audit[2]] }), []);
});
test("duplicate paths and same-day reopen share one column; an open tail invents no end", () => {
    const view = fixture();
    view.audit.push(...view.audit.slice(1).map((event) => ({ ...event, source_path: "other" })));
    view.audit.push({ ...view.audit[1], timestamp: dates[4], bar_index: 600 });
    const ranges = eligibilityBoundaryRanges(view);
    assert.equal(ranges.length, 2);
    assert.deepEqual(ranges[1].kinds, ["交易资格确认日", "交易资格结束日"]);
    assert.equal(eligibilityBoundaryRanges({ ...view, audit: [view.audit[1]] }).length, 1);
});
