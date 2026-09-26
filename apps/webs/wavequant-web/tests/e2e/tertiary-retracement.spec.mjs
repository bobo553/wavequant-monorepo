import { expect, test } from "@playwright/test";

test("Ruiling thirds render at adjusted prices with persistent controls and causal cleanup", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    const unexpectedAkshareRequests = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
        if (["/api/akshare-catalog", "/api/akshare-backtest"].includes(new URL(request.url()).pathname))
            unexpectedAkshareRequests.push(request.url());
    });
    const catalog = {
        available: true,
        latest: "2026-09-07",
        with_daily: 1,
        stocks: [
            {
                symbol: "sz.300154",
                name: "瑞凌股份",
                has_data: true,
                last: "2026-09-07",
                bar_count: 3000,
                source: "tdx",
            },
        ],
    };
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace&symbol=sz.300154&asof=2026-09-07&source=tdx");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#symbol-select")).toHaveValue("sz.300154");
    const backtest = page.waitForResponse(
        (response) => response.url().includes("/api/tdx-backtest?") && response.ok(),
        { timeout: 180_000 },
    );
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const view = await (await backtest).json();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    const chart = page.locator("#price-chart");
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "2");
    await page.locator("#chart-layers-trigger").click();
    const toggle = page.locator("#show-tertiary-retracement");
    await expect(toggle).toBeChecked();
    await toggle.focus();
    await page.keyboard.press("Space");
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "0");
    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#chart-layers-trigger").click();
    await expect(toggle).not.toBeChecked();
    await toggle.check();
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "2");
    await page.locator("#show-tertiary-trend").uncheck();
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "0");
    await page.locator("#show-tertiary-trend").check();
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "2");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#result-scope")).toHaveValue("tdx-backtest");
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "2");
    await page.screenshot({ path: "test-results/rui-tertiary-thirds-workbench.png" });
    expect(unexpectedAkshareRequests).toEqual([]);

    const evidence = await page.evaluate(async (data) => {
        const { PriceChart } = await import("/charts.js");
        const container = globalThis.document.createElement("div");
        container.id = "thirds-regression";
        Object.assign(container.style, {
            position: "fixed",
            inset: "30px",
            height: "550px",
            background: "#111d2d",
            zIndex: "9999",
        });
        globalThis.document.body.append(container);
        const instance = new PriceChart(container, () => {});
        instance.setData(data);
        instance.setTheory(data.theory);
        instance.chart.timeScale().setVisibleRange({ from: "2024-01-01", to: data.bars.at(-1).time });
        await new Promise((resolve) =>
            globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
        );
        instance.refreshMarkers();
        const lines = instance.tertiaryRetracementLines.map((series) => ({
            title: series.options().title,
            data: series.data(),
        }));
        instance.theory = { ...data.theory, asof: "2026-08-25" };
        instance.refreshMarkers();
        const early = instance.tertiaryRetracementLines.length;
        instance.setTheory(data.theory);
        instance.clearTheory();
        instance.refreshMarkers();
        const hidden = instance.tertiaryRetracementLines.length;
        instance.setTheory(data.theory);
        instance.setData({ ...data, theory: null });
        const switched = instance.tertiaryRetracementLines.length;
        instance.setTheory(data.theory);
        globalThis.thirdsRegressionChart = instance;
        return { lines, early, hidden, switched };
    }, view);
    expect(evidence.lines.map((line) => line.title)).toEqual(["Ⅲ 回撤 1/3", "Ⅲ 回撤 2/3"]);
    expect(evidence.lines[0].data[0]).toMatchObject({ time: "2024-02-06" });
    expect(evidence.lines[0].data[0].value).toBeCloseTo(12.8107721093, 7);
    expect(evidence.lines[1].data[0].value).toBeCloseTo(8.8235311212, 7);
    expect(evidence.lines[0].data.at(-1).time).toBe("2025-04-07");
    expect(evidence.lines[1].data.at(-1).time).toBe(view.bars.at(-1).time);
    expect(evidence.early).toBe(0);
    expect(evidence.hidden).toBe(0);
    expect(evidence.switched).toBe(0);
    await page.evaluate(() =>
        globalThis.thirdsRegressionChart.chart.timeScale().setVisibleRange({ from: "2024-01-01", to: "2026-09-07" }),
    );
    await page.screenshot({ path: "test-results/rui-tertiary-thirds-wave.png" });
    await page.evaluate(() => globalThis.document.documentElement.classList.add("light"));
    await page.screenshot({ path: "test-results/rui-tertiary-thirds-light.png" });
    await page.evaluate(() => {
        globalThis.thirdsRegressionChart.destroy();
        globalThis.document.querySelector("#thirds-regression").remove();
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#chart-layers-trigger").click();
    await expect(toggle).toBeVisible();
    await toggle.uncheck();
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "0");
    await toggle.check();
    await expect(chart).toHaveAttribute("data-tertiary-retracement-guides", "2");
    await page.screenshot({ path: "test-results/rui-tertiary-thirds-mobile.png" });
    expect(errors).toEqual([]);
});
