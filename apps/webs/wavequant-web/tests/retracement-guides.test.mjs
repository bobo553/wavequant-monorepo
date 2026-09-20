import assert from "node:assert/strict";
import test from "node:test";

import { tertiaryRetracementGuides } from "../public/retracement-guides.js";

const high = {
    time: "2025-03-20",
    value: 16.798013097369935,
    available_at: "2026-08-26",
    confirmed_low: { time: "2024-02-06", value: 4.836290133074345, available_at: "2025-03-07" },
};
const bars = ["2024-02-06", "2025-03-20", "2026-07-21", "2026-08-25", "2026-08-26", "2026-09-17"].map((time) => ({
    time,
}));
const theory = { asof: "2026-09-17", tertiary_trends: { bear_to_bull_highs: [high] } };

test("Ruiling thirds divide the adjusted whole rise and extend through the later retracement", () => {
    const guides = tertiaryRetracementGuides(theory, bars, "2026-09-17");
    assert.equal(guides.length, 2);
    assert.equal(guides[0].price.toFixed(2), "12.81");
    assert.equal(guides[1].price.toFixed(2), "8.82");
    assert.equal(guides[0].start, "2024-02-06");
    assert.equal(guides[1].end, "2026-09-17");
    assert.ok(8.99508586012157 > guides[1].price);
    const rounded = tertiaryRetracementGuides(
        {
            ...theory,
            tertiary_trends: {
                bear_to_bull_highs: [{ ...high, value: 16.8, confirmed_low: { ...high.confirmed_low, value: 4.84 } }],
            },
        },
        bars,
    );
    assert.ok(Math.abs(rounded[1].price - 8.826666666666668) < 1e-12);
});

test("each third stops on its first strict low-price break, not on touch or later recovery", () => {
    const wave = {
        time: "2026-01-02",
        value: 16,
        available_at: "2026-01-03",
        confirmed_low: { time: "2026-01-01", value: 4, available_at: "2026-01-02" },
    };
    const candles = [
        { time: "2026-01-01", low: 4 },
        { time: "2026-01-02", low: 4 }, // The high candle is not a retracement.
        { time: "2026-01-03", low: 12 }, // Equality does not break 1/3.
        { time: "2026-01-04", low: 11.99 },
        { time: "2026-01-05", low: 8 }, // Equality does not break 2/3.
        { time: "2026-01-06", low: 7.99 },
        { time: "2026-01-07", low: 14 },
    ];
    const snapshot = (asof, visibleBars = candles) =>
        tertiaryRetracementGuides({ asof, tertiary_trends: { bear_to_bull_highs: [wave] } }, visibleBars);
    assert.deepEqual(
        snapshot("2026-01-03").map((guide) => guide.end),
        ["2026-01-03", "2026-01-03"],
    );
    assert.deepEqual(
        snapshot("2026-01-04").map((guide) => guide.end),
        ["2026-01-04", "2026-01-04"],
    );
    assert.deepEqual(
        snapshot("2026-01-07").map((guide) => guide.end),
        ["2026-01-04", "2026-01-06"],
    );
    assert.deepEqual(
        snapshot("2026-01-07", candles.slice(3)).map((guide) => guide.end),
        ["2026-01-06"],
    );
});

test("unknown candle lows do not invent a break", () => {
    const candles = bars.map((bar) => ({ ...bar, low: null }));
    candles[3].low = Number.NaN;
    assert.deepEqual(
        tertiaryRetracementGuides(theory, candles).map((guide) => guide.end),
        ["2026-09-17", "2026-09-17"],
    );
});

test("confirmation time gates anchors and replay clips the right endpoint", () => {
    assert.deepEqual(tertiaryRetracementGuides({ ...theory, asof: "2026-08-25" }, bars), []);
    assert.equal(tertiaryRetracementGuides({ ...theory, asof: "2026-08-26" }, bars)[0].end, "2026-08-26");
    assert.deepEqual(tertiaryRetracementGuides(theory, bars, "2024-02-06"), []);
    const futureLow = { ...high, confirmed_low: { ...high.confirmed_low, available_at: "2026-09-18" } };
    assert.deepEqual(
        tertiaryRetracementGuides({ ...theory, tertiary_trends: { bear_to_bull_highs: [futureLow] } }, bars),
        [],
    );
});

test("latest wave at the viewport endpoint wins without substituting candle extrema", () => {
    const older = {
        ...high,
        time: "2023-03-20",
        available_at: "2023-08-26",
        value: 12,
        confirmed_low: { time: "2022-02-06", value: 3 },
    };
    const fullBars = [{ time: "2022-01-01", high: 999 }, ...bars];
    const multiple = { ...theory, tertiary_trends: { bear_to_bull_highs: [high, older] } };
    assert.equal(tertiaryRetracementGuides(multiple, fullBars)[0].high, high);
    assert.equal(tertiaryRetracementGuides(multiple, fullBars, "2024-02-06")[0].high, older);
    assert.equal(tertiaryRetracementGuides(theory, bars.slice(2))[0].start, "2026-07-21");
});

test("empty, unknown, flat or inverted waves never draw guide prices", () => {
    assert.deepEqual(tertiaryRetracementGuides(null, bars), []);
    assert.deepEqual(tertiaryRetracementGuides(theory, []), []);
    for (const invalid of [
        { ...high, confirmed_low: null },
        { ...high, available_at: undefined },
        { ...high, value: NaN },
        { ...high, value: high.confirmed_low.value },
        { ...high, value: 2 },
        { ...high, confirmed_low: { time: "2025-04-01", value: 3 } },
    ]) {
        assert.deepEqual(
            tertiaryRetracementGuides({ ...theory, tertiary_trends: { bear_to_bull_highs: [invalid] } }, bars),
            [],
        );
    }
});
