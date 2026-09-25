import { expect, test } from "@playwright/test";

test("clicking a tertiary swing endpoint shows that leg's dashed thirds", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/research?page=workspace");
    await page.waitForFunction(() => Boolean(globalThis.LightweightCharts));
    const position = await page.evaluate(async () => {
        const { PriceChart } = await import("/charts.js");
        const container = globalThis.document.createElement("div");
        container.id = "selected-thirds-chart";
        Object.assign(container.style, {
            position: "fixed",
            inset: "20px",
            height: "600px",
            background: "#111d2d",
            zIndex: "9999",
        });
        globalThis.document.body.append(container);
        const chart = new PriceChart(container, () => {});
        const bars = [
            { time: "2026-01-01", open: 5, high: 6, low: 4, close: 5, volume: 100 },
            { time: "2026-01-04", open: 14, high: 16, low: 13, close: 15, volume: 100 },
            { time: "2026-01-08", open: 8, high: 9, low: 7, close: 8, volume: 100 },
            { time: "2026-01-12", open: 10, high: 11, low: 9, close: 10, volume: 100 },
        ];
        const proof = { time: "2026-01-12", label: "确认", value: 10 };
        const key = { time: "2026-01-01", label: "前高", value: 5 };
        const points = [
            {
                kind: "L",
                time: "2026-01-01",
                value: 4,
                available_at: "2026-01-03",
                label: "L",
                flip: "翻空为多",
                confirmed_by: proof,
                broken_key: key,
                levels: [],
            },
            {
                kind: "H",
                time: "2026-01-04",
                value: 16,
                available_at: "2026-01-06",
                label: "H",
                flip: "翻多为空",
                confirmed_by: proof,
                broken_key: key,
                levels: [],
            },
            {
                kind: "L",
                time: "2026-01-08",
                value: 7,
                available_at: "2026-01-10",
                label: "L2",
                flip: "翻空为多",
                confirmed_by: proof,
                broken_key: key,
                levels: [],
            },
        ];
        const stroke = { id: "selected-tertiary", kind: "tertiary", points };
        const empty = { strokes: [], developing_strokes: [] };
        const theory = {
            asof: "2026-01-12",
            shapes: [],
            events: [],
            lecture_drawing: empty,
            reversal_trends: empty,
            secondary_trends: empty,
            tertiary_trends: { ...empty, strokes: [stroke], bear_to_bull_highs: [] },
        };
        chart.setData({ bars, markers: [], asof: theory.asof });
        chart.setTheory(theory);
        chart.chart.timeScale().fitContent();
        await new Promise((resolve) =>
            globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
        );
        const projected = chart.lectureOverlay.projected.find((item) => item.stroke.id === stroke.id).points;
        globalThis.selectedThirdsChart = chart;
        globalThis.selectedThirdsFixture = { bars, theory };
        const bounds = container.getBoundingClientRect();
        return projected.map((point) => ({ x: bounds.left + point.x, y: bounds.top + point.y }));
    });
    await page.mouse.click(position[1].x, position[1].y);
    await expect(page.locator("#selected-thirds-chart")).toHaveAttribute("data-tertiary-retracement-guides", "2");
    const evidence = await page.evaluate(() => {
        const chart = globalThis.selectedThirdsChart;
        return {
            selected: chart.selected?.title,
            lines: chart.tertiaryRetracementLines.map((line) => ({
                title: line.options().title,
                style: line.options().lineStyle,
                points: line.data(),
            })),
        };
    });
    expect(evidence.selected).toContain("三级高点");
    expect(evidence.lines.map((line) => [line.title, line.style, line.points[0].value])).toEqual([
        ["Ⅲ 波段 1/3", 2, 12],
        ["Ⅲ 波段 2/3", 2, 8],
    ]);
    const lowPosition = await page.evaluate(async () => {
        const chart = globalThis.selectedThirdsChart;
        const { bars, theory } = globalThis.selectedThirdsFixture;
        chart.setData({ bars, markers: [], asof: theory.asof });
        chart.setTheory(theory);
        chart.chart.timeScale().fitContent();
        await new Promise((resolve) =>
            globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
        );
        const point = chart.lectureOverlay.projected.find((item) => item.stroke.id === "selected-tertiary").points[2];
        const bounds = chart.container.getBoundingClientRect();
        return { x: bounds.left + point.x, y: bounds.top + point.y };
    });
    await page.mouse.click(lowPosition.x, lowPosition.y);
    await expect
        .poll(() => page.evaluate(() => globalThis.selectedThirdsChart.tertiaryRetracementLines[0]?.data()[0]?.value))
        .toBe(13);
    expect(
        await page.evaluate(() =>
            globalThis.selectedThirdsChart.tertiaryRetracementLines.map((line) => line.data()[0].value),
        ),
    ).toEqual([13, 10]);
    await page.evaluate(() => globalThis.selectedThirdsChart.setTertiaryTrendVisible(false));
    await expect(page.locator("#selected-thirds-chart")).toHaveAttribute("data-tertiary-retracement-guides", "0");
    await page.evaluate(() => {
        globalThis.selectedThirdsChart.destroy();
        globalThis.document.querySelector("#selected-thirds-chart").remove();
    });
    expect(errors).toEqual([]);
});

test("Guofang 2025-05-29 developing high uses the January low", async ({ page }) => {
    test.setTimeout(120_000);
    const response = await page.request.get(
        "/api/market-timeframe?source=akshare&symbol=sh.601086&asof=2026-09-18&timeframe=1d",
        { timeout: 90_000 },
    );
    expect(response.ok(), await response.text()).toBe(true);
    const snapshot = await response.json();
    await page.goto("/research?page=workspace");
    await page.waitForFunction(() => Boolean(globalThis.LightweightCharts));
    const position = await page.evaluate(async ({ view, theory }) => {
        const { PriceChart } = await import("/charts.js");
        const container = globalThis.document.createElement("div");
        container.id = "guofang-thirds-chart";
        Object.assign(container.style, {
            position: "fixed",
            inset: "20px",
            height: "600px",
            background: "#111d2d",
            zIndex: "9999",
        });
        globalThis.document.body.append(container);
        const chart = new PriceChart(container, () => {});
        chart.setData(view);
        chart.setTheory(theory);
        chart.chart.timeScale().setVisibleRange({ from: "2025-01-01", to: "2025-06-30" });
        await new Promise((resolve) =>
            globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)),
        );
        const projected = chart.lectureOverlay.projected.find((item) => item.stroke.kind === "tertiary-developing");
        const point = projected.points.find((item) => item.point.time === "2025-05-29");
        globalThis.guofangThirdsChart = chart;
        const bounds = container.getBoundingClientRect();
        return { x: bounds.left + point.x, y: bounds.top + point.y };
    }, snapshot);
    await page.mouse.click(position.x, position.y);
    await expect(page.locator("#guofang-thirds-chart")).toHaveAttribute("data-tertiary-retracement-guides", "2");
    const evidence = await page.evaluate(() => {
        const chart = globalThis.guofangThirdsChart;
        return {
            scope: chart.selected?.raw?.scope,
            clicked: chart.selected?.raw?.point?.time,
            lines: chart.tertiaryRetracementLines.map((line) => line.data()),
        };
    });
    expect(evidence.scope).toBe("display_only_developing_path");
    expect(evidence.clicked).toBe("2025-05-29");
    expect(evidence.lines.map((line) => [line[0].time, line[0].value, line.at(-1).time])).toEqual([
        ["2025-01-13", 11.95, "2025-06-19"],
        ["2025-01-13", 8.129999999999999, "2026-04-24"],
    ]);
    await page.evaluate(() => {
        globalThis.guofangThirdsChart.destroy();
        globalThis.document.querySelector("#guofang-thirds-chart").remove();
    });
});
