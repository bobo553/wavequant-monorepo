import { expect, test } from "@playwright/test";

test("idle watchlist backtests follow row order while stock controls stay usable", async ({ page }) => {
    test.setTimeout(45_000);
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const asof = "2026-09-24";
    const stocks = [
        { symbol: "sz.000001", name: "阿尔法样本", has_data: true, last: asof, source: "akshare" },
        { symbol: "sz.000002", name: "北京样本", has_data: true, last: asof, source: "akshare" },
    ];
    const view = (symbol, scope = "akshare") => ({
        run_id: "test-run",
        variant: "lecture_v3",
        scenario: "base",
        symbol,
        asof,
        result_scope: scope,
        data_source: "akshare",
        provider_version: "test",
        price_basis: "raw_unadjusted",
        timeframe: "1d",
        timeframe_label: "日线",
        sessions: [asof],
        bars: [{ time: asof, open: 10, high: 11, low: 9, close: 10.5, raw_close: 10.5, volume: 100 }],
        markers:
            scope === "stock" && symbol === "sz.000002"
                ? [
                      {
                          id: "auto-buy-1",
                          time: asof,
                          timestamp: `${asof}T00:00:00`,
                          signal_time: asof,
                          kind: "fill",
                          side: "BUY",
                          status: "filled",
                          price: 10.5,
                          raw_price: 10.5,
                          quantity: 100,
                          fee: 0,
                          reason: "fixture_entry",
                          symbol,
                      },
                  ]
                : [],
        signals: [],
        orders:
            scope === "stock" && symbol === "sz.000002"
                ? [
                      {
                          id: "auto-buy-1",
                          time: asof,
                          timestamp: `${asof}T00:00:00`,
                          signal_time: asof,
                          kind: "fill",
                          side: "BUY",
                          status: "filled",
                          price: 10.5,
                          raw_price: 10.5,
                          quantity: 100,
                          fee: 0,
                          reason: "fixture_entry",
                          symbol,
                      },
                  ]
                : [],
        trades: [],
        curve: [],
        metrics:
            scope === "stock"
                ? {
                      total_return: 0,
                      max_drawdown: 0,
                      trades: 0,
                      entry_fills: symbol === "sz.000002" ? 1 : 0,
                      average_exposure: 0,
                      unrealized_pnl: 0,
                      realized_pnl: 0,
                      total_pnl: 0,
                      final_equity: 100_000,
                      annualized_return: 0,
                      win_rate: null,
                      sharpe: 0,
                      fees: 0,
                      unexecuted_end_signals: 0,
                  }
                : null,
        ...(scope === "stock"
            ? {
                  backtest: {
                      status: "complete",
                      start: "2018-01-01",
                      end: asof,
                      initial_capital: 100_000,
                      strategy: {
                          volume_filter: true,
                          buy_point_definition: "whole_flip_wave_v3",
                          shallow_base_breakout_enabled: true,
                      },
                      execution: {
                          net_reward_risk_filter: false,
                          max_position_weight: 1,
                          missing_minute_daily_fallback: false,
                      },
                      diagnostics: {
                          rejection_reasons: {},
                          entry_fills: 0,
                          closed_trades: 0,
                          open_positions: 0,
                          entry_attempts: 0,
                      },
                      counts: { long_signals: 0 },
                      open_positions: [],
                  },
              }
            : {}),
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
    });
    await page.context().route("**/api/catalog", (route) =>
        route.fulfill({
            json: { runs: [{ id: "test-run", start: "2018-01-01", end: asof, symbols: [] }], variants: {} },
        }),
    );
    await page
        .context()
        .route("**/api/tdx-catalog", (route) =>
            route.fulfill({ json: { available: false, latest: asof, with_daily: 0, stocks: [] } }),
        );
    await page
        .context()
        .route("**/api/akshare-catalog", (route) =>
            route.fulfill({ json: { available: true, latest: asof, with_daily: 2, stocks } }),
        );
    await page.context().route("**/api/market-timeframe?*", (route) => {
        const symbol = new URL(route.request().url()).searchParams.get("symbol");
        return route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: `test-${symbol}`,
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view: view(symbol),
                theory: view(symbol).theory,
            },
        });
    });
    await page
        .context()
        .route("**/api/backtest-version?*", (route) => route.fulfill({ json: { version: "strategy-v1" } }));
    await page
        .context()
        .route("**/api/backtest-jobs*", (route) => route.fulfill({ json: { active: 0, max_active: 4, jobs: [] } }));
    let releaseFirst;
    const firstGate = new Promise((resolve) => (releaseFirst = resolve));
    let releaseSecond;
    const secondGate = new Promise((resolve) => (releaseSecond = resolve));
    const submitted = [];
    await page.context().route("**/api/akshare-backtest?*", async (route) => {
        const params = new URL(route.request().url()).searchParams;
        submitted.push({ symbol: params.get("symbol"), job: params.get("backtest_job") });
        if (submitted.length <= 2) await firstGate;
        if (params.get("symbol") === "sz.000002") await secondGate;
        await route.fulfill({ json: view(params.get("symbol"), "stock") }).catch(() => {});
    });
    await page.context().route("**/api/backtest-job?*", (route) => {
        const job = new URL(route.request().url()).searchParams.get("job");
        const submittedJob = submitted.find((item) => item.job === job);
        return route.fulfill(
            submittedJob
                ? { json: { status: "completed", result: view(submittedJob.symbol, "stock") } }
                : { status: 404, json: { error: "job not found" } },
        );
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden();
    await page.locator("#watchlist-toggle-current").click();
    await page.locator('.stock-item[data-symbol="sz.000002"]').click();
    await expect(page.locator("#symbol-select")).toHaveValue("sz.000002");
    await page.locator("#watchlist-toggle-current").click();
    await expect(page.locator(".watchlist-stock-row")).toHaveCount(2);
    await expect(page.locator("#watchlist-backtest-status")).toContainText("等待页面空闲");
    await expect.poll(() => submitted.length, { timeout: 12_000 }).toBe(1);
    expect(submitted[0].symbol).toBe("sz.000001");
    expect(submitted[0].job).toMatch(/^[0-9a-f-]{36}$/);
    await expect(page.locator('.watchlist-stock-row[data-symbol="sz.000001"] .watchlist-backtest-badge')).toHaveText(
        "回测中",
    );
    await page.locator('.watchlist-stock-row[data-symbol="sz.000001"] .watchlist-stock-open').click();
    await expect(page.locator("#symbol-select")).toHaveValue("sz.000001");
    await expect(page.locator("#loading")).toBeHidden();
    await page.locator("#run-stock-backtest").click();
    await expect.poll(() => submitted.length).toBe(2);
    expect(submitted[1].job).toBe(submitted[0].job);
    expect(submitted[1].symbol).toBe("sz.000001");
    releaseFirst();
    await expect(page.locator("#loading")).toBeHidden();
    await expect(page.locator('.watchlist-stock-row[data-symbol="sz.000001"] .watchlist-backtest-badge')).toHaveText(
        "已完成 · 0笔成交",
    );
    expect(submitted).toHaveLength(2);
    await expect.poll(() => submitted.length, { timeout: 12_000 }).toBe(3);
    expect(submitted[2].symbol).toBe("sz.000002");
    await page.locator('.watchlist-stock-row[data-symbol="sz.000002"] .watchlist-stock-open').click();
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    releaseSecond();
    await expect(page.locator("#result-scope")).toHaveValue("akshare-backtest");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-trade-label-count", "1");
    await expect(page.locator('.watchlist-stock-row[data-symbol="sz.000002"] .watchlist-backtest-badge')).toHaveText(
        "已完成 · 1笔成交",
    );
    await page.locator('.watchlist-stock-row[data-symbol="sz.000001"] .watchlist-stock-open').click();
    await expect(page.locator("#result-scope")).toHaveValue("akshare-backtest");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-trade-label-count", "0");
    await expect(page.locator("#selection-info")).toContainText("本次回测没有模拟成交");
    expect(submitted).toHaveLength(3);
    await page.locator('.watchlist-stock-row[data-symbol="sz.000002"] .watchlist-stock-open').click();
    await expect(page.locator("#result-scope")).toHaveValue("akshare-backtest");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-trade-label-count", "1");
    expect(submitted.at(-1).job).toBe(submitted[2].job);
    const submittedBeforeSecondPage = submitted.length;
    await expect(page.locator("#watchlist-backtest-status")).toContainText("自动回测已完成 2/2");
    await expect(page.locator("#watchlist-backtest-progress")).toHaveJSProperty("value", 2);
    const secondPage = await page.context().newPage();
    await secondPage.goto("/research?page=workspace");
    await expect(secondPage.locator("#loading")).toBeHidden();
    await expect(secondPage.locator(".watchlist-stock-row")).toHaveCount(2);
    await expect(secondPage.locator("#watchlist-backtest-status")).toContainText("自动回测已完成 2/2", {
        timeout: 12_000,
    });
    expect(submitted).toHaveLength(submittedBeforeSecondPage);
    expect(pageErrors).toEqual([]);
});
