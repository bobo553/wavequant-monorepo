import assert from "node:assert/strict";
import test from "node:test";
import { researchSymbols, marketRequestDate } from "../public/research-symbols.js";

test("research choices come only from unique watchlist members and the explicit stock", () => {
    const result = researchSymbols([
        { symbol: "sz.002724", name: "海洋王", groupId: "default" },
        { symbol: "sz.002724", name: "海洋王", groupId: "other" },
        { symbol: "invalid", name: "无效" },
    ], "sz.000978", [{ symbol: "sz.000978", name: "桂林旅游", has_data: true }]);
    assert.deepEqual(result.map((stock) => stock.symbol), ["sz.002724", "sz.000978"]);
    assert.equal(result[1].name, "桂林旅游");
    assert.equal(result[0].status, "available_on_demand");
});

test("an empty watchlist stays empty and never expands into a whole-market catalog", () => {
    assert.deepEqual(researchSymbols([]), []);
    assert.deepEqual(researchSymbols([], null, [{ symbol: "sh.600000" }]), []);
});


test("without whole-market metadata the requested cutoff remains a valid China calendar date", () => {
    assert.equal(marketRequestDate(new Date("2026-10-23T16:30:00Z")), "2026-10-24");
    assert.equal(marketRequestDate(new Date("2026-10-23T15:30:00Z")), "2026-10-23");
});
