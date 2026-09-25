import { expect, test } from "@playwright/test";

test("the default AkShare workspace does not request TDX until it is selected", async ({ page }) => {
    test.setTimeout(30_000);
    await page.addInitScript(() => globalThis.localStorage.setItem("wavequant.watchlists.auto-backtest.v1", "false"));
    const asof = "2026-09-24";
    const stocks = [
        { symbol: "sz.000001", name: "甲股票", has_data: true, last: asof },
        { symbol: "sz.000002", name: "乙股票", has_data: true, last: asof },
    ];
    const catalog = { available: true, latest: asof, with_daily: stocks.length, stocks };
    const theory = {
        asof,
        points: [],
        events: [],
        shapes: [],
        polyline_segments: [],
        lecture_drawing: { strokes: [], teaching_paths: [], inside_connections: [], issues: [] },
        reversal_trends: { strokes: [], developing_strokes: [] },
        secondary_trends: { strokes: [], developing_strokes: [] },
        tertiary_trends: { strokes: [], developing_strokes: [] },
    };
    await page.route("**/api/catalog", (route) =>
        route.fulfill({
            json: { runs: [{ id: "test-run", start: "2018-01-01", end: asof, symbols: [] }], variants: {} },
        }),
    );
    let akshareRequests = 0;
    await page.route("**/api/akshare-catalog", (route) => {
        akshareRequests++;
        return route.fulfill({ json: catalog });
    });
    let tdxRequests = 0;
    let releaseTdx;
    const tdxGate = new Promise((resolve) => (releaseTdx = resolve));
    await page.route("**/api/tdx-catalog", async (route) => {
        tdxRequests++;
        await tdxGate;
        await route.fulfill({ json: catalog }).catch(() => {});
    });
    await page.route("**/api/market-timeframe?*", (route) => {
        const params = new URL(route.request().url()).searchParams;
        const symbol = params.get("symbol");
        const source = params.get("source");
        const view = {
            symbol,
            asof,
            result_scope: source,
            data_source: source,
            price_basis: "raw_unadjusted",
            provider_version: "test",
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
            theory,
            evidence: "模拟行情",
        };
        return route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: `test-${source}-${symbol}`,
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view,
                theory,
            },
        });
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", "sz.000001");
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    expect(akshareRequests).toBe(1);
    expect(tdxRequests).toBe(0);

    await page.locator("#result-scope").selectOption("tdx");
    await expect.poll(() => tdxRequests).toBe(1);
    await expect(page.locator("#stock-source-notice")).toContainText("正在读取通达信股票目录");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", "sz.000001");
    releaseTdx();
    await expect(page.locator("#result-scope")).toHaveValue("tdx");
    await expect(page.locator("#loading")).toBeHidden();
    await page.locator("#result-scope").selectOption("akshare");
    await expect(page.locator("#loading")).toBeHidden();
    await page.locator("#result-scope").selectOption("tdx");
    await expect(page.locator("#loading")).toBeHidden();
    expect(tdxRequests).toBe(1);
    expect(akshareRequests).toBe(1);
});

test("an unavailable AkShare catalog stays selected until the user chooses TDX", async ({ page }) => {
    test.setTimeout(30_000);
    await page.addInitScript(() => globalThis.localStorage.setItem("wavequant.watchlists.auto-backtest.v1", "false"));
    const asof = "2026-09-24";
    const symbol = "sz.000001";
    await page.route("**/api/catalog", (route) =>
        route.fulfill({
            json: { runs: [{ id: "test-run", start: "2018-01-01", end: asof, symbols: [] }], variants: {} },
        }),
    );
    let akshareRequests = 0;
    await page.route("**/api/akshare-catalog", (route) => {
        akshareRequests++;
        return route.fulfill({ json: { available: false, with_daily: 0, stocks: [] } });
    });
    let tdxRequests = 0;
    await page.route("**/api/tdx-catalog", (route) => {
        tdxRequests++;
        return route.fulfill({
            json: {
                available: true,
                latest: asof,
                with_daily: 1,
                stocks: [{ symbol, name: "甲股票", has_data: true, last: asof }],
            },
        });
    });
    await page.route("**/api/market-timeframe?*", (route) => {
        const source = new URL(route.request().url()).searchParams.get("source");
        const theory = {
            asof,
            points: [],
            events: [],
            shapes: [],
            polyline_segments: [],
            lecture_drawing: { strokes: [], teaching_paths: [], inside_connections: [], issues: [] },
            reversal_trends: { strokes: [], developing_strokes: [] },
            secondary_trends: { strokes: [], developing_strokes: [] },
            tertiary_trends: { strokes: [], developing_strokes: [] },
        };
        return route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: `test-${source}-${symbol}`,
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view: {
                    symbol,
                    asof,
                    result_scope: source,
                    data_source: source,
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
                    theory,
                    evidence: "模拟行情",
                },
                theory,
            },
        });
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    await expect(page.locator("#error")).toContainText("AkShare目录暂无可用行情");
    expect(akshareRequests).toBe(1);
    expect(tdxRequests).toBe(0);
    await page.locator("#result-scope").selectOption("tdx");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", symbol);
    await expect(page.locator("#error")).toBeHidden();
    expect(tdxRequests).toBe(1);
});
