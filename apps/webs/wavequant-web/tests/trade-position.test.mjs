import assert from "node:assert/strict";
import test from "node:test";

import { formatFilledTradeCopy } from "../public/filled-trade-copy.js";
import {
    closedPositionFraction,
    closedPositionLabel,
    openPositionForMarker,
    positionProfit,
} from "../public/trade-position.js";

const sell = { kind: "fill", side: "SELL", quantity: 3496.84, remaining_quantity: 3576.32 };

test("buy copy exposes mature anchors, resolved pressure and disabled risk gate", () => {
    const text = formatFilledTradeCopy(
        { symbol: "sz.300163", variant: "lecture_v3", backtest: { start: "2018-01-02" }, asof: "2026-06-09", bars: [] },
        {
            side: "BUY",
            net_reward_risk: 1.2174,
            required_reward_risk: 1.5,
            net_reward_risk_filter: false,
            decision_evidence: [
                {
                    buy_point_type: "mature_shallow_squeeze",
                    origin_index_date: "2025-04-09",
                    ratio_low_price: 2.45,
                    maturity_index_date: "2025-10-28",
                    peak_index_date: "2026-05-21",
                    ratio_high_price: 6.05,
                    minimum_close_index_date: "2026-05-21",
                    counter_price: 5.6,
                    counter_ratio: 0.125,
                    counter_operator: "<=",
                    counter_limit: 1 / 3,
                    secondary_resistance_resolved: true,
                    secondary_high_date: "2026-02-02",
                    secondary_high: 5.99,
                    secondary_attack_date: "2026-05-25",
                    secondary_resolution_date: "2026-06-09",
                    secondary_confirmation_close: 6.94,
                    secondary_resistance_high: 6.66,
                },
            ],
        },
        "V3",
        "2.07%",
        null,
    );
    assert.match(text, /起涨 2025-04-09 2.4500/);
    assert.match(text, /12.50% <= 33.33%/);
    assert.match(text, /2026-02-02 高点 5.9900/);
    assert.match(text, /抵抗阶段高点 6.6600，抵抗解除/);
    assert.match(text, /费后盈亏比：1.22；门槛 1.50；过滤未启用/);
});

test("record squeeze copy distinguishes resistance high from prior candle and selling high", () => {
    const text = formatFilledTradeCopy(
        { symbol: "sz.301130", variant: "lecture_v3", backtest: { start: "2018-01-02" }, asof: "2026-09-21", bars: [] },
        {
            side: "BUY",
            decision_evidence: [
                {
                    squeeze_confirmation: "resistance_record_break",
                    attack_date: "2026-08-03",
                    confirmation_close: 25.9731,
                    confirmation_record_high: 25.8282,
                },
                {
                    inverse_reentry_path: "deep_alternation_kill_high_record_squeeze",
                    origin_index_date: "2024-02-08",
                    flip_high_index_date: "2025-08-11",
                    alternation_low_index_date: "2026-07-21",
                    recovery_whole_retracement: 0.6790135,
                    recovery_inverse_date: "2026-07-21",
                    recovery_kill_high: 24.8663,
                },
            ],
        },
        "V3",
        "5%",
        null,
    );
    assert.match(text, /正 N 2026-08-03/);
    assert.match(text, /收盘 25.9731 > 本次 N 抵抗阶段高点 25.8282/);
    assert.match(text, /67.90%/);
    assert.match(text, /杀多高 24.8663/);
    assert.doesNotMatch(text, /抵抗 K/);
});

test("unsold buy shows end-date mark-to-market profit without pretending to be closed", () => {
    const buy = {
        kind: "fill",
        side: "BUY",
        symbol: "sz.300154",
        time: "2026-01-02",
        timestamp: "2026-01-02T00:00:00",
        price: 10,
        raw_price: 10,
        quantity: 1000,
        fee: 5,
        id: "buy-1",
    };
    const open = {
        symbol: "sz.300154",
        entry_time: "2026-01-02T00:00:00",
        mark_time: "2026-01-03T00:00:00",
        quantity: 1000,
        realized_pnl: 0,
        unrealized_pnl: 995,
        total_pnl: 995,
        net_return: 995 / 10005,
    };
    const view = {
        symbol: buy.symbol,
        variant: "lecture_v3",
        asof: "2026-01-03",
        backtest: { start: "2026-01-01", open_positions: [open] },
        bars: [],
    };
    assert.equal(openPositionForMarker(view, buy), open);
    const profit = positionProfit(buy, null, open);
    assert.match(profit.text, /截至 2026-01-03/);
    assert.match(profit.text, /未实现盈亏：995/);
    assert.match(profit.text, /整笔收益率：9\.95%/);
    assert.equal(positionProfit(buy), null);
    assert.equal(openPositionForMarker(view, { ...buy, timestamp: "2026-01-01T00:00:00", time: "2026-01-01" }), null);
    assert.equal(openPositionForMarker(view, { ...buy, timestamp: "2026-01-02T09:45:00" }), null);
    const copy = formatFilledTradeCopy(view, buy, "V3", "9.99%", null);
    assert.ok(copy.includes(profit.text));
    assert.ok(copy.includes("未卖出"));
});

test("partial and final sales show cumulative original-cost returns, never the remaining lot return", () => {
    const partial = { ...sell, position_closed: false, position_pnl: 100, position_net_return: 0.01 };
    const future = { pnl: -500, net_return: -0.5 };
    assert.match(positionProfit(partial, future).text, /累计已实现收益率：1.00%/);
    assert.match(positionProfit(partial, future).text, /尚未清仓/);
    const final = { ...partial, position_closed: true, position_pnl: 50, position_net_return: 0.005 };
    assert.match(positionProfit(final, future).text, /整笔净收益率：0.50%/);
    assert.equal(positionProfit({ ...sell, position_closed: false }, future), null);
    const text = formatFilledTradeCopy(
        { symbol: "sz.300154", variant: "lecture_v3", backtest: { start: "2018-01-02" }, asof: "2026-09-07", bars: [] },
        partial,
        "V3",
        "—",
        future,
    );
    assert.ok(text.includes(positionProfit(partial).text));
    assert.ok(!text.includes("-50.00%"));
});

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

test("volume reversal copy distinguishes close breakout and structural origin defense", () => {
    const text = formatFilledTradeCopy(
        { symbol: "sz.300163", variant: "lecture_v3", backtest: { start: "2018-01-02" }, asof: "2026-05-18", bars: [] },
        {
            side: "BUY",
            decision_evidence: [
                {
                    squeeze_confirmation: "volume_reversal_record_break",
                    n_close_break_date: "2026-05-12",
                    attack_date: "2026-05-11",
                    reversal_close: 5.1,
                    reversal_record_high: 4.85,
                    reversal_volume: 79253633,
                    reversal_previous_volume: 18961100,
                    reversal_low: 4.38,
                    reversal_structural_low: 4.15,
                },
            ],
        },
        "V3",
        "1%",
        null,
    );
    assert.match(text, /N 收盘突破 2026-05-12/);
    assert.match(text, /5.1000 > 抵抗阶段高 4.8500/);
    assert.match(text, /守住 N 起点 4.1500/);
});
