import { expect, test } from "@playwright/test";

test("a completed manual backtest updates the matching watchlist row", async ({ page }) => {
    test.setTimeout(45_000);
    await page.addInitScript(() => globalThis.localStorage.setItem("wavequant.watchlists.auto-backtest.v1", "false"));
    const symbol = "sz.300154";
    const asof = "2026-09-24";
    const stock = { symbol, name: "瑞凌股份", has_data: true, last: asof, source: "akshare" };
    const view = (scope) => ({
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
        markers: [],
        signals: [],
        orders: [],
        trades: [],
        curve: [],
        metrics:
            scope === "stock"
                ? {
                      total_return: 0.125,
                      max_drawdown: 0,
                      trades: 0,
                      entry_fills: 0,
                      average_exposure: 0,
                      unrealized_pnl: 0,
                      realized_pnl: 0,
                      total_pnl: 12_500,
                      final_equity: 112_500,
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
    await page.context().route("**/api/akshare-catalog", (route) =>
        route.fulfill({
            json: { available: true, latest: asof, with_daily: 1, stocks: [stock] },
        }),
    );
    await page.context().route("**/api/market-timeframe?*", (route) =>
        route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: "manual-test",
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view: view("akshare"),
                theory: view("akshare").theory,
            },
        }),
    );
    await page
        .context()
        .route("**/api/backtest-version?*", (route) => route.fulfill({ json: { version: "strategy-v1" } }));
    let serverRecord = null;
    await page.context().route("**/api/backtest-jobs*", (route) =>
        route.fulfill({
            json: { active: 0, max_active: 4, jobs: [], recent: serverRecord ? [serverRecord] : [] },
        }),
    );
    let submitted = 0;
    let releaseBacktest;
    const backtestHold = new Promise((resolve) => {
        releaseBacktest = resolve;
    });
    await page.context().route("**/api/akshare-backtest?*", async (route) => {
        submitted++;
        await backtestHold;
        const query = new URL(route.request().url()).searchParams;
        const params = Object.fromEntries(query);
        serverRecord = {
            job: params.backtest_job,
            symbol,
            path: "/api/akshare-backtest",
            params: Object.fromEntries([...query].filter(([key]) => key !== "backtest_job")),
            version: "strategy-v1",
            status: "completed",
            result_valid: true,
            result_available: true,
            fill_count: 0,
            total_return: 0.125,
            total_pnl: 12_500,
        };
        return route.fulfill({ json: view("stock") });
    });

    await page.goto(`/research?page=workspace&symbol=${symbol}&source=akshare`);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 15_000 });
    await expect(page.locator("#watchlist-auto-backtest-toggle")).toHaveAttribute("aria-pressed", "false");
    await page.locator("#watchlist-toggle-current").click();
    const badge = page.locator(`.watchlist-stock-row[data-symbol="${symbol}"] .watchlist-backtest-badge`);
    await expect(badge).toHaveText("待回测");
    const runButton = page.locator("#run-stock-backtest");
    await expect(runButton).toHaveAttribute("data-status", "pending");
    await page.locator("#run-stock-backtest").click();
    await expect(badge).toHaveAttribute("data-status", "running");
    await expect(runButton).toHaveAttribute("data-status", "running");
    await expect(runButton).toBeDisabled();
    releaseBacktest();
    await expect(page.locator("#result-scope")).toHaveValue("akshare-backtest");
    await expect(badge).toHaveText("已完成 · 0笔成交");
    await expect(runButton).toHaveAttribute("data-status", "completed");
    await expect(runButton).toContainText("已完成 · 重新回测");
    await expect(runButton).toBeEnabled();
    const result = page.locator(`.watchlist-stock-row[data-symbol="${symbol}"] .watchlist-backtest-result`);
    await expect(result).toHaveText("盈 +12.50%");
    await expect(result).toHaveAttribute("title", /\+12,500\.00 元/);
    const rowLayout = await result.evaluate((element) => {
        const row = element.closest(".watchlist-stock-row");
        const name = row.querySelector(".watchlist-stock-identity strong");
        const badge = row.querySelector(".watchlist-backtest-badge");
        return {
            resultY: element.getBoundingClientRect().y,
            badgeY: badge.getBoundingClientRect().y,
            nameBottom: name.getBoundingClientRect().bottom,
            nameFits: name.scrollWidth <= name.clientWidth + 1,
            color: globalThis.getComputedStyle(element).color,
        };
    });
    expect(Math.abs(rowLayout.resultY - rowLayout.badgeY)).toBeLessThan(5);
    expect(rowLayout.resultY).toBeGreaterThan(rowLayout.nameBottom);
    expect(rowLayout.nameFits).toBe(true);
    const themeColor = (variable) =>
        page.evaluate((name) => {
            const sample = globalThis.document.createElement("span");
            sample.style.color = `var(${name})`;
            globalThis.document.querySelector(".watchlist-stock-row").append(sample);
            const color = globalThis.getComputedStyle(sample).color;
            sample.remove();
            return color;
        }, variable);
    expect(rowLayout.color).toBe(await themeColor("--red"));
    await expect(page.locator(".watchlist-stock-remove")).toHaveCount(0);
    await page.setViewportSize({ width: 1100, height: 900 });
    expect(
        await result.evaluate((element) =>
            Math.abs(
                element.getBoundingClientRect().y -
                    element
                        .closest(".watchlist-stock-row")
                        .querySelector(".watchlist-backtest-badge")
                        .getBoundingClientRect().y,
            ),
        ),
    ).toBeLessThan(5);
    await page.setViewportSize({ width: 390, height: 900 });
    const fits = await result.evaluate((element) => {
        const value = element.getBoundingClientRect();
        const rail = element.closest("#watchlist-rail").getBoundingClientRect();
        return value.right <= rail.right && value.left >= rail.left;
    });
    expect(fits).toBe(true);
    await expect(page.locator("#watchlist-backtest-status")).toContainText("已完成 1/1");
    expect(submitted).toBe(1);
    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 15_000 });
    await expect(badge).toHaveText("已完成 · 0笔成交");
    await expect(runButton).toHaveAttribute("data-status", "completed");
    await expect(result).toHaveText("盈 +12.50%");
    await expect(page.locator("#watchlist-backtest-status")).toContainText("已完成 1/1");
    expect(submitted).toBe(1);
    serverRecord.total_return = -0.075;
    serverRecord.total_pnl = -7_500;
    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 15_000 });
    await expect(result).toHaveText("亏 -7.50%");
    await expect(result).toHaveAttribute("title", /-7,500\.00 元/);
    expect(await result.evaluate((element) => globalThis.getComputedStyle(element).color)).toBe(
        await themeColor("--green"),
    );
    serverRecord.version = "older-strategy";
    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 15_000 });
    await expect(badge).toHaveText("已回测 · 待更新");
    await expect(runButton).toHaveAttribute("data-status", "historical");
    await expect(runButton).toContainText("已回测 · 待更新");
    await expect(result).toBeHidden();
    await expect(page.locator("#watchlist-backtest-status")).toContainText("已完成 0/1");
});
