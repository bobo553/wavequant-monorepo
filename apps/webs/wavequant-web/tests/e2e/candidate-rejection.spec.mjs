import { expect, test } from "@playwright/test";

test("rejected entry candidates expose each same-day strategy reason on hover", async ({ page }) => {
    test.setTimeout(180_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });

    const response = await page.request.get(
        "/api/tdx-backtest?run=acceptance_20260908_verified&variant=lecture_v3&scenario=base&symbol=sz.300154&asof=2026-09-07&start=2018-01-01",
        { timeout: 120_000 },
    );
    expect(response.ok()).toBe(true);
    const view = await response.json();
    const sameDay = view.theory.events.filter(
        (event) =>
            event.available_at === "2018-02-28" &&
            (event.event === "entry_rejected" || event.event === "entry_preflight_rejected"),
    );
    expect(sameDay).toHaveLength(2);

    const point = await page.evaluate(
        async ({ view, sameDay }) => {
            const { PriceChart } = await import("/charts.js");
            const container = globalThis.document.createElement("div");
            container.id = "candidate-rejection-fixture";
            Object.assign(container.style, {
                position: "fixed",
                left: "100px",
                top: "100px",
                width: "900px",
                height: "440px",
                background: "#111d2d",
                zIndex: "9999",
            });
            globalThis.document.body.append(container);
            const chart = new PriceChart(
                container,
                () => {},
                () => {},
            );
            chart.setData({ asof: view.asof, bars: view.bars, markers: [], trades: [] });
            chart.setTheory({ events: sameDay, shapes: [] }, false);
            chart.focus("2018-02-28");
            await new Promise((resolve) =>
                globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
            );
            globalThis.candidateRejectionFixture = chart;
            const group = chart.groups.find(
                (entry) => entry.time === "2018-02-28" && entry.items[0].category === "entry-rejections",
            );
            const bounds = container.getBoundingClientRect();
            return {
                marker: group?.marker,
                count: group?.items.length,
                x: bounds.left + chart.chart.timeScale().timeToCoordinate("2018-02-28"),
                y: bounds.top + chart.candles.priceToCoordinate(sameDay[0].price),
            };
        },
        { view, sameDay },
    );
    expect(point.count).toBe(2);
    expect(point.marker).toMatchObject({
        position: "atPriceBottom",
        color: "#8c9db599",
        shape: "circle",
        text: "",
    });
    await page.mouse.move(point.x, point.y);
    const tooltip = page.locator("#candidate-rejection-fixture .chart-tooltip");
    await expect(tooltip).toContainText("N 字攻击时尚无已确认的空多交替");
    await expect(tooltip).toContainText("不是轧空／强轧空盘");
    await expect(tooltip).toContainText("未提交买单");
    await page.evaluate(() => {
        globalThis.candidateRejectionFixture.destroy();
        globalThis.document.querySelector("#candidate-rejection-fixture").remove();
    });
    expect(errors).toEqual([]);
});
