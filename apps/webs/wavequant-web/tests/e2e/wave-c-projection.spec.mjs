import { expect, test } from "@playwright/test";

test("clicking an A-high candle draws the C-wave target from the later B low", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/research?page=workspace");
    await page.waitForFunction(() => Boolean(globalThis.LightweightCharts));
    await page.evaluate(async () => {
        const { PriceChart } = await import("/charts.js");
        const host = globalThis.document.createElement("div");
        host.id = "wave-c-test-chart";
        Object.assign(host.style, {
            position: "fixed",
            inset: "20px",
            height: "500px",
            background: "#111d2d",
            zIndex: "9999",
        });
        globalThis.document.body.append(host);
        const bars = [
            { time: "2026-01-01", open: 8.5, high: 10, low: 8, close: 9, volume: 1000 },
            { time: "2026-01-02", open: 10, high: 12, low: 10, close: 11, volume: 1000 },
            { time: "2026-01-03", open: 12, high: 14, low: 11, close: 13, volume: 1000 },
            { time: "2026-01-04", open: 13, high: 13, low: 12, close: 12, volume: 1000 },
            { time: "2026-01-05", open: 12, high: 12.5, low: 11.5, close: 12.2, volume: 1000 },
        ];
        const theory = {
            asof: "2026-01-05",
            shapes: [],
            points: [],
            polyline_segments: [],
            events: [
                {
                    id: "positive-n",
                    event: "n_completed",
                    direction: "up",
                    time: "2026-01-02",
                    available_at: "2026-01-02",
                    price: 11,
                    defense: 9,
                    shape: [{ time: "2026-01-01", value: 8 }],
                    levels: [{ name: "1P 投影", price: 12 }],
                },
            ],
        };
        const chart = new PriceChart(
            host,
            () => {},
            (items) => {
                host.dataset.selectedTitle = items[0].title;
                host.dataset.selectedDescription = items[0].description;
            },
        );
        chart.setData({ bars, markers: [], asof: "2026-01-05" });
        chart.setTheory(theory);
        chart.chart.timeScale().fitContent();
        globalThis.waveCTest = { chart, host };
    });
    const point = await page.evaluate(() => {
        const { chart, host } = globalThis.waveCTest;
        const rect = host.getBoundingClientRect();
        return {
            x: rect.left + chart.chart.timeScale().timeToCoordinate("2026-01-03"),
            y: rect.top + chart.candles.priceToCoordinate(13),
        };
    });
    await page.mouse.click(point.x, point.y);
    await expect(page.locator("#wave-c-test-chart")).toHaveAttribute("data-selected-title", "C 浪等浪观察目标");
    await expect(page.locator("#wave-c-test-chart")).toHaveAttribute(
        "data-selected-description",
        /B 浪低点 2026-01-05 11\.50.*17\.50 元/,
    );
    await expect(page.locator("#wave-c-test-chart")).toHaveAttribute("data-level-count", "1");
    const level = await page.evaluate(() => {
        const series = globalThis.waveCTest.chart.levelLines[0];
        return { data: series.data(), visible: series.options().priceLineVisible };
    });
    expect(level.data[0]).toEqual({ time: "2026-01-05", value: 17.5 });
    expect(level.visible).toBe(true);
    expect(errors).toEqual([]);
});
