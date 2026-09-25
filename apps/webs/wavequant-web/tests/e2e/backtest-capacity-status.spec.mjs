import { expect, test } from "@playwright/test";

test("server running status and capacity rejection reach the watchlist and toast", async ({ page }) => {
    const asof = "2026-09-24";
    const symbol = "sz.000978";
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => globalThis.localStorage.setItem("wavequant.watchlists.auto-backtest.v1", "false"));
    const view = {
        run_id: "test-run",
        variant: "lecture_v3",
        scenario: "base",
        symbol,
        asof,
        result_scope: "akshare",
        data_source: "akshare",
        provider_version: "test",
        price_basis: "raw_unadjusted",
        timeframe: "1d",
        timeframe_label: "日线",
        sessions: [asof],
        bars: [{ time: asof, open: 10, high: 11, low: 9, close: 10.5, raw_close: 10.5, volume: 100 }],
        markers: [],
        signals: [],
        orders: [],
        trades: [],
        curve: [],
        metrics: null,
        evidence: "模拟行情",
        theory: {
            asof,
            points: [],
            events: [],
            shapes: [],
            polyline_segments: [],
            lecture_drawing: { strokes: [], teaching_paths: [], inside_connections: [], issues: [] },
            reversal_trends: { strokes: [], developing_strokes: [] },
            secondary_trends: { strokes: [], developing_strokes: [] },
            tertiary_trends: { strokes: [], developing_strokes: [] },
        },
    };
    await page.route("**/api/catalog", (route) =>
        route.fulfill({
            json: { runs: [{ id: "test-run", start: "2018-01-01", end: asof, symbols: [] }], variants: {} },
        }),
    );
    await page.route("**/api/tdx-catalog", (route) =>
        route.fulfill({
            json: { available: false, latest: asof, with_daily: 0, stocks: [] },
        }),
    );
    await page.route("**/api/akshare-catalog", (route) =>
        route.fulfill({
            json: {
                available: true,
                latest: asof,
                with_daily: 1,
                stocks: [{ symbol, name: "桂林旅游", has_data: true, last: asof, source: "akshare" }],
            },
        }),
    );
    await page.route("**/api/market-timeframe?*", (route) =>
        route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: "test",
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view,
                theory: view.theory,
            },
        }),
    );
    await page.route("**/api/backtest-version?*", (route) => route.fulfill({ json: { version: "v1" } }));
    const running = [{ status: "running", symbol, job: "other-tab-job" }];
    await page.route("**/api/backtest-jobs*", (route) =>
        route.fulfill({
            json: { active: 1, max_active: 4, jobs: running },
        }),
    );
    let rejectedCalls = 0;
    await page.route("**/api/akshare-backtest?*", (route) => {
        rejectedCalls++;
        return route.fulfill({
            status: 503,
            json: {
                error: "backtest capacity reached; retry shortly",
                code: "BACKTEST_CAPACITY",
                active: 4,
                max_active: 4,
                jobs: running,
            },
        });
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden();
    await page.locator("#watchlist-toggle-current").click();
    const badge = page.locator(`.watchlist-stock-row[data-symbol="${symbol}"] .watchlist-backtest-badge`);
    await expect(badge).toHaveText("回测中");
    await page.locator("#run-stock-backtest").click();
    await expect(page.locator("#backtest-toast")).toContainText("并行回测已满（4/4）");
    expect(rejectedCalls).toBe(1);
    expect(errors).toEqual([]);
});
