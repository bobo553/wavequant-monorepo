import { expect, test } from "@playwright/test";

test("Huaci reduces on the same candle that reaches its entry N two-t target", async ({ page, context }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.001216", name: "华瓷股份", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
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
    const reduction = view.orders.find((o) => o.side === "SELL" && o.timestamp.startsWith("2026-08-11"));
    expect(reduction.status).toBe("filled");
    expect(reduction.reason).toBe("wave_upper_rejection_reduce");
    expect(reduction.wave_n_date).toBe("2026-08-03");
    expect(reduction.wave_reached_date).toBe("2026-08-11");
    expect(reduction.exit_target_fraction).toBe(0.8);
    expect(reduction.wave_reached_stage).toBe("two_t");
    expect(reduction.execution_model).toBe("same_day_close");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-08-11" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("放量冲高收阴");
    await expect(page.locator("#selection-info")).toContainText("不要求日涨跌幅为负");
    await expect(page.locator("#selection-info")).toContainText("80.00%");
    await page.getByRole("button", { name: "复制 2026-08-11 卖出成交信息", exact: true }).click();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain("本笔正 N 2026-08-03");
    expect(copied).toContain("17.4890");
    expect(copied).toContain("冲高收阴");
    const clear = view.orders.find(
        (o) => o.side === "SELL" && o.timestamp.startsWith("2026-08-12") && o.status === "filled",
    );
    expect(clear.reason).toBe("wave_abnormal_followthrough_clear");
    expect(clear.remaining_quantity).toBe(0);
    expect(clear.raw_shares).toBeCloseTo(200);
    expect(view.orders.some((o) => o.side === "SELL" && o.timestamp.startsWith("2026-08-19"))).toBe(false);
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-08-12" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("目标异常K线次日收盘继续走低");
    await expect(page.locator("#selection-info")).toContainText("16.9983");
    await expect(page.locator("#selection-info")).toContainText("16.6191");
    await page.getByRole("button", { name: "复制 2026-08-12 卖出成交信息", exact: true }).click();
    const clearCopy = await page.evaluate(() => navigator.clipboard.readText());
    expect(clearCopy).toContain("清空余仓，无需再次放量或等待倒 N");
    expect(clearCopy).toContain("2026-08-11 异常K线收盘 16.9983");
    expect(errors).toEqual([]);
});
