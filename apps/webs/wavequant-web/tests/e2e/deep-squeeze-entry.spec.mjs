import { expect, test } from "@playwright/test";

test("deep whole-wave pullback buys the resistance-record squeeze on August 7", async ({ page }) => {
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
    const buy = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-08-07"));
    expect(buy.status).toBe("filled");
    expect(buy.execution_model).toBe("same_day_close");
    expect(buy.entry_conditions[0].passed).toBe(true);
    expect(view.orders.some((o) => o.side === "BUY" && o.timestamp.startsWith("2026-08-06"))).toBe(false);
    const proof = buy.decision_evidence.find((e) => e.inverse_reentry_path);
    expect(proof.attack_date).toBe("2026-08-03");
    expect(proof.recovery_kill_high).toBeCloseTo(24.8663, 4);
    expect(proof.recovery_resistance_high).toBeCloseTo(25.8282, 4);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-08-07" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("正 N 2026-08-03");
    await expect(page.locator("#selection-info")).toContainText("本次 N 抵抗阶段高点 25.8282");
    await expect(page.locator("#selection-info")).toContainText("杀多高 24.8663");
    await expect(page.locator("#selection-info")).toContainText("67.90%");
    expect(errors).toEqual([]);
});
