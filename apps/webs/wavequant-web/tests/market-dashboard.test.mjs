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
    assert.match(runtime, /\/api\/akshare-view/);
    assert.match(runtime, /\/api\/akshare-theory/);
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
