import { expect, test } from "@playwright/test";

test("Guilin selected May 21 buy marks A origin, A high, B low and C confirmation candles", async ({ page }) => {
    test.setTimeout(240_000);
    const catalog = {
        available: true,
        latest: "2021-05-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.000978", name: "桂林旅游", has_data: true, last: "2021-05-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    const pending = page.waitForResponse((response) => response.url().includes("/api/akshare-backtest?"), {
        timeout: 180_000,
    });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    const view = await response.json();
    const buy = view.orders.find((order) => order.side === "BUY" && order.timestamp.startsWith("2021-05-21"));
    expect(buy?.status).toBe("filled");
    const proof = buy.decision_evidence.find((evidence) => evidence.wave_entry_path);
    expect(proof.wave_a_origin_date).toBeLessThan(proof.wave_a_high_date);
    expect(proof.wave_a_high_date).toBe("2021-04-20");
    expect(proof.wave_b_low_date).toBe("2021-05-07");
    expect(proof.wave_breakout_close).toBeCloseTo(6.7021, 4);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2021-05-21" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-wave-endpoint-count", "4");
    await expect(page.locator("#selection-info")).toContainText(`${proof.wave_a_origin_date} 起点`);
    await expect(page.locator("#selection-info")).toContainText("8.5655");
    await expect(page.locator("#selection-info")).toContainText("5.9814");
    await expect(page.locator("#selection-info")).toContainText("6.7021");
});
