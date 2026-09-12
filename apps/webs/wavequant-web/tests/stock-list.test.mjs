import assert from "node:assert/strict";
import test from "node:test";

import { filterStocks, stockInfo } from "../public/stock-list.js";

const stocks = ["sh.600519", "sh.600036", "sz.000858", "sz.000333"].map((symbol) => ({
    symbol,
    sessions: ["2026-09-07"],
}));
test("stock names and code searches cover all loaded symbols", () => {
    assert.equal(filterStocks(stocks).length, 4);
    assert.equal(filterStocks(stocks, "茅台")[0].name, "贵州茅台");
    assert.equal(filterStocks(stocks, "000858")[0].name, "五粮液");
    assert.equal(filterStocks(stocks, "SH600519")[0].symbol, "sh.600519");
    assert.equal(filterStocks(stocks, "ＳＨ６００５１９")[0].symbol, "sh.600519");
    assert.equal(filterStocks(stocks, "sh.600519")[0].symbol, "sh.600519");
});
test("search trims and matches all query terms; unknown query is empty", () => {
    assert.equal(filterStocks(stocks, "  sh  招商  ")[0].symbol, "sh.600036");
    assert.equal(filterStocks(stocks, "SZ").length, 2);
    assert.deepEqual(filterStocks(stocks, "不存在的股票"), []);
    assert.deepEqual(filterStocks([], "600519"), []);
    assert.equal(filterStocks(stocks, "   ").length, 4);
});
test("metadata name overrides local fallback, unknown symbols remain selectable", () => {
    assert.equal(stockInfo({ symbol: "sh.600519", name: "自定义名称" }).name, "自定义名称");
    assert.equal(stockInfo({ symbol: "bj.123456" }).name, "名称待补充");
    assert.equal(filterStocks([{ symbol: "bj.123456" }], "123456").length, 1);
    assert.equal(stockInfo(stocks[0]).exchange, "SH");
});
test("filtering never mutates stock universe or session history", () => {
    const before = structuredClone(stocks);
    filterStocks(stocks, "茅台");
    assert.deepEqual(stocks, before);
});
