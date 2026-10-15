import assert from "node:assert/strict";
import test from "node:test";
import { bearToBullHighAnnotations, bearBullAlternationLowAnnotations } from "../public/annotations.js";
import { lifecycleBullLandmarks, mergeLifecycleLandmarks, createLifecycleLandmarkReader } from "../public/lifecycle-landmarks.js";
import { EligibilityBoundaryOverlay } from "../public/eligibility-boundary-overlay.js";

// 缩减事件夹具核验图表投影，不作为真实行情全历史回测证据。
function fixture(level = 2) {
    const dates = ["2018-11-22", "2019-01-04", "2019-02-26", "2019-03-04",
        "2019-04-11", "2019-04-30", "2019-08-06", "2019-10-09", "2020-10-26"];
    const common = { symbol: "sh.600812", trend_level: level, source_path: `level-${level}`,
        direction: "up", cycle_started_index: 3, origin_index: 1, origin_price: 3.96,
        special_bull_recovery: true, counter_index: null, alternation_index: null };
    return { symbol: common.symbol, timeframe: "1d", asof: dates.at(-1),
        bars: dates.map((time) => ({ time, close: 5 })),
        backtest: { strategy: { buy_interval_level: level } },
        audit: [
            { ...common, timestamp: dates[3], bar_index: 3, available_index: 3,
                event: "trend_lifecycle_state", state: "空翻多", source_event: "短暂跌破后同级再次空翻多",
                flip_index: 2, flip_known_index: 3, breakout_level: 4.67,
                broken_high_index: 0, broken_high_price: 4.65 },
            { ...common, timestamp: dates[5], bar_index: 5, available_index: 5,
                event: "trend_lifecycle_anchor", state: "空翻多", source_event: "特殊空翻多同腿高点延伸",
                flip_index: 4, flip_known_index: 5, breakout_level: 5.78 },
            { ...common, timestamp: dates[7], bar_index: 7, available_index: 7,
                event: "trend_lifecycle_state", state: "空多交替", source_event: "短暂跌破后空多交替结束",
                flip_index: 4, flip_known_index: 5, breakout_level: 5.78,
                counter_index: 6, counter_ordinal: 0, counter_price: 4, alternation_index: 7 },
        ] };
}

for (const level of [1, 2, 3]) {
    test(`level ${level}: audit-only preview shows extended flip high and confirmed August low`, () => {
        const view = fixture(level);
        const result = lifecycleBullLandmarks(view)[level - 1];
        assert.equal(result.highs.length, 1);
        assert.equal(result.highs[0].time, "2019-04-11");
        assert.equal(result.highs[0].available_at, "2019-04-30");
        assert.equal(result.lows[0].time, "2019-08-06");
        assert.equal(result.lows[0].available_at, "2019-10-09");
        assert.equal(result.highs[0].broken_key.time, "2018-11-22");
        assert.equal(result.confirmations[0].time, "2019-03-04");
        const highs = bearToBullHighAnnotations([{ level, landmarks: result.highs }], "2019-01-01", view.asof);
        const lows = bearBullAlternationLowAnnotations([{ level, landmarks: result.lows }], "2019-01-01", view.asof);
        assert.match(highs[0].description, /特殊空翻多/);
        assert.match(lows[0].description, /守住新起涨低点，并满足既有交替结束规则/);
        assert.equal(lows[0].price, 4);
        const early = lifecycleBullLandmarks(view, "2019-08-19")[level - 1];
        assert.equal(early.lows.length, 0);
        assert.equal(early.highs[0].time, "2019-04-11");
        assert.equal(lifecycleBullLandmarks(view, "2019-03-01")[level - 1].highs.length, 0);
    });
}

test("completed and preview data share markers; same-leg old high and legacy duplicates are replaced", () => {
    const view = fixture();
    const before = lifecycleBullLandmarks(view, "2019-03-04")[1];
    const after = lifecycleBullLandmarks(view)[1];
    const legacy = [...before.highs, { ...after.highs[0], id: "legacy" }];
    assert.deepEqual(mergeLifecycleLandmarks(legacy, after.highs, after.superseded), after.highs);
    const preview = { ...view, asof: "2019-10-09", bars: view.bars.slice(0, 8) };
    assert.deepEqual(lifecycleBullLandmarks(preview)[1].lows, after.lows);
});

for (const level of [1, 2, 3]) {
    test(`level ${level}: N squeeze evidence survives lifecycle projection and annotation rendering`, () => {
        const view = fixture(level);
        const evidence = { regime: "强轧空", time_path: true, a_duration: 63, b_duration: 79,
            b_minimum_close: 4.09, half_price: 4.87 };
        Object.assign(view.audit.at(-1), { confirmation_rule: "positive_n_and_squeeze_after_qualified_b",
            confirmation_evidence: evidence, counter_source_level: 0 });
        const low = lifecycleBullLandmarks(view)[level - 1].lows[0];
        assert.deepEqual(low.confirmation_evidence, evidence);
        assert.equal(low.source_level, 0);
        const [annotation] = bearBullAlternationLowAnnotations([{ level, landmarks: [low] }], "2019-01-01", view.asof);
        assert.equal(annotation.price, 4);
        assert.deepEqual(annotation.raw.confirmation_evidence, evidence);
        assert.match(annotation.description, /正 N 与轧空确认/);
        for (const missing of [undefined, null]) {
            const [legacy] = bearBullAlternationLowAnnotations([{ level, landmarks: [
                { ...low, lifecycle: false, confirmation_evidence: missing },
            ] }], "2019-01-01", view.asof);
            assert.match(legacy.description, /确认细节未随本次快照提供/);
            const [partial] = bearBullAlternationLowAnnotations([{ level, landmarks: [
                { ...low, confirmation_evidence: missing },
            ] }], "2019-01-01", view.asof);
            assert.equal(partial.price, 4);
        }
    });
}

test("suspicion, candidate, other symbol and future confirmation never create markers", () => {
    const view = fixture();
    const flip = view.audit[0];
    view.audit = [
        { ...flip, source_event: "底部疑虑" },
        { ...flip, event: "trend_lifecycle_candidate" },
        { ...flip, symbol: "sz.000678" },
        { ...flip, available_index: 20 },
    ];
    assert.equal(lifecycleBullLandmarks(view)[1].highs.length, 0);
    assert.equal(lifecycleBullLandmarks({ ...fixture(), timeframe: "1m" })[1].highs.length, 0);
});

test("source revisions retract the active cycle; later bearish history does not erase confirmed bull markers", () => {
    const view = fixture();
    const invalidation = { ...view.audit[2], timestamp: "2020-10-26", bar_index: 8,
        event: "trend_lifecycle_invalidated", reason: "source_evidence_revised" };
    assert.equal(lifecycleBullLandmarks({ ...view, audit: [...view.audit, invalidation] })[1].lows.length, 0);
    const bear = { ...invalidation, event: "trend_lifecycle_state", direction: "down", state: "多翻空" };
    assert.equal(lifecycleBullLandmarks({ ...view, audit: [...view.audit, bear, invalidation] })[1].lows.length, 1);
});

test("cropped warmup is resolved by confirmation date rather than unadjusted market index", () => {
    const view = fixture();
    const full = lifecycleBullLandmarks(view)[1];
    const cropped = lifecycleBullLandmarks({ ...view, bars: view.bars.slice(1) })[1];
    assert.equal(cropped.highs[0].time, full.highs[0].time);
    assert.equal(cropped.lows[0].time, full.lows[0].time);
});

test("projection reuse follows snapshot/cutoff changes and flips alone never shade eligibility boundaries", () => {
    const view = fixture();
    const read = createLifecycleLandmarkReader();
    assert.equal(read(view, view.asof), read(view, view.asof));
    assert.notEqual(read(view, "2019-08-19"), read(view, view.asof));
    const overlay = new EligibilityBoundaryOverlay();
    overlay.setData(view);
    assert.deepEqual(overlay.ranges, []);
    overlay.setData({ ...view, backtest: { strategy: { buy_interval_level: 1 } } });
    assert.deepEqual(overlay.ranges, []);
});
