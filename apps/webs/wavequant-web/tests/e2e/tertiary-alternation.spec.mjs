import process from "node:process";

import { expect, test } from "@playwright/test";

test("Ruiling tertiary alternation uses adjusted whole-wave evidence and a causal chart marker", async ({ page }) => {
    test.setTimeout(180_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const apiOrigin = process.env.WAVEQUANT_API_PORT ? `http://127.0.0.1:${process.env.WAVEQUANT_API_PORT}` : "";
    if (apiOrigin)
        await page.route("**/api/**", async (route) => {
            const url = new URL(route.request().url());
            const response = await route.fetch({ url: `${apiOrigin}${url.pathname}${url.search}`, timeout: 180_000 });
            await route.fulfill({ response });
        });
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator('#variant-select option[value="lecture_v3"]')).toContainText("二/三级交替后 N 轧空");
    const response = await page.request.get(
        `${apiOrigin}/api/tdx-backtest?run=acceptance_20260908_verified&variant=lecture_v3_d50_c50&scenario=base&symbol=sz.300154&asof=2026-09-07&start=2018-01-02`,
        { timeout: 120_000 },
    );
    expect(response.ok()).toBe(true);
    const view = await response.json();
    expect(view.backtest.strategy.first_pullback_threshold).toBe(0.5);
    const low = view.theory.tertiary_trends.bear_bull_alternation_lows.find((item) => item.time === "2026-07-21");
    expect(low).toMatchObject({
        available_at: "2026-08-07",
        trend_level: 3,
        confirmation_rule: "positive_n_and_squeeze_after_qualified_b",
    });
    const rejected = view.theory.events.filter(
        (event) => event.time === "2026-08-07" && event.event === "entry_rejected",
    );
    expect(rejected.map((event) => event.reason)).toEqual(["attack_volume_unavailable_or_low"]);
    expect(low.value).toBeCloseTo(9, 2);
    expect(low.confirmed_bear_low.value).toBeCloseTo(4.84, 2);
    expect(low.confirmed_flip_high.value).toBeCloseTo(16.8, 2);
    expect(low.retracement_ratio).toBeCloseTo(0.652324691, 8);

    const marker = await page.evaluate(async (data) => {
        const { PriceChart } = await import("/charts.js");
        const container = globalThis.document.createElement("div");
        container.id = "tertiary-alternation-regression";
        Object.assign(container.style, {
            position: "fixed",
            inset: "40px",
            height: "520px",
            background: "#111d2d",
            zIndex: "9999",
        });
        globalThis.document.body.append(container);
        const chart = new PriceChart(
            container,
            () => {},
            () => {},
        );
        chart.setData(data);
        chart.setTheory(data.theory);
        chart.focus("2026-07-21");
        await new Promise((resolve) =>
            globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
        );
        chart.refreshMarkers();
        const find = () =>
            chart.groups.find((group) =>
                group.items.some(
                    (item) =>
                        item.category === "trend-alternation-lows" &&
                        item.raw?.trend_level === 3 &&
                        item.time === "2026-07-21",
                ),
            );
        const group = find();
        const evidence = group?.items.find(
            (item) => item.raw?.trend_level === 3 && item.category === "trend-alternation-lows",
        );
        chart.theory = { ...data.theory, asof: "2026-08-06" };
        chart.refreshMarkers();
        const appearsEarly = !!find();
        chart.theory = data.theory;
        chart.refreshMarkers();
        await new Promise((resolve) =>
            globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
        );
        globalThis.alternationRegressionChart = chart;
        const bounds = container.getBoundingClientRect();
        return {
            marker: group?.marker,
            evidence,
            appearsEarly,
            x: bounds.left + chart.chart.timeScale().timeToCoordinate("2026-07-21"),
            y: bounds.top + chart.candles.priceToCoordinate(evidence.price),
        };
    }, view);
    expect(marker.appearsEarly).toBe(false);
    expect(marker.marker.position).toBe("atPriceBottom");
    expect(marker.evidence.title).toContain("Ⅲ 空多交替低点");
    expect(marker.evidence.description).toContain("65.23%");
    expect(marker.evidence.sourceLabel).toContain("正 N 轧空确认");
    expect(marker.evidence.description).toContain("2026-08-07");
    await page.mouse.move(marker.x, marker.y);
    await expect(page.locator("#tertiary-alternation-regression .chart-tooltip")).toContainText("Ⅲ 空多交替低点");
    await page.screenshot({ path: "test-results/rui-tertiary-alternation.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator("#tertiary-alternation-regression canvas").first()).toBeVisible();
    await page.evaluate(() => {
        globalThis.alternationRegressionChart.destroy();
        globalThis.document.querySelector("#tertiary-alternation-regression").remove();
    });
    expect(errors).toEqual([]);
});
