import assert from "node:assert/strict";
import test from "node:test";

import { formatFilledTradeCopy } from "../public/filled-trade-copy.js";
import { closedPositionFraction, closedPositionLabel } from "../public/trade-position.js";

const sell = { kind: "fill", side: "SELL", quantity: 3496.84, remaining_quantity: 3576.32 };

test("closing proportion uses actual sold and remaining shares, not the target fraction", () => {
    assert.equal(closedPositionFraction({ ...sell, exit_fraction: 0.5 }), 3496.84 / 7073.16);
    assert.equal(closedPositionLabel(sell), "平仓比例：49.44%（占卖出前该股票持仓）");
    const text = formatFilledTradeCopy(
        { symbol: "sz.300154", variant: "lecture_v3", backtest: { start: "2018-01-02" }, asof: "2026-09-07", bars: [] },
        sell,
        "V3",
        "—",
        null,
    );
    assert.ok(text.includes(closedPositionLabel(sell)));
});

test("later liquidation uses the holding immediately before that sale", () => {
    assert.equal(closedPositionFraction({ ...sell, quantity: 3000, remaining_quantity: 3500 }), 3000 / 6500);
    assert.equal(closedPositionFraction({ ...sell, remaining_quantity: 0 }), 1);
    assert.equal(closedPositionFraction({ kind: "fill", side: "SELL", quantity: 100, position_closed: true }), 1);
});

test("missing holdings, invalid quantities, and unfilled orders never imply full liquidation", () => {
    for (const patch of [
        { remaining_quantity: undefined },
        { remaining_quantity: -1 },
        { quantity: 0 },
        { quantity: NaN },
        { remaining_quantity: Infinity },
        { side: "BUY" },
        { kind: "order" },
    ])
        assert.equal(closedPositionFraction({ ...sell, ...patch }), null);
    assert.match(closedPositionLabel({ ...sell, remaining_quantity: undefined }), /缺少成交前持仓数据/);
});
