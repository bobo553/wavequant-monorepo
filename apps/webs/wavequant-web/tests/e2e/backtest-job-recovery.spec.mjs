import { expect, test } from "@playwright/test";

test("a timed-out backtest follows its job, and changing the start date can retry after failure", async ({ page }) => {
    test.setTimeout(30_000);
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.addInitScript(() => {
        const nativeTimeout = AbortSignal.timeout.bind(AbortSignal);
        AbortSignal.timeout = (duration) => nativeTimeout(duration === 300_000 ? 150 : duration);
    });

    const asof = "2026-09-24";
    const symbol = "sz.000001";
    const nextSymbol = "sz.000002";
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
    const view = {
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
        theory,
    };
    const nextView = { ...view, symbol: nextSymbol };
    const catalog = {
        available: true,
        latest: asof,
        with_daily: 1,
        stocks: [
            {
                symbol,
                code: "000001",
                exchange: "深交所",
                name: "平安银行",
                has_data: true,
                last: asof,
                source: "akshare",
            },
            {
                symbol: nextSymbol,
                code: "000002",
                exchange: "深交所",
                name: "万科 A",
                has_data: true,
                last: asof,
                source: "akshare",
            },
        ],
    };
    await page.route("**/api/catalog", (route) =>
        route.fulfill({
            json: { runs: [{ id: "test-run", start: "2018-01-01", end: asof, symbols: [] }], variants: {} },
        }),
    );
    await page.route("**/api/tdx-catalog", (route) =>
        route.fulfill({ json: { available: false, latest: asof, with_daily: 0, stocks: [] } }),
    );
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/market-timeframe?*", (route) =>
        route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: "test-snapshot",
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view: new URL(route.request().url()).searchParams.get("symbol") === nextSymbol ? nextView : view,
                theory,
            },
        }),
    );

    const submittedJobs = [];
    const polledJobs = [];
    await page.route("**/api/akshare-backtest?*", async (route) => {
        const job = new URL(route.request().url()).searchParams.get("backtest_job");
        submittedJobs.push(job);
        if (submittedJobs.length === 1 || submittedJobs.length === 4) {
            // The browser aborts this slow response; the server-side task remains queryable.
            await new Promise((resolve) => setTimeout(resolve, 350));
            await route.fulfill({ json: view }).catch(() => {});
        } else if (submittedJobs.length === 2) {
            await route.fulfill({ status: 422, json: { error: "测试回测失败" } });
        } else {
            await route.fulfill({ json: submittedJobs.length === 5 ? nextView : view });
        }
    });
    await page.route("**/api/backtest-job?*", (route) => {
        polledJobs.push(new URL(route.request().url()).searchParams.get("job"));
        return route.fulfill(
            polledJobs.length === 2 || polledJobs.length === 4
                ? { status: 200, json: { status: "completed", result: view } }
                : { status: 202, json: { status: "running" } },
        );
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden();
    await expect(page.locator("#run-stock-backtest")).toBeEnabled();
    await page.locator("#run-stock-backtest").click();
    await expect(page.locator("#chart-loading-overlay")).toBeHidden();
    await expect(page.locator("#price-chart")).toBeVisible();
    await expect(page.locator("#stock-backtest-status")).toContainText("回测");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 10_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#selected-stock-summary")).toContainText("回测已完成");
    expect(submittedJobs).toHaveLength(1);
    expect(submittedJobs[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(polledJobs).toEqual([submittedJobs[0], submittedJobs[0]]);

    await page.locator("#backtest-start").fill("2024-01-01");
    await expect(page.locator("#error")).toContainText("测试回测失败");
    await expect(page.locator("#chart-loading-overlay")).toBeHidden();
    await expect(page.locator("#price-chart")).toBeVisible();
    await expect(page.locator("#selected-stock-summary")).toHaveAttribute("data-backtest-status", "failed");
    expect(submittedJobs).toHaveLength(2);
    expect(submittedJobs[1]).not.toBe(submittedJobs[0]);
    await expect(page.locator("#run-stock-backtest")).toBeEnabled();
    await page.getByRole("button", { name: "重新运行当前股票回测" }).click();
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#loading")).toBeHidden();
    expect(submittedJobs).toHaveLength(3);

    await page.locator("#run-stock-backtest").click();
    await expect.poll(() => submittedJobs.length).toBe(4);
    await expect(page.locator("#selected-stock-summary")).toHaveAttribute("data-backtest-status", "running");
    const nextStock = page.locator(`.stock-item[data-symbol="${nextSymbol}"]`);
    await expect(nextStock).toBeEnabled();
    await nextStock.click();
    await expect(page.locator("#symbol-select")).toHaveValue(nextSymbol);
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", nextSymbol);
    await expect(page.locator("#stock-backtest-status")).toContainText("后台回测");
    await expect.poll(() => polledJobs.length).toBe(4);
    expect(polledJobs[3]).toBe(submittedJobs[3]);
    await page.locator(`.stock-item[data-symbol="${symbol}"]`).click();
    await expect(page.locator("#selected-stock-summary")).toContainText("回测已完成");
    await expect(page.locator("#error")).toBeHidden();
    expect(submittedJobs).toHaveLength(4);
    expect(pageErrors).toEqual([]);
});
