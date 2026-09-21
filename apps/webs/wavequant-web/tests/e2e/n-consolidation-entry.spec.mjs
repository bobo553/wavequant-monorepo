import { expect, test } from "@playwright/test";

test("Guofang defended N fills nonflat limit close with explicit simulation evidence", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-18",
        with_daily: 1,
        stocks: [{ symbol: "sh.601086", name: "国芳集团", has_data: true, last: "2026-09-18", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
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
    expect(view.backtest.status).toBe("complete");
    const signal = view.signals.find((s) => s.side === "LONG" && s.time === "2026-08-28");
    expect(signal.trigger_timestamp).toContain("2026-07-29");
    const order = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-08-28"));
    expect(order.status).toBe("filled");
    expect(order.execution_model).toBe("same_day_close");
    expect(order.minute_fallback.purpose).toBe("consolidation_entry");
    expect(order.fill_assumption).toBe("nonflat_limit_close_without_queue_verification");
    expect(order.raw_price).toBeCloseTo(8.34, 6);
    expect(order.decision_evidence.find((e) => e.buy_point_type).alternation_low_index_date).toBe("2026-06-29");
    expect(view.backtest.minute_fallbacks.find((e) => e.date === "2026-08-28").coverage.missing_volume).toBe(111600);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-08-28" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("非一字涨停按当日收盘价模拟成交");
    await expect(page.locator("#selection-info")).toContainText("跳空放量重新站上原 N 高点");
    await expect(page.locator("#selection-info")).toContainText("2026-07-29");
    await expect(page.locator("#selection-info")).toContainText("2026-06-29");
    expect(errors).toEqual([]);
});
