import { expect, test } from "@playwright/test";

test("Xianfeng buy explains resolved secondary pressure and disabled reward-risk filter", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-06-09",
        with_daily: 1,
        stocks: [{ symbol: "sz.300163", name: "先锋新材", has_data: true, last: "2026-06-09", source: "akshare" }],
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
    const buy = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-06-09"));
    expect(buy.status).toBe("filled");
    expect(buy.net_reward_risk_filter).toBe(false);
    expect(buy.entry_conditions.at(-1).passed).toBeNull();
    const proof = buy.decision_evidence.find((e) => e.secondary_resistance_resolved);
    expect(proof.secondary_high_date).toBe("2026-02-02");
    expect(proof.secondary_resistance_high).toBe(6.66);
    expect(proof.secondary_confirmation_close).toBe(6.94);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-06-09" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("2026-02-02 高点 5.9900");
    await expect(page.locator("#selection-info")).toContainText(
        "2026-06-09 收盘 6.9400 > 抵抗阶段高点 6.6600，抵抗解除",
    );
    await expect(page.locator("#selection-info")).toContainText("未启用／未执行 · 成交价费用后盈亏比");
    expect(errors).toEqual([]);
});
