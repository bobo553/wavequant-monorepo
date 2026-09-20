import { expect, test } from "@playwright/test";

test("Ruiling backtest preserves AKShare and fills missing-minute days using daily closes", async ({
    page,
}, testInfo) => {
    test.setTimeout(180_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-07",
        with_daily: 1,
        stocks: [{ symbol: "sz.300154", name: "瑞凌股份", has_data: true, last: "2026-09-07", source: "tdx" }],
    };
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-01");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const response = page.waitForResponse((item) => item.url().includes("/api/akshare-backtest?"), {
        timeout: 120_000,
    });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    expect(view.backtest.execution.exit_on_target).toBe(false);
    expect(view.backtest.execution.inverse_n_after_reduction).toBe(true);
    const inverse = view.signals.find(
        (signal) => signal.time === "2025-05-19" && signal.reason === "inverse_n_risk_exit",
    );
    expect(inverse).toBeTruthy();
    const reduction = view.orders.find(
        (order) => order.timestamp.startsWith("2025-05-20") && order.reason === "inverse_n_after_reduction_90",
    );
    expect(reduction.signal_timestamp).toContain("2025-05-19");
    expect(reduction.exit_target_fraction).toBe(0.9);
    expect(reduction.remaining_quantity).toBeCloseTo(715.8750654, 5);
    expect(
        await page.evaluate(async () => (await import("/annotations.js")).reasonText("inverse_n_after_reduction_90")),
    ).toContain("累计减仓至原持仓 90%");
    expect(view.backtest.execution.staged_exit_intraday).toBe(true);
    expect(view.orders.some((order) => order.side === "SELL" && order.reason === "target_observed")).toBe(false);
    expect(view.orders.some((order) => order.side === "SELL" && order.timestamp.startsWith("2026-08-07"))).toBe(false);
    expect(view.backtest.source.provider).toBe("akshare");
    expect(view.backtest.source.upstream).toBe("sina");
    expect(view.backtest.source.minute.upstream).toBe("sina");
    expect(view.backtest.status).toBe("complete");
    expect(view.orders.some((order) => order.minute_fallback && order.execution_model === "same_day_close")).toBe(true);
    expect(view.metrics.entry_fills).toBeGreaterThan(0);
    expect(view.theory.events.length).toBeGreaterThan(0);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await expect(page.locator("#backtest-details")).toContainText("按当日日线收盘价模拟成交");
    await expect(page.locator("#download-backtest")).toBeEnabled();
    await expect(page.locator("#result-scope")).toHaveValue("akshare-backtest");
    await page.screenshot({ path: testInfo.outputPath("same-source-coverage.png") });
    expect(errors).toEqual([]);
});

test("clicking an N keeps distant targets visible and gates later levels during replay", async ({ page }, testInfo) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/research?page=workspace");
    await page.waitForFunction(() => Boolean(globalThis.LightweightCharts));
    await page.evaluate(async () => {
        const { PriceChart } = await import("/charts.js");
        const host = globalThis.document.createElement("div");
        host.id = "n-extension-chart";
        Object.assign(host.style, {
            position: "fixed",
            inset: "20px",
            height: "500px",
            background: "#111d2d",
            zIndex: "9999",
        });
        globalThis.document.body.append(host);
        const data = {
            asof: "2026-08-07",
            markers: [],
            bars: [
                { time: "2026-07-27", open: 9, high: 11, low: 9, close: 10, volume: 1000 },
                { time: "2026-07-28", open: 10, high: 11, low: 9.5, close: 10.5, volume: 1000 },
                { time: "2026-08-07", open: 11, high: 13, low: 10, close: 12, volume: 1000 },
            ],
        };
        const theory = {
            asof: data.asof,
            shapes: [],
            points: [],
            polyline_segments: [],
            events: [
                {
                    id: "n-extension-test",
                    event: "n_completed",
                    direction: "up",
                    time: "2026-07-27",
                    available_at: "2026-07-27",
                    price: 10,
                    levels: [
                        { name: "2T 投影", price: 12 },
                        { name: "五顶投影", price: 18, available_at: "2026-08-07" },
                        { name: "十满投影", price: 28, available_at: "2026-08-07" },
                    ],
                },
            ],
        };
        const chart = new PriceChart(host, () => {});
        chart.setData(data, { annotations: { rules: true, levels: true } });
        chart.setTheory(theory);
        chart.chart.timeScale().fitContent();
        chart.refreshMarkers();
        globalThis.nExtensionTest = { chart, theory, host };
    });
    const point = await page.evaluate(() => {
        const { chart, host } = globalThis.nExtensionTest;
        const rect = host.getBoundingClientRect();
        return {
            x: rect.left + chart.chart.timeScale().timeToCoordinate("2026-07-27"),
            y: rect.top + chart.candles.priceToCoordinate(10),
        };
    });
    await page.mouse.click(point.x, point.y);
    await expect(page.locator("#n-extension-chart")).toHaveAttribute("data-level-count", "3");
    await expect
        .poll(() =>
            page.evaluate(() => {
                const { chart, host } = globalThis.nExtensionTest;
                const y = chart.candles.priceToCoordinate(28);
                return y !== null && y > 0 && y < host.clientHeight;
            }),
        )
        .toBe(true);
    const levels = await page.evaluate(() =>
        globalThis.nExtensionTest.chart.levelLines.map((line) => ({
            title: line.options().title,
            points: line.data(),
        })),
    );
    expect(levels.map((level) => level.title)).toEqual(["2T 投影", "五顶投影", "十满投影"]);
    expect(levels[1].points[0].time).toBe("2026-08-07");
    await page.evaluate(() => {
        const { chart, theory } = globalThis.nExtensionTest;
        chart.setTheory({ ...theory, asof: "2026-07-27" });
        chart.refreshMarkers();
        chart.selectAnnotation("n-extension-test", false);
    });
    await expect(page.locator("#n-extension-chart")).toHaveAttribute("data-level-count", "1");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => {
        const { chart, theory } = globalThis.nExtensionTest;
        chart.setTheory(theory);
        chart.refreshMarkers();
        chart.selectAnnotation("n-extension-test", false);
    });
    await expect(page.locator("#n-extension-chart")).toHaveAttribute("data-level-count", "3");
    await page.screenshot({ path: testInfo.outputPath("n-extension-levels-mobile.png") });
    await page.evaluate(() => globalThis.nExtensionTest.chart.setAnnotationOptions({ levels: false }));
    await expect(page.locator("#n-extension-chart")).toHaveAttribute("data-level-count", "0");
    await page.evaluate(() => {
        globalThis.nExtensionTest.chart.destroy();
        globalThis.nExtensionTest.host.remove();
        delete globalThis.nExtensionTest;
    });
    expect(errors).toEqual([]);
});
