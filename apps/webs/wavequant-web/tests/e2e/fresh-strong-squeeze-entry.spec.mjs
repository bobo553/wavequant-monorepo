import { expect, test } from "@playwright/test";

test("Xianfeng buys at the first fresh strong squeeze on August 4", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.300163", name: "先锋新材", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const response = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    const order = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-08-04"));
    expect(order.status).toBe("filled");
    expect(order.execution_model).toBe("same_day_close");
    expect(order.reference_price).toBe(6.17);
    expect(order.trigger_timestamp).toContain("2026-07-31");
    expect(order.decision_evidence.find((e) => e.buy_point_type).inverse_reentry_path).toBe(
        "fresh_n_uninterrupted_strong_squeeze",
    );
    expect(view.orders.some((o) => o.side === "BUY" && o.timestamp.startsWith("2026-08-07"))).toBe(false);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-08-04" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("强轧空");
    await expect(page.locator("#selection-info")).toContainText("2026-07-31");
    expect(errors).toEqual([]);
});
