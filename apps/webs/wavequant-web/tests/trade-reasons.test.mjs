import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { formatFilledTradeCopy } from "../public/filled-trade-copy.js";
import { numberedTradeReasons, tradeReasonItems } from "../public/trade-reasons.js";
import { appendTradeEvidence } from "../public/trade-review.js";

test("buy decision reasons are translated and numbered in order with recorded evidence", () => {
    const marker = {
        side: "BUY",
        reason: "system_n_continuation|system_transition_squeeze",
        decision_evidence: [
            {
                squeeze_confirmation: "resistance_record_break",
                attack_date: "2026-08-03",
                confirmation_close: 25.9731,
                confirmation_record_high: 25.8282,
            },
        ],
    };
    assert.deepEqual(numberedTradeReasons(marker), [
        "1. N 字延续",
        "2. 第一类：交替后正 N 轧空",
        "3. 抵抗高点突破：2026-08-03 正 N，确认收盘 25.9731 > 抵抗阶段高 25.8282。",
    ]);
    const copy = formatFilledTradeCopy(
        { symbol: "sz.301130", variant: "lecture_v3", backtest: { start: "2026-01-01" }, asof: "2026-09-21", bars: [] },
        marker,
        "V3",
        "5%",
        null,
    );
    assert.match(copy, /买入原因：\n1\. N 字延续\n2\. 第一类：交替后正 N 轧空\n3\. 抵抗高点突破/);
});

test("joint N and alternation explain March 25 after the March 22 attack", () => {
    const reasons = tradeReasonItems({
        side: "BUY",
        reason: "system_transition_squeeze",
        decision_evidence: [
            {
                buy_point_type: "transition_squeeze",
                joint_alternation_confirmation: true,
                attack_date: "2021-03-22",
                alternation_index_date: "2021-03-25",
                trend_level: 2,
            },
        ],
    });
    assert.match(reasons.at(-1), /2021-03-22 出现正 N，2021-03-25 轧空共同确认空多交替与入场资格/);
});

test("sell reasons use exit trigger and observed values without inventing missing evidence", () => {
    const marker = {
        side: "SELL",
        reason: "volume_down_reduce_70",
        volume_trigger_date: "2026-09-04",
        trigger_volume: 200000,
        previous_volume: 100000,
        trigger_close: 9,
        previous_close: 10,
    };
    assert.deepEqual(tradeReasonItems(marker), [
        "放量下跌且收盘低于前收，当日收盘累计减仓原持仓 70%",
        "放量下跌：2026-09-04 成交量 200,000 > 前日 100,000，收盘 9.0000 < 前收 10.0000。",
    ]);
    assert.deepEqual(numberedTradeReasons({ side: "SELL" }), ["1. 本次成交记录未提供具体决策原因。"]);
});

test("next-session gap fade clear shows the observable close confirmation", () => {
    const reasons = tradeReasonItems({
        side: "SELL",
        reason: "volume_down_next_gap_fade_clear",
        volume_trigger_date: "2020-05-13",
        trigger_volume: 9716400,
        previous_volume: 6451974,
        trigger_close: 5.27,
        previous_close: 5.31,
        gap_previous_close: 5.27,
        observed_open: 5.2,
        observed_close: 5.14,
    });
    assert.match(reasons[0], /当日收盘清空余仓/);
    assert.match(reasons[2], /开盘 5\.2000 < 前收 5\.2700，收盘 5\.1400 < 开盘 5\.2000/);
});

test("next-session bearish close below warning low explains full exit", () => {
    const reasons = tradeReasonItems({
        side: "SELL",
        reason: "volume_down_next_followthrough_clear",
        volume_trigger_date: "2020-05-13",
        trigger_volume: 9716400,
        previous_volume: 6451974,
        trigger_close: 5.27,
        previous_close: 5.31,
        warning_low: 5.26,
        observed_open: 5.3,
        observed_close: 5.07,
    });
    assert.match(reasons[0], /当日收盘清空余仓/);
    assert.match(reasons[2], /收盘 5\.0700 < 警示日低点 5\.2600，且低于当日开盘 5\.3000/);
});

test("chart trade detail renders the same numbered reasons", () => {
    const document = new JSDOM("<div id='panel'></div>").window.document;
    const previousDocument = globalThis.document;
    globalThis.document = document;
    try {
        const marker = { side: "SELL", reason: "volume_down_reduce_70" };
        appendTradeEvidence(document.getElementById("panel"), marker);
        assert.equal(document.querySelector(".trade-reason-list p")?.textContent, numberedTradeReasons(marker)[0]);
    } finally {
        globalThis.document = previousDocument;
    }
});

test("secondary C-wave clear explains known anchors, target, resistance and close break", () => {
    const marker = {
        side: "SELL",
        reason: "secondary_wave_target_resistance_clear",
        trend_origin_date: "2020-04-28",
        trend_origin_low: 4.6676,
        trend_key_date: "2020-06-04",
        trend_key_high: 5.6978,
        wave_b_date: "2020-06-12",
        wave_b_low: 4.9199,
        wave_equal_target: 5.9501,
        trend_attack_date: "2020-07-10",
        trend_resistance_dates: ["2020-07-10", "2020-07-13"],
        trend_indecision_date: "2020-07-14",
        trend_upper_shadow_fraction: 0.4412,
        trend_lower_shadow_fraction: 0.4412,
        trend_indecision_low: 5.5931,
        observed_close: 5.5716,
    };
    const lines = numberedTradeReasons(marker);
    assert.equal(lines.length, 4);
    assert.match(lines[1], /2020-04-28.*2020-06-04.*2020-06-12.*5\.9501/);
    assert.match(lines[2], /2020-07-10、2020-07-13.*2020-07-14/);
    assert.match(lines[3], /5\.5716 < .*5\.5931/);
});
