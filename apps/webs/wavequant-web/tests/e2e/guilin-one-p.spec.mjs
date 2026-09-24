import { expect, test } from "@playwright/test";

test("Guilin ordinary A rebound buys July 6", async ({ page }) => {
    test.setTimeout(240000);
    const catalog = {
        available: true,
        latest: "2020-07-31",
        with_daily: 1,
        stocks: [{ symbol: "sz.000978", name: "桂林旅游", has_data: true, last: "2020-07-31", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (r) => r.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (r) => r.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((e) => {
        e.checked = true;
    });
    const pending = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    const view = await response.json();
    const buy = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2020-07-06"));
    expect(buy?.status).toBe("filled");
    const proof = buy.decision_evidence.find((e) => e.wave_a_class);
    expect(proof.wave_a_class).toBe("ordinary");
    expect(proof.wave_entry_n_date).toBe("2020-05-28");
    expect(proof.wave_b_low_date).toBe("2020-06-29");
    expect(proof.wave_equal_target).toBeCloseTo(5.012686, 5);
    expect(proof.wave_c_1618_target).toBeUndefined();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2020-07-06" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("普通 A 浪反弹买点");
    await expect(page.locator("#selection-info")).toContainText("5.0127");
    await expect(page.locator("#selection-info")).not.toContainText("大 C 浪扩展目标");
});
