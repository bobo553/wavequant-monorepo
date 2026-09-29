import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { StockList, filterStocks, stockCoverageText, stockInfo } from "../public/stock-list.js";

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
test("online catalogs describe on-demand history without claiming zero bars", () => {
    assert.equal(
        stockCoverageText({ status: "available_on_demand", bar_count: null }),
        "按需读取 · 最新交易日以返回为准",
    );
    assert.equal(stockCoverageText({ bar_count: 6003, last: "2026-09-11" }), "6003 根日线 · 至 2026-09-11");
});

test("backtest polling updates stock badges without replacing focused rows", () => {
    const dom = new JSDOM('<div id="list"></div><span id="count"></span>');
    const previousDocument = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const list = document.getElementById("list");
        const subject = new StockList({ list, count: document.getElementById("count"), onSelect() {} });
        subject.setStocks(stocks, stocks[0].symbol);
        const row = list.querySelector('.stock-item[data-symbol="sh.600519"]');
        row.focus();

        subject.setBacktestStatuses({ "sh.600519": "running" }, [
            { symbol: "sh.600519", status: "running", elapsed_seconds: 63 },
        ]);
        assert.equal(list.querySelector('.stock-item[data-symbol="sh.600519"]'), row);
        assert.equal(document.activeElement, row);
        assert.equal(row.querySelector(".stock-backtest-badge").textContent, "回测中");
        assert.match(row.getAttribute("aria-label"), /回测中/);
        const duration = row.querySelector(".stock-backtest-elapsed");
        assert.equal(duration.textContent, "已运行 1 分钟");

        subject.setBacktestStatuses({ "sh.600519": "running" }, [
            { symbol: "sh.600519", status: "running", elapsed_seconds: 120 },
        ]);
        assert.equal(list.querySelector('.stock-item[data-symbol="sh.600519"]'), row);
        assert.equal(row.querySelector(".stock-backtest-elapsed"), duration);
        assert.equal(duration.textContent, "已运行 2 分钟");

        subject.setBacktestStatuses({ "sh.600519": "completed" });
        assert.equal(list.querySelector('.stock-item[data-symbol="sh.600519"]'), row);
        assert.equal(row.querySelector(".stock-backtest-badge").textContent, "已回测");
        assert.equal(row.querySelector(".stock-backtest-elapsed"), null);

        subject.setBacktestStatuses({});
        assert.equal(list.querySelector('.stock-item[data-symbol="sh.600519"]'), row);
        assert.equal(row.querySelector(".stock-backtest-badge"), null);
        assert.equal(row.getAttribute("aria-label"), "贵州茅台 600519 SH");
    } finally {
        dom.window.close();
        if (previousDocument === undefined) delete globalThis.document;
        else globalThis.document = previousDocument;
    }
});
