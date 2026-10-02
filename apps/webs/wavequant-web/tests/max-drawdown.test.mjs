import assert from "node:assert/strict";
import test from "node:test";

import {
    drawdownCandleRange,
    holdingDrawdownCurve,
    holdingDrawdownForMarker,
    holdingDrawdownInterval,
    holdingDrawdownLines,
    holdingDrawdownNote,
    holdingDrawdownValue,
    holdingDrawdownVersion,
    maxDrawdownInterval,
} from "../public/max-drawdown.js";

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

const episode = {
    symbol: "sz.001216",
    trade_id: "sz.001216-trade-1",
    entry_order_time: "2024-01-17T00:00:00",
    entry_time: "2024-01-17T15:00:00",
    exit_time: "2024-01-22T09:30:00",
    asof: "2024-01-22T09:30:00",
    status: "closed",
    metric_version: holdingDrawdownVersion,
    max_drawdown: -0.12,
    observed_max_drawdown: -0.12,
    cost_price: 10,
    low_price: 8.8,
    low_time: "2024-01-19",
    loss_amount: -120,
    coverage: "complete",
};
const metrics = {
    max_drawdown: -0.01,
    holding_drawdown_version: holdingDrawdownVersion,
    holding_drawdown_status: "complete",
    holding_max_drawdown: -0.12,
    holding_drawdown_interval: episode,
};

test("holding MAE uses only current metric version and never substitutes account equity drawdown", () => {
    assert.equal(holdingDrawdownValue(metrics), -0.12);
    assert.equal(holdingDrawdownValue({ max_drawdown: -0.3 }), null);
    assert.equal(
        holdingDrawdownValue({ ...metrics, holding_drawdown_version: "holding_price_peak_to_trough_v1" }),
        null,
    );
    assert.equal(holdingDrawdownValue({ ...metrics, holding_max_drawdown: 0 }), 0);
    assert.equal(holdingDrawdownValue({ ...metrics, holding_max_drawdown: null }), null);
    assert.match(holdingDrawdownNote({ max_drawdown: 0 }), /重新回测/);
    assert.match(holdingDrawdownNote({ ...metrics, holding_drawdown_status: "no_entry_fills" }), /无买入成交/);
    assert.match(holdingDrawdownNote({ ...metrics, holding_drawdown_status: "incomplete" }), /无法确认/);
    assert.match(holdingDrawdownNote({ ...metrics, holding_drawdown_status: "no_closed_cycles" }), /暂无完全卖出/);
});

test("holding loss chart clears at each liquidation and carries no previous cycle loss", () => {
    const view = {
        metrics,
        curve: curve([1, 0.8, 0.9, 1, 1.1]),
        backtest: {
            holding_drawdowns: [
                {
                    symbol: "TEST",
                    drawdown_curve: [
                        { timestamp: "2026-01-02T09:30:00", value: 0, quantity: 100 },
                        { timestamp: "2026-01-02T15:00:00", value: -0.3, quantity: 100 },
                        { timestamp: "2026-01-03T09:30:00", value: 0, quantity: 0 },
                    ],
                },
                {
                    symbol: "TEST",
                    drawdown_curve: [
                        { timestamp: "2026-01-04T09:30:00", value: 0, quantity: 200 },
                        { timestamp: "2026-01-04T15:00:00", value: -0.05, quantity: 200 },
                        { timestamp: "2026-01-05T09:30:00", value: 0, quantity: 0 },
                    ],
                },
                { symbol: "TEST", drawdown_curve: [{ timestamp: "2026-01-06T15:00:00", value: 0, quantity: 50 }] },
            ],
        },
    };
    assert.deepEqual(
        holdingDrawdownCurve(view).map((point) => point.value),
        [-30, 0, -5, 0, 0],
    );
    assert.deepEqual(
        holdingDrawdownCurve({ ...view, curve: view.curve.slice(0, 3) }),
        holdingDrawdownCurve(view).slice(0, 3),
    );
    assert.deepEqual(holdingDrawdownCurve({ ...view, metrics: { max_drawdown: -0.5 } }), []);
});

test("same-day new cycle and later complete data replace a cleared incomplete cycle", () => {
    const view = {
        metrics,
        curve: curve([1, 1]),
        backtest: {
            holding_drawdowns: [
                {
                    symbol: "TEST",
                    drawdown_curve: [
                        { timestamp: "2026-01-02T15:00:00", value: null, quantity: 100 },
                        { timestamp: "2026-01-03T11:00:00", value: 0, quantity: 0 },
                    ],
                },
                {
                    symbol: "TEST",
                    drawdown_curve: [
                        { timestamp: "2026-01-03T14:00:00", value: 0, quantity: 100 },
                        { timestamp: "2026-01-03T15:00:00", value: -0.1, quantity: 100 },
                    ],
                },
            ],
        },
    };
    assert.deepEqual(holdingDrawdownCurve(view), [{ time: "2026-01-02" }, { time: "2026-01-03", value: -10 }]);
});

test("final drawdown navigation covers only a completed same-symbol holding cycle", () => {
    const view = { symbol: "sz.001216", metrics };
    assert.deepEqual(holdingDrawdownInterval(view), { ...episode, from: "2024-01-17", to: "2024-01-22" });
    assert.equal(holdingDrawdownInterval({ ...view, symbol: "sh.600009" }), null);
    const open = { ...episode, exit_time: null, status: "open", asof: "2024-01-19T15:00:00" };
    assert.equal(holdingDrawdownInterval({ ...view, metrics: { ...metrics, holding_drawdown_interval: open } }), null);
});

test("trade details share cycle identity for initial entry, add-on and partial sell", () => {
    const view = { symbol: "sz.001216", metrics, backtest: { holding_drawdowns: [episode] } };
    for (const side of ["BUY", "SELL"]) {
        assert.equal(holdingDrawdownForMarker(view, { kind: "fill", side, trade_id: episode.trade_id }), episode);
    }
    assert.equal(holdingDrawdownForMarker(view, { kind: "order", trade_id: episode.trade_id }), null);
    assert.equal(holdingDrawdownForMarker(view, { kind: "fill", trade_id: "other" }), null);
    assert.match(holdingDrawdownLines(episode).join("\n"), /-12.00%.*买入至卖出/);
    assert.match(holdingDrawdownLines(episode).join("\n"), /浮亏 -120.00 元/);
    assert.match(holdingDrawdownLines({ ...episode, coverage: "incomplete" }).join("\n"), /已观察下界/);
});
