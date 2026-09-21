import { expect, test } from "@playwright/test";

test("Xidian clears first adverse candle after resisted tertiary flip", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.301130", name: "西点药业", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    const response = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    const clear = view.orders.find((o) => o.side === "SELL" && o.timestamp.startsWith("2025-08-13"));
    expect(clear.status).toBe("filled");
    expect(clear.reason).toBe("trend_flip_resistance_adverse_clear");
    expect(clear.remaining_quantity).toBe(0);
    expect(clear.execution_model).toBe("same_day_close");
    expect(clear.trend_key_date).toBe("2023-08-11");
    expect(clear.trend_resistance_dates).toEqual(["2025-08-11", "2025-08-12"]);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2025-08-13" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("高层级翻多受阻后出现不利K线，当日清仓");
    await expect(page.locator("#selection-info")).toContainText("3级末跌高 2023-08-11");
    await expect(page.locator("#selection-info")).toContainText("36.3315");
    await expect(page.locator("#selection-info")).toContainText("阴线实体、收盘低于前收、最低价跌破前低");
    expect(errors).toEqual([]);
});
