import { env } from "node:process";

import { expect, test } from "@playwright/test";

test("Xinhua Wenxuan has one level-two path and visible level-three development", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sh.601811", name: "新华文轩", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    const apiOrigin = env["WAVEQUANT_E2E_API_ORIGIN"];
    if (apiOrigin) {
        await page.route("**/api/**", async (route) => {
            const target = new URL(route.request().url());
            const response = await route.fetch({
                url: `${apiOrigin}${target.pathname}${target.search}`,
                timeout: 180_000,
            });
            await route.fulfill({ response });
        });
    }
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.setViewportSize({ width: 1920, height: 1100 });
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const backtest = page.waitForResponse((response) => response.url().includes("/api/akshare-backtest?"), {
        timeout: 180_000,
    });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const view = await (await backtest).json();
    expect(view.theory.secondary_trends.strokes).toHaveLength(1);
    expect(view.theory.secondary_trends.strokes[0].points).toHaveLength(48);
    expect(view.theory.tertiary_trends.strokes[0].points.map((point) => point.time)).toEqual([
        "2019-03-13",
        "2021-02-04",
    ]);
    expect(view.theory.tertiary_trends.developing_strokes[0].points.some((point) => point.time === "2023-05-05")).toBe(
        true,
    );
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    const chart = page.locator("#price-chart");
    const bounds = await chart.boundingBox();
    await page.mouse.move(bounds.x + bounds.width - 100, bounds.y + bounds.height / 2);
    for (let index = 0; index < 30; index += 1) await page.mouse.wheel(0, 100);
    await expect.poll(async () => Number(await chart.getAttribute("data-secondary-points"))).toBeGreaterThan(0);
    await expect.poll(async () => Number(await chart.getAttribute("data-tertiary-points"))).toBeGreaterThan(0);
    await expect
        .poll(async () => Number(await chart.getAttribute("data-tertiary-developing-points")))
        .toBeGreaterThan(0);
    await page.screenshot({ path: "test-results/xinhuawenxuan-trends.png" });
    expect(errors).toEqual([]);
});
