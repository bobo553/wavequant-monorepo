import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const workspaceRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = join(workspaceRoot, "src");
const publicRoot = join(workspaceRoot, "public");

test("default page renders the React research workbench and market remains a Next.js route", () => {
    const page = readFileSync(join(sourceRoot, "app", "page.tsx"), "utf8");
    const layout = readFileSync(join(sourceRoot, "app", "layout.tsx"), "utf8");
    const marketPage = readFileSync(join(sourceRoot, "app", "market", "page.tsx"), "utf8");
    const dashboard = readFileSync(join(sourceRoot, "features", "market-dashboard", "market-dashboard.tsx"), "utf8");
    const packageJson = JSON.parse(readFileSync(join(workspaceRoot, "package.json"), "utf8"));
    assert.match(page, /ResearchWorkbench/);
    assert.doesNotMatch(page, /httpEquiv|research\.html/);
    assert.match(layout, /WaveQuantShell/);
    assert.doesNotMatch(marketPage, /MarketShell|MarketWorkspaceProvider/);
    assert.match(marketPage, /MarketDashboard/);
    assert.match(dashboard, /"use client"/);
    assert.equal(packageJson.dependencies.next, "^16.2.6");
    assert.equal(packageJson.dependencies.react, "^19.2.6");
    assert.equal(packageJson.dependencies["@repo/design-system-web"], "workspace:*");
});

test("research workbench exposes AkShare as a read-only online market source", () => {
    const controls = readFileSync(
        join(sourceRoot, "features", "research-workbench", "components", "research-controls.tsx"),
        "utf8",
    );
    const runtime = readFileSync(join(publicRoot, "app.js"), "utf8");
    assert.match(controls, /defaultValue="akshare"/);
    assert.ok(controls.indexOf('value="akshare"') < controls.indexOf('value="tdx"'));
    assert.match(controls, /value="akshare">AkShare · 在线 A 股行情/);
    assert.match(runtime, /\/api\/akshare-catalog/);
    assert.match(runtime, /loadMarketTimeframeSnapshot/);
    assert.match(runtime, /state\.akshareSessions/);
    assert.match(runtime, /source: isAkShare\(\) \? "akshare"/);
    assert.match(runtime, /查询当前股票买点/);
    assert.match(runtime, /查询全市场结构/);
    assert.match(runtime, /买点与结构仅读服务器预计算结果/);
    assert.match(runtime, /state\.akshare\.with_daily \? "akshare" : state\.tdx\.with_daily \? "tdx" : "stock"/);
    assert.match(runtime, /data\.source_fallback/);
    assert.match(runtime, /data\.supplemented_bars/);
    assert.doesNotMatch(runtime, /run-stock-backtest"\)\.disabled = !state\.tdx\?\.with_daily \|\| isAkShare/);
    assert.doesNotMatch(runtime, /if \(isAkShare\(\) && tab !== "all"\) return/);
});

test("research workbench provides categorized local watchlists and structure-result shortcuts", () => {
    const browser = readFileSync(
        join(sourceRoot, "features", "research-workbench", "components", "stock-browser.tsx"),
        "utf8",
    );
    const chart = readFileSync(
        join(sourceRoot, "features", "research-workbench", "components", "research-chart.tsx"),
        "utf8",
    );
    const rail = readFileSync(
        join(sourceRoot, "features", "research-workbench", "components", "watchlist-rail.tsx"),
        "utf8",
    );
    const runtime = readFileSync(join(publicRoot, "app.js"), "utf8");
    const watchlists = readFileSync(join(publicRoot, "watchlists.js"), "utf8");
    const structures = readFileSync(join(publicRoot, "structure-signals.js"), "utf8");

    for (const id of [
        "watchlist-group-select",
        "watchlist-group-add",
        "watchlist-group-rename",
        "watchlist-group-delete",
        "structure-watchlist-add-all",
    ])
        assert.match(browser + rail, new RegExp(`id="${id}"`));
    assert.match(rail, /id="watchlist-rail"/);
    assert.match(rail, /id="watchlist-rail-toggle"/);
    assert.match(chart, /id="watchlist-toggle-current"/);
    assert.doesNotMatch(browser, /watchlists-tab|watchlists-panel|watchlist-add-current/);
    assert.match(runtime, /new Watchlists/);
    assert.match(runtime, /await watchlists\.init\(\)/);
    assert.match(runtime, /watchlists\.setUniverse/);
    assert.match(structures, /structure-watchlist-add/);
    assert.match(structures, /button\.textContent = added \? "★" : "☆"/);
    assert.match(structures, /this\.watchlists\.remove\(result\.symbol\)/);
    assert.match(watchlists, /remove\.textContent = "★"/);
    assert.doesNotMatch(watchlists, /remove\.textContent = "移除"/);
    assert.match(structures, /addAllToWatchlist/);
    assert.match(watchlists, /wavequant-user-data/);
    assert.match(watchlists, /keyPath: \["groupId", "symbol"\]/);
    assert.match(watchlists, /默认分类不能删除/);
});

test("market browsing exposes server-backed daily through yearly candle timeframes", () => {
    const chart = readFileSync(
        join(sourceRoot, "features", "research-workbench", "components", "research-chart.tsx"),
        "utf8",
    );
    const runtime = readFileSync(join(publicRoot, "app.js"), "utf8");

    for (const [value, label] of [
        ["1d", "日"],
        ["1w", "周"],
        ["1mo", "月"],
        ["3mo", "季"],
        ["1y", "年"],
    ]) {
        assert.match(chart, new RegExp(`\\["${value}", "${label}"\\]`));
    }
    assert.match(chart, /id="timeframe-select"/);
    assert.match(chart, /role="tablist"/);
    assert.match(chart, /role="tab"/);
    assert.match(runtime, /loadMarketTimeframeSnapshot/);
    assert.match(runtime, /timeframe: request\.timeframe/);
    assert.match(runtime, /state\.akshareSessions\[`\$\{data\.symbol\}:\$\{data\.timeframe/);
    assert.match(runtime, /封存样本与策略回测保持日线口径/);
    assert.match(runtime, /is_partial_last_bar/);
});

test("current-stock backtests tolerate cold computation and report non-JSON proxy failures clearly", () => {
    const runtime = readFileSync(join(publicRoot, "app.js"), "utf8");

    assert.match(runtime, /path === "\/api\/tdx-backtest" \? 300000/);
    assert.match(runtime, /const text = await response\.text\(\)/);
    assert.match(runtime, /body = JSON\.parse\(text\)/);
    assert.match(runtime, /服务暂时不可用（HTTP/);
    assert.match(runtime, /首次回测计算超时，后台可能仍在生成缓存/);
    assert.doesNotMatch(runtime, /const body = await response\.json\(\)/);
});

test("chart controls use top-layer progressive disclosure without consuming candle height", () => {
    const chart = readFileSync(
        join(sourceRoot, "features", "research-workbench", "components", "research-chart.tsx"),
        "utf8",
    );
    const runtime = readFileSync(join(publicRoot, "app.js"), "utf8");
    const styles = readFileSync(join(publicRoot, "styles.css"), "utf8");

    assert.match(chart, /id="chart-layers-trigger"/);
    assert.match(chart, /id="chart-guide-trigger"/);
    assert.equal((chart.match(/popover="manual"/g) || []).length, 2);
    assert.match(chart, /data-chart-layer-toggle="true"/);
    assert.match(chart, /role="tooltip"/);
    assert.match(chart, /aria-describedby={helpId}/);
    assert.doesNotMatch(chart, /className="chart-legend"/);
    assert.doesNotMatch(chart, /className="trend-controls/);

    assert.match(runtime, /createChartPopoverController/);
    assert.match(runtime, /showPopover\(\)/);
    assert.match(runtime, /event\.key !== "Escape"/);
    assert.match(runtime, /layer-toggle-count/);
    assert.match(styles, /\.chart-tool-popover\[popover\]/);
    assert.match(styles, /position: fixed/);
    assert.match(styles, /:popover-open/);
    assert.match(styles, /\.chart-card \{[\s\S]*display: flex;[\s\S]*flex-direction: column;/);
    assert.match(styles, /min-height: max\(560px, calc\(100svh - 24px\)\)/);
    assert.match(styles, /\.price-chart \{[\s\S]*flex: 1 1 355px;[\s\S]*height: auto;/);
});

test("trade letters render above trend series and chart drawing primitives", () => {
    const chartRuntime = readFileSync(join(publicRoot, "charts.js"), "utf8");
    const appRuntime = readFileSync(join(publicRoot, "app.js"), "utf8");
    const ledgers = readFileSync(
        join(sourceRoot, "features", "research-workbench", "components", "research-ledgers.tsx"),
        "utf8",
    );
    assert.doesNotMatch(chartRuntime, /this\.tradeMarkers|createSeriesMarkers\(this\.candles, \[\], \{ zOrder: "top" \}\)/);
    assert.match(chartRuntime, /this\.candles\.attachPrimitive\(this\.tradeMarkerOverlay\)/);
    assert.match(chartRuntime, /this\.tradeMarkerOverlay\.setMarkers\(/);
    assert.match(chartRuntime, /g\.items\[0\]\.kind !== "fill"/);
    assert.match(appRuntime, /\$\("fills-only"\)\.disabled = markers\.length === 0/);
    assert.match(appRuntime, /\$\("fills-only"\)\.addEventListener\("click",[\s\S]*chart\.selectAnnotation\(marker\.id\);[\s\S]*requestAnimationFrame\(\(\) => \$\("price-chart"\)\.scrollIntoView/);
    assert.match(ledgers, /id="fills-only">仅看并定位成交/);
});

test("running a current-stock backtest focuses its latest actual B/S fill", () => {
    const runtime = readFileSync(join(publicRoot, "app.js"), "utf8");
    assert.match(runtime, /\$\("run-stock-backtest"\)\.addEventListener\("click",[\s\S]*loadView\(\{ focusLatestFill: true \}\)/);
    assert.match(runtime, /const latestFill = data\.markers\.filter\(\(marker\) => marker\.kind === "fill"\)\.at\(-1\)/);
    assert.match(runtime, /chart\.selectAnnotation\(latestFill\.id\)/);
    assert.match(runtime, /\$\("price-chart"\)\.scrollIntoView\(\{ block: "center", behavior: "instant" \}\)/);
    assert.match(runtime, /本次回测没有模拟成交/);
});

test("feature modules retain the overview, ladder, responsive and chart boundaries", () => {
    const dashboard = readFileSync(join(sourceRoot, "features", "market-dashboard", "market-dashboard.tsx"), "utf8");
    const chart = readFileSync(
        join(sourceRoot, "features", "market-dashboard", "components", "market-breadth-chart.tsx"),
        "utf8",
    );
    const styles = readFileSync(join(sourceRoot, "app", "globals.css"), "utf8");
    assert.match(dashboard, /OverviewView/);
    assert.match(dashboard, /LadderView/);
    assert.match(chart, /ResizeObserver/);
    assert.match(chart, /echarts\/core/);
    assert.match(styles, /@repo\/design-system-web\/globals\.css/);
    assert.match(styles, /data-theme="market-blue"/);
    for (const component of [
        "sector-view",
        "theme-view",
        "leader-view",
        "radar-view",
        "multi-stock-view",
        "review-view",
    ]) {
        assert.match(
            readFileSync(join(sourceRoot, "features", "market-dashboard", "components", `${component}.tsx`), "utf8"),
            /export function/,
        );
    }
});

test("the complete classic v2 interaction prototype remains available as a compatibility route", () => {
    const html = readFileSync(join(publicRoot, "wavequant-v2-classic.html"), "utf8");
    for (const navigation of ["market", "research", "backtest", "trading", "signals", "settings"]) {
        assert.match(html, new RegExp(`data-nav="${navigation}"`));
    }
    for (const behavior of ["mOverview", "mSectorView", "mLadderView", "mRadarView", "mMultiView", "mReviewView"]) {
        assert.match(html, new RegExp(`function ${behavior}`));
    }
});

test("the complete server-backed research workbench is composed from React feature components", () => {
    const featureRoot = join(sourceRoot, "features", "research-workbench");
    const workbench = readFileSync(join(featureRoot, "research-workbench.tsx"), "utf8");
    const runtime = readFileSync(join(featureRoot, "runtime", "research-runtime.tsx"), "utf8");
    const legacyRuntime = readFileSync(join(publicRoot, "app.js"), "utf8");
    const legacyStyles = readFileSync(join(publicRoot, "styles.css"), "utf8");
    const legacyCharts = readFileSync(join(publicRoot, "charts.js"), "utf8");
    const components =
        readFileSync(join(featureRoot, "components", "research-secondary-pages.tsx"), "utf8") +
        readFileSync(join(featureRoot, "components", "research-controls.tsx"), "utf8") +
        readFileSync(join(featureRoot, "components", "research-chart.tsx"), "utf8") +
        readFileSync(join(featureRoot, "components", "stock-browser.tsx"), "utf8");
    assert.match(workbench, /data-wavequant-react-workbench/);
    assert.match(workbench, /ResearchChart/);
    assert.match(workbench, /StockBrowser/);
    assert.match(workbench, /ResearchRuntime/);
    assert.match(runtime, /"\/app\.js"/);
    assert.match(legacyStyles, /--bg: var\(--background\)/);
    assert.match(legacyStyles, /--cyan: var\(--primary\)/);
    assert.match(legacyCharts, /new MutationObserver\(refreshChartThemes\)/);
    assert.match(legacyCharts, /token\("--card"/);
    assert.match(components, /name="structure-signal-type"[\s\S]*value="bullish_turn"[\s\S]*defaultChecked/);
    assert.match(components, /id: "show-bullish-turn-signals"/);
    assert.match(components, /label: "各级转多信号"/);
    assert.match(legacyCharts, /drawBullishTurnGuides/);
    assert.match(components, /className="loading-slot"/);
    assert.match(components, /id="chart-loading-overlay"/);
    assert.match(components, /className="chart-loading-spinner"/);
    assert.doesNotMatch(legacyRuntime, /el\.hidden = state\.loading \|\| state\.error/);
    assert.match(legacyRuntime, /const hasRenderedView = Boolean\(state\.view\)/);
    assert.match(legacyRuntime, /\$\("chart-loading-overlay"\)\.hidden = false/);
    assert.equal((legacyRuntime.match(/\$\("chart-loading-overlay"\)\.hidden = true/g) || []).length, 2);
    for (const id of [
        "price-chart",
        "run-stock-backtest",
        "scan-start",
        "ratio-results",
        "equity-chart",
        "orders-body",
        "health-body",
    ]) {
        assert.match(components, new RegExp(`(?:id|bodyId)="${id}"`));
    }
    for (const market of ["shanghai", "shenzhen", "chinext", "star", "beijing"]) {
        assert.match(components, new RegExp(`name="structure-market" value="${market}"`));
    }
    assert.equal(
        (components.match(/name="structure-market" value="(?:shanghai|shenzhen|chinext)" defaultChecked/g) || [])
            .length,
        3,
    );
    assert.match(components, /名称含 \* 的股票会被排除/);
});
