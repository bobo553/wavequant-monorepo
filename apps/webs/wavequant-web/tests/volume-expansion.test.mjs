import assert from "node:assert/strict";
import { test } from "node:test";

import { candleCopyText, candleDetails } from "../public/candle-details.js";
import { VolumeExpansionOverlay, volumeExpansion } from "../public/volume-expansion.js";

test("volume tiers use unrounded ratios at every agreed boundary", () => {
    for (const [volume, marker, label] of [
        [0, "", "未达倍量"],
        [199.999, "", "未达倍量"],
        [200, "B2", "倍量"],
        [299.999, "B2", "倍量"],
        [300, "B3", "显著放量"],
        [499.999, "B3", "显著放量"],
        [500, "B5", "剧烈放量"],
        [999.999, "B5", "剧烈放量"],
        [1000, "B10+", "极端放量"],
    ]) {
        assert.equal(volumeExpansion(volume, 100).marker, marker);
        assert.equal(volumeExpansion(volume, 100).label, label);
    }
});

test("missing or invalid volume does not fabricate a ratio", () => {
    for (const previous of [undefined, null, 0, -1, NaN, Infinity, "100"])
        assert.equal(volumeExpansion(200, previous), null);
    for (const current of [undefined, null, -1, NaN, Infinity, "200"])
        assert.equal(volumeExpansion(current, 100), null);
});

test("tooltip and clipboard share ten-thousand shares and daily comparison", () => {
    const bar = { time: "2026-10-19", volume: 32600, close: 10 };
    const details = Object.fromEntries(candleDetails(bar, 10, 10000));
    assert.equal(details["成交量"], "3.26 万股");
    assert.equal(details["较前日"], "3.26 倍");
    assert.equal(details["放量等级"], "显著放量");
    assert.match(candleCopyText(bar, "", 10, 10000), /较前日：3\.26 倍/);
    assert.equal(Object.fromEntries(candleDetails(bar, 10, 0))["较前日"], "—");
    assert.equal(Object.fromEntries(candleDetails(bar, 10, 10000, "5m"))["较前日"], undefined);
});

test("overlay uses adjacent trading sessions and clears on hide or replacement", () => {
    const overlay = new VolumeExpansionOverlay({});
    const bars = [
        { time: "2026-10-16", volume: 100 },
        { time: "2026-10-19", volume: 300 },
        { time: "2026-10-20", volume: 3000 },
    ];
    overlay.setData(bars, true);
    assert.deepEqual(
        overlay.points.map(({ time, marker }) => [time, marker]),
        [
            ["2026-10-19", "B3"],
            ["2026-10-20", "B10+"],
        ],
    );
    overlay.setData(bars, false);
    assert.equal(overlay.points.length, 0);
    overlay.setData([{ time: "2026-10-21", volume: 90000 }], true);
    assert.equal(overlay.points.length, 0);
});
