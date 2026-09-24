import { expect, test } from "@playwright/test";

test("Guilin volume-down reduction fills at close and a next-session gap fade clears", async ({ page }) => {
    test.setTimeout(300_000);
    const catalog = {
        available: true,
        latest: "2026-09-18",
        with_daily: 1,
        stocks: [{ symbol: "sz.000978", name: "桂林旅游", has_data: true, last: "2026-09-18", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#symbol-select")).toHaveValue("sz.000978");
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const response = page.waitForResponse((item) => item.url().includes("/api/akshare-backtest?"), {
        timeout: 240_000,
    });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    const sells = view.orders.filter((order) => order.status === "filled" && order.side === "SELL");
    const reduction = sells.find((order) => order.timestamp.startsWith("2022-09-22"));
    expect(reduction?.reason).toBe("volume_down_reduce_70");
    expect(reduction?.exit_target_fraction).toBe(0.7);
    expect(reduction?.execution_model).toBe("same_day_close");
    expect(reduction?.volume_support_date).toBe("2022-09-19");
    const clear = sells.find((order) => order.timestamp.startsWith("2022-09-23"));
    expect(clear?.reason).toBe("volume_down_next_gap_fade_clear");
    expect(clear?.remaining_quantity).toBe(0);
    expect(clear?.gap_previous_close).toBe(reduction.trigger_close);

    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page.getByRole("button", { name: /定位 2022-09-22 卖出成交/ }).click();
    await expect(page.locator("#selection-info")).toContainText("当日收盘累计减仓原持仓 70%");
    await page.getByRole("button", { name: /定位 2022-09-23 卖出成交/ }).click();
    await expect(page.locator("#selection-info")).toContainText("当日收盘清空余仓");
});
