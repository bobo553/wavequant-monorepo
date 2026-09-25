import { expect, test } from "@playwright/test";

test("current-stock risk rejection appears in its buy signal tooltip without another dot", async ({ page }) => {
    test.setTimeout(180_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));

    await page.goto("/research?page=workspace&symbol=sh.600519&asof=2026-09-07&source=tdx");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#result-scope")).toHaveValue("tdx");
    const backtestResponse = page.waitForResponse(
        (response) => response.url().includes("/api/tdx-backtest?") && response.ok(),
    );
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const view = await (await backtestResponse).json();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#show-risk-rejections")).toHaveCount(0);
    const rejected = view.markers.find((marker) => marker.reason === "risk_budget_below_one_lot");
    expect(rejected).toBeDefined();
    const signal = view.markers.find(
        (marker) =>
            marker.kind === "signal" &&
            marker.side === "LONG" &&
            marker.time === rejected.signal_time &&
            marker.price === rejected.reference_price,
    );
    expect(signal).toBeDefined();
    const index = view.bars.findIndex((bar) => bar.time === rejected.time);
    expect(index).toBeGreaterThan(2);
    const bars = view.bars.slice(index - 4, index + 4);

    // Keep the signal and its next-open rejected order together in a small chart,
    // without changing the main workbench's current zoom/pan state.
    const point = await page.evaluate(
        async ({ bars, rejected, signal }) => {
            const { PriceChart } = await import("/charts.js");
            const container = globalThis.document.createElement("div");
            container.id = "risk-rejection-fixture";
            Object.assign(container.style, {
                position: "fixed",
                left: "100px",
                top: "100px",
                width: "800px",
                height: "420px",
                background: "#111d2d",
                zIndex: "9999",
            });
            globalThis.document.body.append(container);
            const chart = new PriceChart(
                container,
                () => {},
                (items) => {
                    container.dataset.selected = items[0].id;
                },
            );
            chart.setData({ asof: bars.at(-1).time, bars, markers: [signal, rejected], trades: [] });
            chart.chart.timeScale().fitContent();
            await new Promise((resolve) =>
                globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
            );
            globalThis.riskRejectionFixture = chart;
            const marker = chart.groups.find((group) => group.id === signal.id)?.marker;
            const bounds = container.getBoundingClientRect();
            return {
                marker,
                groups: chart.groups.map((group) => group.id),
                x: bounds.left + chart.chart.timeScale().timeToCoordinate(signal.time),
                y: bounds.top + chart.candles.priceToCoordinate(signal.price),
            };
        },
        { bars, rejected, signal },
    );

    expect(point.groups).toEqual([signal.id]);
    expect(point.marker).toMatchObject({
        color: "#49d5dc",
        shape: "circle",
        position: "belowBar",
    });
    await page.mouse.move(point.x, point.y);
    await expect(page.locator("#risk-rejection-fixture .chart-tooltip")).toContainText("单笔风险预算不足以买入一手");
    await expect(page.locator("#risk-rejection-fixture .chart-tooltip")).toContainText("未实际买入");
    await page.mouse.click(point.x, point.y);
    await expect(page.locator("#risk-rejection-fixture")).toHaveAttribute("data-selected", signal.id);
    await page.evaluate(() => {
        globalThis.riskRejectionFixture.destroy();
        globalThis.document.querySelector("#risk-rejection-fixture").remove();
    });
    await page.locator("#fills-only").click();
    await expect(page.locator("#show-markers")).not.toBeChecked();
    expect(errors).toEqual([]);
});
