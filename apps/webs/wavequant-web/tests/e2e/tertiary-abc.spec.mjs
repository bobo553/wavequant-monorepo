import process from "node:process";

import { expect, test } from "@playwright/test";

test("tertiary ABC boundary fixture renders candidate evidence, later breakout, replay and controls", async ({
    page,
}) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const apiOrigin = process.env.WAVEQUANT_API_PORT ? `http://127.0.0.1:${process.env.WAVEQUANT_API_PORT}` : "";
    if (apiOrigin)
        await page.route("**/api/**", async (route) => {
            const url = new URL(route.request().url());
            const response = await route.fetch({ url: `${apiOrigin}${url.pathname}${url.search}` });
            await route.fulfill({ response });
        });
    const response = await page.request.get(
        `${apiOrigin}/api/tdx-backtest?run=acceptance_20260908_verified&variant=lecture_v3&scenario=base&symbol=sz.300154&asof=2026-09-17&start=2018-01-01`,
        { timeout: 180_000 },
    );
    expect(response.ok(), await response.text()).toBe(true);
    const base = await response.json();
    expect(base.theory.tertiary_trends.bear_to_bull_highs[0].confirmed_low.value).toBeCloseTo(4.84, 2);
    for (const event of base.theory.events.filter((item) => item.event === "tertiary_c_candidate")) {
        expect(
            event.price_path ||
                (event.time_path && event.b_duration > event.a_duration && event.b_minimum_close < event.half_price),
        ).toBe(true);
        expect(["轧空", "强轧空"]).toContain(event.regime);
    }
    // A controlled boundary case exercises the time path even when the live
    // stock has no newly confirmed positive-N squeeze on the selected date.
    const prices = [
        [4, 5],
        [9, 10],
        [14, 15],
        [11, 12],
        [10, 11],
        [7, 9],
        [10, 11],
        [9, 10],
        [11, 12],
        [12, 13],
        [15, 17],
    ];
    const bars = prices.map(([low, close], i) => ({
        time: `2026-01-${String(i + 1).padStart(2, "0")}`,
        open: close,
        high: close + 1,
        low,
        close,
        volume: 100,
    }));
    const proof = {
        a_origin_index: 0,
        a_high_index: 2,
        b_low_index: 5,
        a_origin_price: 4,
        a_high_price: 16,
        b_low_price: 7,
        b_minimum_close: 9,
        half_price: 10,
        two_thirds_price: 8,
        a_duration: 2,
        b_duration: 3,
        price_path: false,
        time_path: true,
        attack: 8,
        candidate_index: 9,
        regime: "轧空",
        trend_level: 3,
        levels: [
            { name: "a 高点 / c 突破参考", price: 16 },
            { name: "a 的 1/2 回撤价", price: 10 },
        ],
    };
    const events = [
        {
            ...proof,
            event: "tertiary_c_candidate",
            id: "abc-candidate",
            time: bars[9].time,
            available_at: bars[9].time,
            price: 13,
        },
        {
            ...proof,
            event: "tertiary_c_breakout",
            id: "abc-breakout",
            time: bars[10].time,
            available_at: bars[10].time,
            price: 17,
        },
    ];
    const empty = { strokes: [], developing_strokes: [] };
    const theory = {
        ...base.theory,
        asof: bars[10].time,
        points: [],
        events,
        shapes: [],
        polyline_segments: [],
        lecture_drawing: empty,
        reversal_trends: empty,
        secondary_trends: empty,
        tertiary_trends: empty,
    };
    const view = { ...base, asof: bars[10].time, bars, markers: [], orders: [], trades: [], theory };
    await page.route("**/api/tdx-backtest?*", (route) => route.fulfill({ json: view }));
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
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-tertiary-abc-count", "2");
    await page.locator("#chart-layers-trigger").click();
    const toggle = page.locator("#show-tertiary-abc");
    await toggle.focus();
    await page.keyboard.press("Space");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-tertiary-abc-count", "0");
    await toggle.check();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-tertiary-abc-count", "2");
    await page.locator("#show-tertiary-trend").uncheck();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-tertiary-abc-count", "0");
    await page.locator("#show-tertiary-trend").check();
    await page.keyboard.press("Escape");
    const hit = await page.evaluate(async (data) => {
        const { PriceChart } = await import("/charts.js");
        const element = globalThis.document.createElement("div");
        element.id = "abc-regression";
        Object.assign(element.style, {
            position: "fixed",
            inset: "30px",
            height: "520px",
            zIndex: "9999",
            background: "#111d2d",
        });
        globalThis.document.body.append(element);
        const chart = new PriceChart(element, () => {});
        chart.setData(data);
        chart.setTheory({ ...data.theory, asof: "2026-01-10" });
        chart.refreshMarkers();
        const early = chart.groups.flatMap((group) => group.items).map((item) => item.title);
        chart.selectAnnotation("abc-candidate", false);
        chart.setTertiaryTrendVisible(false);
        const hiddenLevels = chart.levelLines.length;
        chart.setTertiaryTrendVisible(true);
        chart.setTheory(data.theory);
        await new Promise((resolve) =>
            globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
        );
        chart.refreshMarkers();
        globalThis.abcRegressionChart = chart;
        const box = element.getBoundingClientRect();
        return {
            early,
            hiddenLevels,
            x: box.left + chart.chart.timeScale().timeToCoordinate("2026-01-10"),
            y: box.top + chart.candles.priceToCoordinate(13),
        };
    }, view);
    expect(hit.early).toEqual(["Ⅲ c 段启动候选"]);
    expect(hit.hiddenLevels).toBe(0);
    await page.mouse.move(hit.x, hit.y);
    await expect(page.locator("#abc-regression .chart-tooltip")).toContainText("Ⅲ c 段启动候选");
    const description = await page.evaluate(
        () =>
            globalThis.abcRegressionChart.groups
                .flatMap((group) => group.items)
                .find((item) => item.id === "abc-candidate").description,
    );
    expect(description).toContain("b 3 根 > a 2 根");
    expect(description).toContain("< 1/2 回撤价");
    await page.screenshot({ path: "test-results/tertiary-abc-boundary-fixture.png" });
    await page.evaluate(() => {
        globalThis.abcRegressionChart.destroy();
        globalThis.document.querySelector("#abc-regression").remove();
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#chart-layers-trigger").click();
    await expect(toggle).toBeVisible();
    await toggle.uncheck();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-tertiary-abc-count", "0");
    expect(errors).toEqual([]);
});
