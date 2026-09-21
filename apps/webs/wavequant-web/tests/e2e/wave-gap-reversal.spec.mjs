import { expect, test } from "@playwright/test";

test("Ruiling reduces at five top despite a positive daily return", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.300154", name: "瑞凌股份", has_data: true, last: "2026-09-21", source: "akshare" }],
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
    const reduction = view.orders.find((o) => o.side === "SELL" && o.timestamp.startsWith("2024-10-08"));
    expect(reduction.status).toBe("filled");
    expect(reduction.reason).toBe("wave_gap_reversal_reduce");
    expect(reduction.exit_target_fraction).toBe(0.8);
    expect(reduction.wave_reached_stage).toBe("five_top");
    expect(reduction.execution_model).toBe("same_day_close");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2024-10-08" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("放量跳空高开大幅回落");
    await expect(page.locator("#selection-info")).toContainText("即使收盘高于前收也触发减仓");
    await expect(page.locator("#selection-info")).toContainText("80.00%");
    expect(errors).toEqual([]);
});
