import { expect, test } from "@playwright/test";

test("Lexin target-stage bullish resistance failure clears on the earliest qualifying day", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-18",
        with_daily: 1,
        stocks: [{ symbol: "sz.300562", name: "乐心股份", has_data: true, last: "2026-09-18", source: "akshare" }],
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
    expect(view.signals.some((s) => s.side === "LONG" && s.time === "2022-01-21")).toBe(false);
    expect(view.orders.some((o) => o.side === "BUY" && o.timestamp.startsWith("2022-01-21"))).toBe(false);
    const clear = view.orders.find((o) => o.side === "SELL" && o.timestamp.startsWith("2022-01-05"));
    expect(clear.status).toBe("filled");
    expect(clear.reason).toBe("wave_bull_resistance_failed_clear");
    expect(clear.remaining_quantity).toBe(0);
    expect(clear.execution_model).toBe("same_day_close");
    expect(clear.resistance_date).toBe("2022-01-04");
    expect(view.orders.filter((o) => o.trade_id === clear.trade_id && o.side === "SELL").at(-1)).toEqual(clear);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2022-01-05" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("多头抵抗低点，当日清仓");
    await expect(page.locator("#selection-info")).toContainText("2022-01-04");
    await expect(page.locator("#selection-info")).toContainText("16.2844");
    await expect(page.locator("#selection-info")).toContainText("清空余仓，无需高开或再次放量");
    expect(errors).toEqual([]);
});
