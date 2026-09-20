import assert from "node:assert/strict";
import test from "node:test";

import { tradingViewChartUrl, tradingViewSymbol, tradingViewWidgetConfig } from "../public/tradingview-widget.js";

test("local Shanghai and Shenzhen symbols map to TradingView exchange symbols", () => {
    assert.equal(tradingViewSymbol("sh.600519"), "SSE:600519");
    assert.equal(tradingViewSymbol("sz.002396"), "SZSE:002396");
    assert.equal(tradingViewSymbol("bj.920002"), null);
    assert.equal(tradingViewSymbol('sz.002396"}'), null);
});

test("advanced widget keeps its drawing and symbol toolbars available", () => {
    const config = tradingViewWidgetConfig("SZSE:002396", true);
    assert.equal(config.symbol, "SZSE:002396");
    assert.equal(config.hide_side_toolbar, false);
    assert.equal(config.hide_top_toolbar, false);
    assert.equal(config.allow_symbol_change, true);
    assert.equal(config.theme, "light");
    assert.equal(config.interval, "D");
    assert.equal(tradingViewChartUrl(config.symbol), "https://www.tradingview.com/chart/?symbol=SZSE%3A002396");
});
