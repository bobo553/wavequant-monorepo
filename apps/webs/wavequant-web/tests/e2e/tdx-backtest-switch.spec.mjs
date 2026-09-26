import { expect, test } from "@playwright/test";

test("TDX stock switching keeps the earlier backtest alive and reuses its result", async ({ page }) => {
    test.setTimeout(30_000);
    await page.addInitScript(() => globalThis.localStorage.setItem("wavequant.watchlists.auto-backtest.v1", "false"));
    const asof = "2026-09-24";
    const first = "sz.000001";
    const second = "sz.000002";
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
    const view = (symbol, result_scope = "tdx") => ({
        symbol,
        asof,
        result_scope,
        data_source: result_scope === "stock" ? "tdx" : result_scope,
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
        theory,
        evidence: "模拟行情",
        metrics:
            result_scope === "stock"
                ? {
                      total_return: 0,
                      max_drawdown: 0,
                      trades: 0,
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
        ...(result_scope === "stock"
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
    });
    await page.route("**/api/catalog", (route) =>
        route.fulfill({
            json: { runs: [{ id: "test-run", start: "2018-01-01", end: asof, symbols: [] }], variants: {} },
        }),
    );
    await page.route("**/api/akshare-catalog", (route) =>
        route.fulfill({
            json: {
                available: true,
                latest: asof,
                with_daily: 2,
                stocks: [
                    { symbol: first, name: "甲股票", has_data: true, last: asof },
                    { symbol: second, name: "乙股票", has_data: true, last: asof },
                ],
            },
        }),
    );
    await page.route("**/api/tdx-catalog", (route) =>
        route.fulfill({
            json: {
                available: true,
                latest: asof,
                with_daily: 2,
                stocks: [
                    { symbol: first, name: "甲股票", has_data: true, last: asof },
                    { symbol: second, name: "乙股票", has_data: true, last: asof },
                ],
            },
        }),
    );
    await page.route("**/api/market-timeframe?*", (route) => {
        const params = new URL(route.request().url()).searchParams;
        const symbol = params.get("symbol");
        const source = params.get("source");
        return route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: `test-${symbol}`,
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view: view(symbol, source),
                theory,
            },
        });
    });
    let releaseFirst;
    const firstGate = new Promise((resolve) => (releaseFirst = resolve));
    const submitted = [];
    let akshareBacktestRequests = 0;
    await page.route("**/api/akshare-backtest?*", (route) => {
        akshareBacktestRequests++;
        return route.fulfill({ status: 500, body: "Unexpected AkShare backtest" });
    });
    await page.route("**/api/tdx-backtest?*", async (route) => {
        const params = new URL(route.request().url()).searchParams;
        const symbol = params.get("symbol");
        submitted.push({ symbol, job: params.get("backtest_job") });
        if (symbol === first) await firstGate;
        await route.fulfill({ json: view(symbol, "stock") }).catch(() => {});
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden();
    await page.locator("#result-scope").selectOption("tdx");
    await expect(page.locator("#loading")).toBeHidden();
    await page.locator("#run-stock-backtest").click();
    await expect.poll(() => submitted.filter((item) => item.symbol === first).length).toBe(1);
    await expect(page.locator("#chart-loading-overlay")).toBeHidden();
    await expect(page.locator("#stock-backtest-status")).toHaveAttribute("data-status", "running");

    await page.locator(`.stock-item[data-symbol="${second}"]`).click();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", second);
    await expect.poll(() => submitted.filter((item) => item.symbol === second).length).toBe(1);
    await expect(page.locator(`.stock-item[data-symbol="${first}"] .stock-backtest-badge`)).toHaveText("回测中");
    releaseFirst();
    await expect(page.locator(`.stock-item[data-symbol="${first}"] .stock-backtest-badge`)).toHaveText("已回测");

    await page.locator("#stock-picker-toggle").click();
    await page.locator(`.stock-item[data-symbol="${first}"]`).click();
    await expect(page.locator("#selected-stock-summary")).toContainText("回测已完成");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", first);
    await expect(page.locator("#chart-loading-overlay")).toBeHidden();
    expect(submitted.filter((item) => item.symbol === first)).toHaveLength(1);
    expect(akshareBacktestRequests).toBe(0);
});
