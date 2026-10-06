import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { formatBlockedTradeCopy } from "../public/blocked-trade-nodes.js";
import { formatFilledTradeCopy } from "../public/filled-trade-copy.js";
import { holdingDrawdownVersion } from "../public/max-drawdown.js";
import { numberedTradeReasons, tradeReasonItems } from "../public/trade-reasons.js";
import { appendTradeEvidence } from "../public/trade-review.js";

test("five-top body-child clear traces the actual mother body and allows a higher wick", () => {
    const marker = {
        id: "body-clear",
        kind: "fill",
        side: "SELL",
        reason: "wave_five_top_body_upper_shadow_clear",
        time: "2025-04-25",
        signal_timestamp: "2025-04-25T00:00:00",
        wave_n_date: "2024-08-30",
        wave_reached_date: "2025-04-11",
        wave_reached_stage: "ten_full",
        wave_reached_price: 10.5736383573,
        mother_date: "2025-04-24",
        mother_body_low: 15.5599148436,
        mother_body_high: 19.0161558053,
        child_body_low: 17.1172724311,
        child_body_high: 17.1172724311,
        observed_high: 19.5352750012,
        observed_low: 17.1172724311,
        wave_upper_shadow_fraction: 1,
        execution_model: "same_day_close",
        raw_price: 12.53,
        raw_shares: 100,
        fill_assumption: "nonflat_limit_close_sell_without_queue_verification",
        applied_slippage_bps: 0,
    };
    const text = numberedTradeReasons(marker).join("\n");
    assert.match(text, /2025-04-24 母线实体 15\.5599～19\.0162.*17\.1173～17\.1173/);
    assert.match(text, /今日十字实体/);
    assert.match(text, /19\.5353.*100\.00%.*100%.*清空余仓/);
    assert.doesNotMatch(text, /undefined|NaN|开盘 .*< 前收|收盘 .*≤ 开盘/);
    const view = { symbol: "sh.601086", asof: "2025-04-25", bars: [], metrics: {}, backtest: {} };
    const copy = formatFilledTradeCopy(view, marker, "V3", "10%");
    assert.match(copy, /母线实体 15\.5599～19\.0162/);
    assert.match(copy, /12\.5300.*未验证跌停排队成交.*0 bps/);
    const document = new JSDOM("<div id='panel'></div>").window.document;
    const previousDocument = globalThis.document;
    globalThis.document = document;
    try {
        appendTradeEvidence(document.getElementById("panel"), marker);
        assert.match(document.getElementById("panel").textContent, /母线实体 15\.5599～19\.0162/);
    } finally {
        globalThis.document = previousDocument;
    }
});

test("intraday body-child reasons use the completed partial body and separate execution time", () => {
    const text = numberedTradeReasons({
        reason: "wave_five_top_body_upper_shadow_clear",
        wave_reached_date: "2025-04-24",
        wave_reached_stage: "five_top",
        wave_reached_price: 10,
        decision_timestamp: "2025-04-25T09:40:00+08:00",
        execution_timestamp: "2025-04-25T09:40:00+08:00",
        execution_model: "intraday_5m_next_open",
        mother_date: "2025-04-24",
        mother_body_low: 9.8,
        mother_body_high: 10.1,
        child_body_low: 9.9,
        child_body_high: 10,
        observed_high: 10.5,
        observed_low: 9.9,
        wave_upper_shadow_fraction: 5 / 6,
    }).join("\n");
    assert.match(text, /2025-04-25 09:40.*9\.8000～10\.1000.*已完成五分钟线.*9\.9000～10\.0000/);
    assert.doesNotMatch(text, /低开|收盘 .*≤ 开盘|undefined|NaN/);
});

test("two-T body reversal details and clipboard trace the engulfed candle and previous bearish volume", () => {
    const marker = {
        kind: "fill",
        side: "SELL",
        reason: "wave_two_t_body_volume_clear",
        time: "2023-12-18",
        wave_n_date: "2023-11-02",
        wave_reached_date: "2023-12-11",
        wave_reached_price: 5.17802758,
        engulfed_date: "2023-12-15",
        previous_open: 5.33952552,
        previous_close: 5.5716788,
        observed_open: 5.63224053,
        observed_close: 5.31933828,
        observed_volume: 69_475_932,
        previous_volume: 89_356_897,
        bearish_reference_date: "2023-12-08",
        bearish_reference_volume: 23_328_933,
    };
    const reasons = numberedTradeReasons(marker).join("\n");
    assert.match(reasons, /2023-11-02 正 N.*2023-12-11.*二饱/);
    assert.match(reasons, /实体反包 2023-12-15.*5.6322.*≥.*5.5717.*5.3193.*≤.*5.3395/);
    assert.match(reasons, /69,475,932 股 >.*2023-12-08.*23,328,933 股.*清空余仓/);
    assert.doesNotMatch(reasons, /undefined|NaN|89,356,897 股 >|前日成交量/);
    const view = { symbol: "sh.600825", asof: "2023-12-18", bars: [], metrics: {}, backtest: {} };
    assert.match(formatFilledTradeCopy(view, marker, "V3", "10%"), /实体反包 2023-12-15.*最近阴线 2023-12-08/);
});

test("trade detail and clipboard show the same whole-holding entry-cost maximum loss", () => {
    const episode = {
        metric_version: holdingDrawdownVersion,
        symbol: "sz.001216",
        trade_id: "t1",
        entry_time: "2024-01-17T15:00:00",
        exit_time: null,
        asof: "2024-01-19T15:00:00",
        status: "open",
        observed_max_drawdown: -0.125,
        max_drawdown: -0.125,
        cost_price: 16,
        low_price: 14,
        low_time: "2024-01-19",
        loss_amount: -200,
        coverage: "complete",
    };
    const marker = { kind: "fill", side: "BUY", trade_id: "t1", time: "2024-01-17" };
    const view = {
        symbol: "sz.001216",
        asof: "2024-01-19",
        bars: [],
        metrics: { holding_drawdown_version: holdingDrawdownVersion },
        backtest: { start: "2024-01-01", holding_drawdowns: [episode] },
    };
    const text = formatFilledTradeCopy(view, marker, "V3", "10%");
    assert.match(text, /整笔持仓最大亏损：-12.50%/);
    assert.match(text, /未清仓/);
    assert.match(text, /浮亏 -200.00 元/);
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const panel = document.querySelector("section");
        appendTradeEvidence(panel, marker, null, episode);
        assert.match(panel.textContent, /整笔持仓最大亏损：-12.50%/);
        assert.match(panel.textContent, /成本 16.0000 元.*最低 14.0000 元/);
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});

test("secondary then primary alternation breakout explains dates, price and volume", () => {
    const marker = {
        side: "BUY",
        reason: "system_nested_alternation_breakout",
        decision_evidence: [
            {
                buy_point_type: "nested_alternation_breakout",
                secondary_low_date: "2023-10-23",
                secondary_known_date: "2023-11-08",
                secondary_low_price: 11.9045,
                primary_low_date: "2023-12-28",
                primary_known_date: "2024-01-05",
                primary_low_price: 12.723,
                confirmation_close: 14.9195,
                breakout_high: 13.9041,
                confirmation_volume: 4557338,
                previous_volume: 1579601,
                alternation_index_date: "2024-01-05",
                attack_date: "2024-01-17",
            },
        ],
    };
    const reasons = numberedTradeReasons(marker);
    assert.equal(reasons.length, 3);
    assert.match(reasons[0], /二级后一级交替低点放量阳线突破/);
    assert.match(reasons[1], /2023-10-23.*2023-11-08.*2023-12-28.*2024-01-05/);
    assert.match(reasons[2], /14\.9195 >.*13\.9041.*4,557,338.*当日正 N/);
    const view = {
        symbol: "sz.001216",
        variant: "lecture_v3",
        asof: "2024-01-17",
        backtest: { start: "2021-10-19" },
        bars: [],
    };
    assert.match(formatFilledTradeCopy(view, marker, "V3", "20%"), /二级交替低点/);
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const panel = document.querySelector("section");
        appendTradeEvidence(panel, { ...marker, kind: "signal" });
        assert.match(panel.textContent, /二级交替低点.*一级交替低点/);
        assert.doesNotMatch(panel.textContent, /第一类买点|空多交替 → 新正 N → 轧空/);
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});

test("near C equal-wave resistance and later volume break explain both July sales", () => {
    const warning = numberedTradeReasons({
        side: "SELL",
        reason: "wave_c_equal_near_resistance_reduce",
        target_warning_date: "2026-07-06",
        wave_c_equal_target: 8.57,
        target_warning_high: 8.56,
        wave_target_gap: 0.01,
        target_resistance_patterns: ["long_upper_shadow"],
        wave_upper_shadow_fraction: 0.58 / 0.78,
        exit_target_fraction: 0.8,
    }).join(" ");
    assert.match(warning, /8\.5700.*8\.5600.*0\.0100/);
    assert.match(warning, /上影占振幅 74\.36%.*累计 80\.00%/);
    const clear = numberedTradeReasons({
        side: "SELL",
        reason: "wave_c_equal_near_volume_clear",
        target_warning_date: "2026-07-06",
        observed_low: 6.58,
        previous_low: 7.4,
        observed_close: 7.07,
        previous_close: 8.15,
        observed_volume: 82_443_254,
        bearish_reference_date: "2026-06-30",
        bearish_reference_volume: 18_267_900,
    }).join(" ");
    assert.match(clear, /6\.5800 < 昨低 7\.4000.*7\.0700 < 昨收 8\.1500/);
    assert.match(clear, /82,443,254 股 > 此前最近阴线 2026-06-30 的 18,267,900 股/);
    assert.doesNotMatch(clear, /undefined|NaN/);
});

test("five-top child break explains the prior bearish volume and both dated price breaks", () => {
    const marker = {
        side: "SELL",
        reason: "wave_five_top_child_volume_clear",
        wave_n_date: "2025-09-29",
        wave_reached_date: "2025-10-28",
        wave_reached_stage: "five_top",
        wave_reached_price: 4.93,
        mother_date: "2025-11-13",
        child_date: "2025-11-14",
        child_low: 4.91,
        observed_low: 4.85,
        observed_close: 4.91,
        previous_low: 4.99,
        previous_close: 5.19,
        observed_volume: 63_288_181,
        bearish_reference_date: "2025-11-11",
        bearish_reference_volume: 63_172_500,
    };
    const lines = numberedTradeReasons(marker);
    assert.equal(lines.length, 4);
    const text = lines.join(" ");
    assert.match(text, /2025-09-29 正 N/);
    assert.match(text, /2025-10-28 已达到五顶 4\.9300/);
    assert.match(text, /2025-11-13 母线包含前天 2025-11-14 子线/);
    assert.match(text, /4\.8500 < 昨低 4\.9900.*4\.9100 < 昨收 5\.1900.*最低 < 子低 4\.9100/);
    assert.match(text, /63,288,181 股 > 最近阴线 2025-11-11 的 63,172,500 股.*清空余仓/);
    assert.doesNotMatch(text, /undefined|子线收盘|成交量.*前日/);
    assert.match(numberedTradeReasons({ ...marker, wave_reached_stage: "ten_full" }).join(" "), /已达到十满/);
    assert.doesNotMatch(numberedTradeReasons({ side: "SELL", reason: marker.reason }).join(" "), /undefined|NaN/);
    const copy = formatFilledTradeCopy(
        { symbol: "sz.300163", variant: "lecture_v3", backtest: { start: "2025-08-01" }, asof: "2025-11-18", bars: [] },
        marker,
        "V3",
        "",
        null,
    );
    assert.match(copy, /63,288,181 股 > 最近阴线 2025-11-11 的 63,172,500 股/);
    const document = new JSDOM("<div id='panel'></div>").window.document;
    const previousDocument = globalThis.document;
    globalThis.document = document;
    try {
        appendTradeEvidence(document.getElementById("panel"), marker);
        assert.match(document.querySelector(".trade-reason-list").textContent, /前天 2025-11-14 子线/);
    } finally {
        globalThis.document = previousDocument;
    }
});

test("five-top gap clear explains the prior open and bearish-volume reference", () => {
    const text = numberedTradeReasons({
        side: "SELL",
        reason: "wave_five_top_gap_volume_clear",
        wave_n_date: "2024-04-12",
        wave_reached_date: "2026-09-14",
        wave_reached_stage: "five_top",
        wave_reached_price: 23.6007,
        observed_open: 24.8662,
        observed_close: 22.5537,
        observed_volume: 52_774_200,
        previous_open: 22.8045,
        previous_close: 25.0613,
        bearish_reference_date: "2026-08-26",
        bearish_reference_volume: 7_902_800,
    }).join(" ");
    assert.match(text, /2026-09-14 已达到五顶 23\.6007/);
    assert.match(text, /24\.8662 < 前收 25\.0613.*22\.5537 < 前开 22\.8045/);
    assert.match(text, /52,774,200 股 > 最近阴线 2026-08-26 的 7,902,800 股/);
    assert.doesNotMatch(text, /undefined|NaN/);
});

test("five-top gap upper-shadow clear traces same-basis Guofang prices in detail and clipboard", () => {
    const marker = {
        kind: "signal",
        side: "SELL",
        reason: "wave_five_top_gap_upper_shadow_clear",
        time: "2025-04-25",
        wave_n_date: "2024-08-30",
        wave_reached_date: "2025-04-11",
        wave_reached_stage: "ten_full",
        wave_reached_price: 10.573638357283865,
        observed_open: 17.117272431106827,
        observed_high: 19.535275001183372,
        observed_low: 17.117272431106827,
        observed_close: 17.117272431106827,
        previous_close: 19.016155805347726,
        wave_upper_shadow_fraction: 1,
    };
    const reasons = numberedTradeReasons(marker).join("\n");
    assert.match(reasons, /2024-08-30 正 N.*2025-04-11 已达到十满 10\.5736/);
    assert.match(reasons, /17\.1173 < 前收 19\.0162.*收盘 17\.1173 ≤ 开盘.*十字线/);
    assert.match(reasons, /最高 19\.5353.*最低 17\.1173.*上影占振幅 100\.00% ≥ 50%/);
    assert.match(reasons, /退出目标为 100%.*清空余仓.*无需放量或此前减仓/);
    assert.doesNotMatch(reasons, /undefined|NaN|最近阴线|12\.53/);
    const view = { symbol: "sh.601086", asof: "2025-04-25", bars: [], backtest: { start: "2018-01-01" } };
    const copy = formatFilledTradeCopy(view, { ...marker, kind: "fill" }, "V3", "");
    assert.match(copy, /2025-04-11 已达到十满.*17\.1173 < 前收 19\.0162/s);
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        appendTradeEvidence(document.querySelector("section"), marker);
        assert.match(document.querySelector(".trade-reason-list").textContent, /上影占振幅 100\.00%.*退出目标为 100%/);
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
    assert.match(numberedTradeReasons({ ...marker, wave_reached_stage: "five_top" }).join(" "), /已达到五顶/);
    const composite = { ...marker, reason: "negative_turn_risk_exit|wave_five_top_gap_upper_shadow_clear" };
    assert.match(numberedTradeReasons(composite).join(" "), /负扭转风险退出.*17\.1173 < 前收 19\.0162/);
    const deferred = {
        ...marker,
        id: "deferred-2025-04-25",
        kind: "order",
        status: "deferred",
        price: marker.observed_close,
        reason: "not_sellable",
        decision_reason: composite.reason,
        signal_time: marker.time,
        blockedStage: "execution",
    };
    const deferredReasons = numberedTradeReasons(deferred).join(" ");
    assert.match(deferredReasons, /17\.1173 < 前收 19\.0162.*退出目标为 100%/);
    assert.doesNotMatch(deferredReasons, /已清空|已成交/);
    const blockedCopy = formatBlockedTradeCopy(view, [{ time: deferred.time, nodes: [deferred] }], "V3");
    assert.match(blockedCopy, /执行委托未成交.*当日不满足可卖条件，退出延期/s);
    const delayedFill = {
        ...deferred,
        kind: "fill",
        status: "filled",
        reason: marker.reason,
        time: "2025-04-28",
        signal_time: undefined,
        signal_timestamp: "2025-04-25T00:00:00",
    };
    const delayedReasons = numberedTradeReasons(delayedFill).join(" ");
    assert.match(delayedReasons, /判定日 2025-04-25：开盘 17\.1173/);
    assert.doesNotMatch(delayedReasons, /判定日 2025-04-28|本日开盘/);
});

test("five-top minute exit renders the supplied decision and execution times independently", () => {
    const marker = {
        kind: "fill",
        side: "SELL",
        reason: "wave_five_top_gap_upper_shadow_clear",
        time: "2025-04-25",
        wave_n_date: "2024-08-30",
        wave_reached_date: "2025-04-11",
        wave_reached_stage: "ten_full",
        wave_reached_price: 10.5736,
        observed_open: 17.1173,
        observed_high: 19.5353,
        observed_low: 17.1173,
        observed_close: 17.1173,
        previous_close: 19.0162,
        wave_upper_shadow_fraction: 1,
        execution_model: "intraday_5m_next_open",
        decision_timestamp: "2025-04-25T09:55:00",
        execution_timestamp: "2025-04-25T10:00:00",
        timestamp: "2025-04-25T10:00:00",
        minute_next_open_raw: 12.61,
        raw_price: 12.603695,
        price: 12.603695 * 1.3661031469359,
        adjustment_factor: 1.3661031469359,
        applied_slippage_bps: 5,
        remaining_quantity: 0,
    };
    const view = {
        symbol: "sh.601086",
        variant: "lecture_v3",
        asof: marker.time,
        bars: [],
        backtest: { start: "2018-01-01" },
    };
    const copy = formatFilledTradeCopy(view, marker, "V3", "");
    assert.match(copy, /判定日 2025-04-25 09:55.*累计日内收盘/);
    assert.match(copy, /决定时间：2025-04-25T09:55:00/);
    assert.match(copy, /成交时间：2025-04-25T10:00:00/);
    assert.match(copy, /下一根五分钟开盘原价：12\.6100 元/);
    assert.match(copy, /已完成五分钟线判定.*模拟成交/);
    assert.doesNotMatch(copy, /未还原尾盘分钟路径|日线成交原因/);
    const queueMarker = {
        ...marker,
        fill_assumption: "observed_nonflat_limit_intraday_sell_without_queue_verification",
        minute_next_open_raw: 12.53,
        raw_price: 12.53,
        price: 12.53 * marker.adjustment_factor,
        applied_slippage_bps: 0,
    };
    const queueCopy = formatFilledTradeCopy(view, queueMarker, "V3", "");
    assert.match(queueCopy, /判定时已观察到跌停打开.*下一根分钟开盘原价 12\.5300.*未验证跌停排队成交.*滑点 0 bps/);
    assert.doesNotMatch(queueCopy, /非一字跌停.*收盘原价|未还原尾盘分钟路径/);
    // This separate execution record uses a 10 bps model capped by the 12.53 price floor.
    const floorMarker = {
        ...marker,
        minute_next_open_raw: 12.54,
        raw_price: 12.53,
        price: 12.53 * marker.adjustment_factor,
        slippage_price_floor: "a_share_lower_limit",
        execution_lower_limit_raw: 12.53,
        applied_slippage_bps: 10000 / 1254,
    };
    const floorCopy = formatFilledTradeCopy(view, floorMarker, "V3", "");
    assert.match(floorCopy, /原始跌停价 12\.5300 元.*实际卖出滑点 7\.9745 bps/);
    assert.doesNotMatch(floorCopy, /未验证跌停排队成交/);
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        appendTradeEvidence(document.querySelector("section"), marker);
        const text = document.querySelector("section").textContent;
        assert.match(text, /决定 2025-04-25T09:55:00.*模拟成交 2025-04-25T10:00:00/);
        assert.match(text, /下一根五分钟线开盘原价 12\.6100 元/);
        assert.doesNotMatch(text, /本笔按触发当日收盘价/);
        const queuePanel = document.createElement("section");
        appendTradeEvidence(queuePanel, queueMarker);
        assert.match(queuePanel.textContent, /判定时已观察到跌停打开.*12\.5300.*未验证跌停排队成交.*滑点 0 bps/);
        const floorPanel = document.createElement("section");
        appendTradeEvidence(floorPanel, floorMarker);
        assert.match(floorPanel.textContent, /原始跌停价 12\.5300 元.*实际卖出滑点 7\.9745 bps/);
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});

test("nonflat limit-down close sell explains raw 12.53, zero slippage and explicit minute fallback", () => {
    const marker = {
        kind: "fill",
        side: "SELL",
        reason: "wave_five_top_gap_upper_shadow_clear",
        time: "2025-04-25",
        wave_n_date: "2024-08-30",
        wave_reached_date: "2025-04-11",
        wave_reached_stage: "ten_full",
        wave_reached_price: 10.5736,
        observed_open: 17.117272431106827,
        observed_high: 19.535275001183372,
        observed_low: 17.117272431106827,
        observed_close: 17.117272431106827,
        previous_close: 19.016155805347726,
        wave_upper_shadow_fraction: 1,
        execution_model: "same_day_close",
        decision_timestamp: "2025-04-25T15:00:00",
        timestamp: "2025-04-25T15:00:00",
        price: 17.117272431106827,
        raw_price: 12.53,
        adjustment_factor: 1.3661031469359,
        fill_assumption: "nonflat_limit_close_sell_without_queue_verification",
        applied_slippage_bps: 0,
        minute_fallback: { reason: "minute_coverage_unavailable", purpose: "five_top_gap_upper_shadow_exit" },
        remaining_quantity: 0,
    };
    const view = {
        symbol: "sh.601086",
        variant: "lecture_v3",
        asof: marker.time,
        bars: [],
        backtest: { start: "2018-01-01" },
    };
    const copy = formatFilledTradeCopy(view, marker, "V3", "");
    assert.match(copy, /非一字跌停.*收盘原价 12\.5300 元模拟卖出.*未验证跌停排队成交.*滑点 0 bps/);
    assert.match(copy, /成交口径：当日收盘价.*未还原尾盘分钟路径/);
    assert.match(copy, /日线成交原因：当日缺少完整同源分钟线/);
    assert.doesNotMatch(copy, /下一根五分钟开盘原价|涨停排队|09:55|10:00/);
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        appendTradeEvidence(document.querySelector("section"), marker);
        const text = document.querySelector("section").textContent;
        assert.match(text, /非一字跌停.*收盘原价 12\.5300.*未验证跌停排队成交.*滑点 0 bps/);
        assert.match(text, /缺少完整同源分钟线.*回退到日线收盘/);
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});

test("bearish outside mother reduction and next-session clear use mother price and volume evidence", () => {
    const reduction = tradeReasonItems({
        side: "SELL",
        reason: "volume_bearish_child_mother_reduce_70",
        mother_date: "2024-01-29",
        mother_low: 15.8461,
        mother_high: 16.3274,
        mother_volume: 3_106_650,
        child_date: "2024-01-26",
        child_low: 15.8942,
        child_high: 16.3274,
        bearish_reference_date: "2024-01-22",
        bearish_reference_volume: 2_868_478,
    }).join(" ");
    assert.match(reduction, /子母线阴母反包.*累计减仓 70%/);
    assert.match(reduction, /2024-01-29.*15\.8461～16\.3274.*反包 2024-01-26.*15\.8942～16\.3274/);
    assert.match(reduction, /母线成交量 3,106,650 股 > 此前 2024-01-22 最近阴线量 2,868,478 股/);
    const clear = tradeReasonItems({
        side: "SELL",
        reason: "volume_bearish_child_mother_break_clear",
        mother_date: "2024-01-29",
        mother_low: 15.8461,
        mother_close: 16.0386,
        observed_low: 15.3167,
        observed_close: 15.3528,
    }).join(" ");
    assert.match(clear, /次一交易日最低 15\.3167 < 母线低点 15\.8461.*收盘 15\.3528 < 母线收盘 16\.0386.*清空余仓/);
    assert.doesNotMatch(clear, /子线低点|子线收盘|undefined/);
});

test("two-T next-session clear explains target and double break without a shadow warning", () => {
    const reasons = numberedTradeReasons({
        side: "SELL",
        reason: "wave_two_t_next_volume_clear",
        wave_reached_date: "2026-06-09",
        wave_reached_price: 7.3,
        previous_upper_shadow_fraction: 0.36 / 0.76,
        observed_low: 6.32,
        previous_low: 6.54,
        observed_close: 6.47,
        previous_close: 6.94,
        observed_volume: 50_750_501,
        bearish_reference_date: "2026-06-04",
        bearish_reference_volume: 36_051_401,
    }).join("\n");
    assert.match(reasons, /2026-06-09 二饱.*7\.3000.*到位/);
    assert.match(reasons, /前日上影占振幅 47\.37%，超过 40%；次日收阴/);
    assert.match(reasons, /6\.3200 < 昨低 6\.5400/);
    assert.match(reasons, /6\.4700 < 昨收 6\.9400/);
    assert.match(reasons, /最近阴线 2026-06-04/);
    assert.match(reasons, /清空余仓/);
    assert.doesNotMatch(reasons, /50%|减仓|undefined/);
});

test("global two-T risk explains warning and the next bearish-volume double break", () => {
    const warning = {
        side: "SELL",
        reason: "wave_two_t_resistance_reduce",
        target_warning_date: "2026-06-09",
        wave_reached_price: 7.3,
        wave_upper_shadow_fraction: 0.5,
        exit_target_fraction: 0.8,
    };
    const reduced = numberedTradeReasons(warning).join("\n");
    assert.match(reduced, /禁买与加仓/);
    assert.match(reduced, /7\.3000/);
    assert.match(reduced, /上影占振幅 50\.00%，至少 50%/);
    assert.doesNotMatch(reduced, /三分之一/);
    const clear = numberedTradeReasons({
        ...warning,
        reason: "wave_two_t_resistance_volume_clear",
        observed_low: 6.32,
        previous_low: 6.54,
        observed_close: 6.47,
        previous_close: 6.94,
        observed_volume: 50_750_501,
        bearish_reference_date: "2026-06-04",
        bearish_reference_volume: 36_051_401,
    }).join("\n");
    assert.match(clear, /6\.3200 < 昨低 6\.5400/);
    assert.match(clear, /6\.4700 < 昨收 6\.9400/);
    assert.match(clear, /最近阴线 2026-06-04/);
    assert.match(clear, /清空余仓/);
});

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

const attackBarProof = {
    squeeze_confirmation: "resistance_attack_bar_break",
    attack_date: "2024-03-05",
    n_resistance_date: "2024-03-05",
    confirmation_high: 4.87,
    confirmation_close: 4.84,
    confirmation_record_high: 4.84,
    n_attack_high: 4.68,
    n_attack_close: 4.64,
    confirmation_strong_bullish: true,
    confirmation_body_open_ratio: 0.043103,
    confirmation_body_range_ratio: 0.869565,
    confirmation_upper_shadow_ratio: 0.130435,
};

test("attack-bar break explains separate strict close and high comparisons in reasons, detail and clipboard", () => {
    const marker = {
        id: "attack-bar-break",
        time: "2024-03-20",
        side: "BUY",
        reason: "system_transition_squeeze",
        decision_evidence: [attackBarProof],
    };
    const explanation = tradeReasonItems(marker).find((line) => line.startsWith("原正 N 突破棒确认："));
    assert.match(
        explanation,
        /2024-03-05 正 N.*收盘 4\.8400 > 原突破棒收盘 4\.6400.*最高价 4\.8700 > 原突破棒最高 4\.6800/,
    );
    assert.match(explanation, /原 N 防守完整.*突破当天或次日已有空头抵抗（2024-03-05）失败.*确认轧空/);
    assert.match(
        explanation,
        /中大阳且短上影.*实体\/开盘 4\.31% ≥ 3%.*实体\/振幅 86\.96% ≥ 60%.*上影\/振幅 13\.04% ≤ 20%/,
    );
    assert.doesNotMatch(explanation, /提前|本轮此前高|守稳/);
    const copy = formatFilledTradeCopy(
        { symbol: "sh.600825", variant: "lecture_v3", backtest: { start: "2018-01-01" }, asof: "2024-03-20", bars: [] },
        marker,
        "V3",
        "5%",
        null,
    );
    assert.ok(copy.includes(explanation));
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const panel = document.querySelector("section");
        appendTradeEvidence(panel, marker);
        assert.ok(panel.querySelector(".trade-reason-list").textContent.includes(explanation));
        assert.ok([...panel.children].some((child) => child.tagName === "P" && child.textContent === explanation));
        panel.replaceChildren();
        appendTradeEvidence(panel, { ...marker, side: "LONG", kind: "signal" });
        assert.ok([...panel.children].some((child) => child.tagName === "P" && child.textContent === explanation));
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});

test("attack-bar break with incomplete prices, dates or strong shape proof never reconstructs a complete explanation", () => {
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        for (const missing of [
            { confirmation_high: undefined },
            { confirmation_high: NaN },
            { confirmation_close: undefined },
            { n_attack_high: undefined },
            { n_attack_close: undefined },
            { n_attack_high: "4.68" },
            { attack_date: "" },
            { n_resistance_date: undefined },
            { confirmation_strong_bullish: undefined },
            { confirmation_strong_bullish: false },
            { confirmation_strong_bullish: "true" },
            { confirmation_body_open_ratio: undefined },
            { confirmation_body_range_ratio: undefined },
            { confirmation_upper_shadow_ratio: undefined },
            { confirmation_body_open_ratio: "0.043103" },
            { confirmation_body_range_ratio: NaN },
            { confirmation_upper_shadow_ratio: Infinity },
        ]) {
            const marker = {
                id: "attack-bar-break",
                time: "2024-03-20",
                side: "BUY",
                reason: "system_transition_squeeze",
                decision_evidence: [{ ...attackBarProof, ...missing }],
            };
            const reasons = numberedTradeReasons(marker).join("\n");
            assert.match(reasons, /缺少完整日期、价位或强势形态证据/);
            assert.doesNotMatch(reasons, /最高价|原 N 防守完整|已有空头抵抗|中大阳|短上影|undefined|NaN/);
            const copy = formatFilledTradeCopy(
                {
                    symbol: "sh.600825",
                    variant: "lecture_v3",
                    backtest: { start: "2018-01-01" },
                    asof: "2024-03-20",
                    bars: [],
                },
                marker,
                "V3",
                "5%",
                null,
            );
            assert.match(copy, /缺少完整日期、价位或强势形态证据/);
            assert.doesNotMatch(copy, /最高价|原 N 防守完整|已有空头抵抗|中大阳|短上影|undefined|NaN/);
            const panel = document.querySelector("section");
            panel.replaceChildren();
            appendTradeEvidence(panel, marker);
            assert.match(panel.textContent, /缺少完整日期、价位或强势形态证据/);
            assert.doesNotMatch(panel.textContent, /最高价|原 N 防守完整|已有空头抵抗|中大阳|短上影|undefined|NaN/);
        }
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});

test("attack-bar break is never inferred from prices without its source and keeps the record source independent", () => {
    for (const source of [undefined, "uninterrupted_squeeze", "resistance_record_break"]) {
        const marker = {
            side: "BUY",
            reason: "system_transition_squeeze",
            decision_evidence: [
                {
                    ...attackBarProof,
                    squeeze_confirmation: source,
                    confirmation_close: source === "resistance_record_break" ? 4.85 : attackBarProof.confirmation_close,
                },
            ],
        };
        assert.doesNotMatch(numberedTradeReasons(marker).join("\n"), /原正 N 突破棒确认|原 N 防守完整|收盘.*≥/);
        if (source === "resistance_record_break")
            assert.match(numberedTradeReasons(marker).join("\n"), /抵抗高点突破：2024-03-05 正 N，确认收盘.*>/);
    }
});

test("deep inverse recovery explains the original attack-bar path without losing its separate risk gates", () => {
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        for (const path of [
            "deep_alternation_kill_high_attack_bar_squeeze",
            "deep_alternation_kill_high_record_squeeze",
        ]) {
            const isAttackBar = path === "deep_alternation_kill_high_attack_bar_squeeze";
            const marker = {
                id: "deep-inverse-recovery",
                time: "2024-03-20",
                side: "BUY",
                reason: "system_transition_squeeze",
                decision_evidence: [
                    {
                        ...attackBarProof,
                        squeeze_confirmation: isAttackBar ? "resistance_attack_bar_break" : "resistance_record_break",
                        buy_point_type: "transition_squeeze",
                        inverse_reentry_path: path,
                        origin_index_date: "2023-12-12",
                        flip_high_index_date: "2024-01-02",
                        alternation_low_index_date: "2024-03-04",
                        recovery_whole_retracement: 0.8,
                        recovery_inverse_date: "2024-02-06",
                        recovery_kill_high: 4.6,
                    },
                ],
            };
            const panel = document.querySelector("section");
            panel.replaceChildren();
            appendTradeEvidence(panel, marker);
            const recovery = [...panel.children].find((child) => child.textContent.startsWith("深回撤恢复："));
            assert.match(
                recovery.textContent,
                /整段 2023-12-12 → 2024-01-02，2024-03-04 回撤 80\.00%；守住回调低点，收复 2024-02-06 杀多高 4\.6000/,
            );
            const copy = formatFilledTradeCopy(
                {
                    symbol: "sh.600825",
                    variant: "lecture_v3",
                    backtest: { start: "2018-01-01" },
                    asof: "2024-03-20",
                    bars: [],
                },
                marker,
                "V3",
                "5%",
                null,
            );
            assert.ok(copy.includes(recovery.textContent));
            if (isAttackBar) {
                assert.match(recovery.textContent, /至少 2\/3.*结构归属与可知时序成立.*收盘严格收复杀多高/);
                assert.match(recovery.textContent, /原正 N 突破棒的收盘与最高价严格双比较/);
                assert.doesNotMatch(recovery.textContent, /本轮前高|抵抗阶段高|新高|提前|实体/);
                assert.match(panel.textContent, /收盘 4\.8400 > 原突破棒收盘 4\.6400/);
            } else {
                assert.doesNotMatch(recovery.textContent, /原正 N 突破棒|严格双比较/);
            }
        }
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
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

test("pending shallow pullback breakout has dated and numbered buy reasons", () => {
    const marker = {
        side: "BUY",
        reason: "system_shallow_base_breakout",
        decision_evidence: [
            {
                buy_point_type: "shallow_base_breakout",
                origin_index_date: "2024-02-08",
                origin_price: 4.219,
                flip_high_index_date: "2024-12-12",
                flip_high_price: 8.9095,
                alternation_low_index_date: "2025-01-13",
                alternation_low_price: 5.8896,
                candidate_known_index_date: "2025-01-22",
                counter_ratio: 0.6438,
                base_sessions: 51,
                base_low: 6.1355,
                base_high: 7.0511,
                base_width_fraction: 0.1492,
                breakout_close: 7.1604,
                breakout_body_fraction: 0.1102,
                breakout_volume: 20135200,
                breakout_volume_multiple: 2.0459,
                stop: 6.1355,
                target: 8.9095,
            },
        ],
    };
    const reasons = numberedTradeReasons(marker);
    assert.equal(reasons.length, 4);
    assert.match(reasons[0], /0\.618 浅回撤待选后横盘放量突破/);
    assert.match(reasons[1], /2025-01-22 才成为待选/);
    assert.match(reasons[2], /51 根 K 线/);
    assert.match(reasons[3], /2\.05 倍/);
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

test("post-B target evidence names the new N origin and one-P milestone", () => {
    const marker = {
        kind: "fill",
        side: "SELL",
        reason: "wave_target_upper_shadow_reduce",
        wave_n_origin_date: "2020-06-12",
        wave_n_origin_price: 4.9199,
        wave_n_date: "2020-07-06",
        wave_reached_date: "2020-07-09",
        wave_reached_stage: "one_p",
        wave_reached_price: 5.751,
    };
    assert.match(tradeReasonItems(marker)[1], /新段正 N 起点 2020-06-12 4\.9199 元.*一饱 5\.7510 元/);
    const copy = formatFilledTradeCopy(
        { symbol: "sh.601086", variant: "lecture_v3", backtest: { start: "2020-01-01" }, asof: "2020-07-09", bars: [] },
        marker,
        "V3",
        "",
        null,
    );
    assert.match(copy, /新段正 N 起点 2020-06-12 4\.9199 元；正 N 2020-07-06.*一饱 5\.7510 元/);
    const document = new JSDOM("<div id='panel'></div>").window.document;
    const previousDocument = globalThis.document;
    globalThis.document = document;
    try {
        appendTradeEvidence(document.getElementById("panel"), marker);
        assert.match(
            document.getElementById("panel").textContent,
            /新段正 N 起点 2020-06-12 4\.9199 元；正 N 2020-07-06.*一饱 5\.7510 元/,
        );
    } finally {
        globalThis.document = previousDocument;
    }
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

test("bearish inside child reduction and next-session break show their own evidence", () => {
    const reduction = tradeReasonItems({
        side: "SELL",
        reason: "volume_bearish_child_reduce_70",
        mother_date: "2023-03-31",
        mother_close: 4.5,
        bearish_reference_date: "2023-03-29",
        bearish_reference_high: 4.38,
        bearish_reference_volume: 3_526_100,
        child_date: "2023-04-03",
        child_volume: 15_040_800,
        child_bearish_volume_multiple: 15_040_800 / 3_526_100,
    });
    assert.match(reduction.join(" "), /2023-03-31.*2023-03-29.*2023-04-03.*4\.27 倍.*减仓 70%/);
    const clear = tradeReasonItems({
        side: "SELL",
        reason: "volume_bearish_child_break_clear",
        child_date: "2023-04-03",
        child_low: 4.36,
        child_close: 4.4,
        observed_low: 4.26,
        observed_close: 4.3,
    });
    assert.match(clear.join(" "), /最低 4\.2600 < 子线低点 4\.3600.*收盘 4\.3000 < 子线收盘 4\.4000.*清空余仓/);
});

test("bearish child clear explains expanding volume on the bar breaking the child low", () => {
    const clear = tradeReasonItems({
        side: "SELL",
        reason: "bearish_mother_child_break_clear",
        child_date: "2020-08-12",
        child_low: 5.21,
        child_close: 5.34,
        observed_low: 4.96,
        observed_close: 5.08,
        child_volume: 15_799_059,
        observed_volume: 19_530_900,
        bearish_reference_date: "2020-08-12",
        bearish_reference_volume: 15_799_059,
    });
    assert.match(clear[0], /放量且低点、收盘均跌破阴子线.*清空余仓/);
    assert.match(clear.join(" "), /最低 4\.9600 < 子线低点 5\.2100.*收盘 5\.0800 < 子线收盘 5\.3400/);
    assert.match(
        clear.join(" "),
        /19,530,900 股.*子线量 15,799,059 股或此前 2020-08-12 最近阴线量 15,799,059 股.*直接清空余仓/,
    );
    assert.doesNotMatch(clear.join(" "), /无需放量/);
});

test("bearish mother and child explain volume before the pair and a bullish low break", () => {
    const reduction = tradeReasonItems({
        side: "SELL",
        reason: "volume_bearish_mother_child_reduce_70",
        mother_date: "2026-04-17",
        mother_low: 4.89,
        mother_high: 5.18,
        child_date: "2026-04-20",
        child_low: 4.89,
        child_high: 4.98,
        child_volume: 20_778_797,
        bearish_reference_date: "2026-04-15",
        bearish_reference_volume: 15_569_200,
    });
    assert.match(reduction.join(" "), /阴母阴子组合.*累计减仓 70%/);
    assert.match(reduction.join(" "), /2026-04-17.*4\.8900～5\.1800.*2026-04-20.*4\.8900～4\.9800/);
    assert.match(reduction.join(" "), /子线成交量 20,778,797 股 > 组合前 2026-04-15 最近阴线 15,569,200 股/);
    const clear = tradeReasonItems({
        side: "SELL",
        reason: "volume_bearish_mother_child_low_clear",
        mother_date: "2026-04-17",
        child_date: "2026-04-20",
        child_low: 4.89,
        child_close: 4.9,
        observed_low: 4.88,
        observed_close: 5.06,
    });
    assert.match(clear.join(" "), /最低 4\.8800 < 子线低点 4\.8900.*当日收盘清空余仓/);
    assert.match(clear.join(" "), /本日收盘 5\.0600，无需低于子线收盘 4\.9000/);
});

test("C-wave 0.618 exit explains the earlier target and later warning break", () => {
    const reduction = tradeReasonItems({
        side: "SELL",
        reason: "wave_c_0618_upper_shadow_reduce",
        wave_reached_stage: "c_0618",
        wave_reached_date: "2026-01-30",
        wave_reached_price: 5.50666,
        wave_c_0618_target: 5.50666,
        observed_close: 5.54,
        previous_close: 5.64,
        wave_upper_shadow_fraction: 0.45 / 0.55,
    });
    assert.match(reduction.join(" "), /2026-01-30.*5\.5067.*上影占振幅.*减仓 80%/);

    const clear = tradeReasonItems({
        side: "SELL",
        reason: "wave_c_0618_shadow_break_clear",
        abnormal_date: "2026-02-02",
        abnormal_low: 5.44,
        abnormal_close: 5.54,
        observed_low: 5.3,
        observed_close: 5.36,
        previous_close: 5.57,
    });
    assert.match(clear.join(" "), /2026-02-02.*5\.3000 < 警示低点 5\.4400.*5\.5400，清空余仓/);
});

test("pressure gap reduction and subsequent clear keep their distinct dated reasons", () => {
    const base = {
        side: "SELL",
        pressure_date: "2022-04-13",
        pressure_low: 7.2992,
        pressure_high: 7.6698,
        pressure_n_date: "2022-06-24",
        pressure_adverse_patterns: ["long_upper_shadow"],
        observed_low: 7.0007,
        previous_high: 6.9801,
        observed_close: 7.1242,
        previous_close: 6.9801,
    };
    const reduction = tradeReasonItems({ ...base, reason: "pressure_gap_adverse_reduce" });
    assert.match(reduction[0], /减仓 50%/);
    assert.match(reduction.at(-1), /最低 7\.0007 > 前高 6\.9801/);
    const clear = tradeReasonItems({
        ...base,
        reason: "pressure_reduced_lower_close_clear",
        pressure_warning_date: "2022-06-27",
        observed_close: 7.4228,
        previous_close: 7.6184,
    });
    assert.match(clear[0], /清空余仓/);
    assert.match(clear.at(-1), /2022-06-27 减仓后首次收跌/);
});

test("massive old-high bearish breakout explains frozen volume and next-session clear", () => {
    const base = {
        side: "SELL",
        record_high_date: "2021-09-22",
        record_high: 4.56,
        record_high_age: 118,
        record_breakout_date: "2022-03-21",
        record_warning_date: "2022-03-21",
        record_warning_close: 5.07,
        record_volume_baseline_date: "2022-03-18",
        record_volume_window: 20,
        record_volume_mean: 16_904_716,
        record_volume_threshold: 33_809_432,
        record_volume_min_ratio: 2,
    };
    const reduction = tradeReasonItems({
        ...base,
        reason: "record_high_massive_resistance_reduce_30",
        observed_volume: 194_599_622,
        record_volume_ratio: 194_599_622 / 16_904_716,
        observed_open: 5.34,
        observed_close: 5.07,
    });
    assert.match(reduction[0], /累计减仓 30%/);
    assert.match(reduction[1], /前期高点：2021-09-22.*4\.5600/);
    assert.match(reduction[2], /2022-03-18.*20 日均量.*11\.51 倍.*2\.00 倍/);
    assert.match(reduction[3], /2022-03-21.*5\.0700 < 开盘 5\.3400/);
    const clear = tradeReasonItems({
        ...base,
        reason: "record_high_massive_followthrough_clear",
        observed_volume: 152_565_213,
        record_volume_ratio: 152_565_213 / 16_904_716,
        observed_open: 4.7,
        observed_close: 4.42,
    });
    assert.match(clear[0], /次笔继续巨量收阴下跌/);
    assert.match(clear[2], /9\.03 倍.*2\.00 倍/);
    assert.match(clear[3], /次一交易日.*4\.4200 < 开盘 4\.7000.*警示收盘 5\.0700/);
    assert.match(clear[3], /清空余仓/);
});

test("old bullish record resistance explains the February reduction and lower close", () => {
    const base = {
        side: "SELL",
        record_high_date: "2021-09-10",
        record_high: 7.0007,
        record_high_age: 97,
        record_breakout_date: "2022-02-11",
    };
    const reduction = tradeReasonItems({
        ...base,
        reason: "record_high_resistance_reduce",
        record_adverse_patterns: ["close_below_previous", "long_upper_shadow"],
    });
    assert.match(reduction[0], /减仓 50%/);
    assert.match(reduction[1], /2021-09-10.*7\.0007/);
    assert.match(reduction[2], /2022-02-11.*长上影/);
    const clear = tradeReasonItems({
        ...base,
        reason: "record_high_lower_close_clear",
        record_resistance_dates: ["2022-02-11", "2022-02-14"],
        observed_close: 6.5683,
        previous_close: 7.1036,
    });
    assert.match(clear[0], /清空余仓/);
    assert.match(clear.at(-1), /2022-02-11、2022-02-14.*6\.5683 < 前收 7\.1036/);
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
