import process from "node:process";

import { expect, test } from "@playwright/test";

test("West Point level-3 flip high and re-break-confirmed alternation low remain visible in historical windows", async ({
    page,
}) => {
    test.setTimeout(120_000);
    await page.goto("/research?page=workspace");
    const endpoint = process.env.WAVEQUANT_E2E_API_URL || "";
    const response = await page.request.get(
        `${endpoint}/api/market-timeframe?source=tdx&symbol=sz.301130&asof=2026-09-07&timeframe=1d`,
        { timeout: 90_000 },
    );
    expect(response.ok()).toBe(true);
    const bundle = await response.json();
    const high = bundle.theory.tertiary_trends.bear_to_bull_highs.find((item) => item.time === "2025-08-11");
    const low = bundle.theory.tertiary_trends.bear_bull_alternation_lows.find((item) => item.time === "2026-07-21");
    expect(high).toMatchObject({ value: 36.98, available_at: "2026-09-07" });
    expect(low).toMatchObject({
        value: 21.88,
        available_at: "2026-09-07",
        confirmation_rule: "confirmed_higher_pullback_then_confirmed_flip_high_rebreak",
    });
    expect(low.confirmed_rebreak_high).toMatchObject({ time: "2026-08-20", value: 39.98 });

    const displayed = await page.evaluate(async (data) => {
        const { PriceChart } = await import("/charts.js");
        const container = globalThis.document.createElement("div");
        Object.assign(container.style, { position: "fixed", inset: "40px", height: "520px" });
        globalThis.document.body.append(container);
        const chart = new PriceChart(
            container,
            () => {},
            () => {},
        );
        chart.setData({ ...data.view, theory: data.theory });
        chart.setTheory(data.theory);
        const settle = () =>
            new Promise((resolve) => globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)));
        const find = (category, date) =>
            chart.groups
                .flatMap((group) => group.items)
                .find((item) => item.category === category && item.raw?.trend_level === 3 && item.time === date);
        chart.focus("2026-07-21");
        await settle();
        chart.refreshMarkers();
        const confirmedLow = find("trend-alternation-lows", "2026-07-21");
        chart.theory = { ...data.theory, asof: "2026-09-06" };
        chart.refreshMarkers();
        const lowAppearsEarly = Boolean(find("trend-alternation-lows", "2026-07-21"));
        chart.theory = data.theory;
        chart.focus("2025-08-11");
        await settle();
        chart.refreshMarkers();
        const confirmedHigh = find("trend-flip-highs", "2025-08-11");
        const result = {
            low: confirmedLow?.title,
            lowDescription: confirmedLow?.description,
            lowAppearsEarly,
            high: confirmedHigh?.title,
        };
        chart.destroy();
        container.remove();
        return result;
    }, bundle);
    expect(displayed.low).toContain("Ⅲ 空多交替低点");
    expect(displayed.lowDescription).toContain("39.98");
    expect(displayed.lowAppearsEarly).toBe(false);
    expect(displayed.high).toContain("Ⅲ 空翻多高点");
});
