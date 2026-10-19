import assert from "node:assert/strict";
import test from "node:test";

import { backtestPreviewView } from "../public/backtest-preview.js";
import { chartMarketBars } from "../public/chart-market-bars.js";

function fixture() {
    return { job: "job-1", revision: 1, preview: {
        symbol: "sz.000678", asof: "2026-01-01", timeframe: "1d", processed: 1, total: 10,
        bars: [{ time: "2026-01-01", open: 10, high: 12, low: 9, close: 11, volume: 100, factor: 1, raw_close: 11 }],
        theory: { asof: "2026-01-01", shapes: [], events: [] },
        phase: "daily", markers: [], signals: [], orders: [], trades: [], audit: [],
        curve: [{ timestamp: "2026-01-01T00:00:00", equity: 100 }],
        metrics: {}, backtest: { status: "running" },
    } };
}
const context = { job: "job-1", symbol: "sz.000678", source: "tdx" };

test("full market candles do not advance the decision clock or allow future fills", () => {
    const payload = fixture();
    const preview = payload.preview;
    preview.market_bars = Array.from({ length: 10 }, (_, index) => ({ ...preview.bars[0],
        time: `2026-01-${String(index + 1).padStart(2, "0")}` }));
    preview.market_asof = "2026-01-10";
    const view = backtestPreviewView(payload, context);
    assert.equal(chartMarketBars(view).length, 10);
    assert.equal(view.bars.length, 1);
    assert.equal(view.asof, "2026-01-01");
    assert.equal(view.theory.asof, view.asof);
    assert.equal(view.processed, 1);
    preview.markers.push({ time: "2026-01-02", kind: "fill", side: "BUY" });
    assert.equal(backtestPreviewView(payload, context), null);
    preview.markers = [];
    preview.market_bars[0].close = 12;
    assert.equal(backtestPreviewView(payload, context), null, "different adjustment bases cannot share an axis");
    preview.market_bars[0].close = 11;
    preview.market_bars.pop();
    assert.equal(backtestPreviewView(payload, context), null, "incomplete market payload cannot claim all candles");
});

test("only completed market prefix is shown without fabricated fills", () => {
    const view = backtestPreviewView(fixture(), context);
    assert.equal(view.bars.length, 1);
    assert.equal(view.result_scope, "stock-preview");
    assert.deepEqual(view.markers, []);
    assert.equal(view.backtest.status, "running");
});

test("late data from another job or stock and malformed prefixes are rejected", () => {
    assert.equal(backtestPreviewView(fixture(), { ...context, job: "job-2" }), null);
    assert.equal(backtestPreviewView(fixture(), { ...context, symbol: "sh.600812" }), null);
    const payload = fixture();
    payload.preview.processed = 2;
    assert.equal(backtestPreviewView(payload, context), null);
    payload.preview.processed = 1;
    payload.preview.asof = "2026-01-02";
    assert.equal(backtestPreviewView(payload, context), null);
});

test("real fills survive preview updates and future-dated records are rejected", () => {
    const payload = fixture();
    const marker = { id: "fill-1", time: "2026-01-01", kind: "fill", side: "BUY", price: 11 };
    payload.preview.markers.push(marker);
    assert.deepEqual(backtestPreviewView(payload, context).markers, [marker]);
    payload.preview.markers[0].time = "2026-01-02";
    assert.equal(backtestPreviewView(payload, context), null);
});

test("an unfinished account day cannot masquerade as finished market progress", () => {
    const payload = fixture();
    payload.preview.curve = [];
    assert.equal(backtestPreviewView(payload, context), null);
    payload.preview.curve = [{ timestamp: "2025-12-31T00:00:00", equity: 100 }];
    assert.equal(backtestPreviewView(payload, context), null);
});
