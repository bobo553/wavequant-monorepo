import { expect, test } from "@playwright/test";

test("Guofang massive gap reversal clears the August holding instead of partial reduction", async ({ page }) => {
    test.setTimeout(240_000);
    const catalog = {
        available: true,
        latest: "2020-08-14",
        with_daily: 1,
        stocks: [{ symbol: "sh.601086", name: "国芳集团", has_data: true, last: "2020-08-14", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace&symbol=sh.601086&asof=2020-08-14&source=akshare");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#symbol-select")).toHaveValue("sh.601086");
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const response = page.waitForResponse((item) => item.url().includes("/api/akshare-backtest?"), {
        timeout: 180_000,
    });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    const buy = view.orders.find(
        (order) => order.status === "filled" && order.side === "BUY" && order.timestamp.startsWith("2020-07-30"),
    );
    const clear = view.orders.find(
        (order) => order.status === "filled" && order.side === "SELL" && order.timestamp.startsWith("2020-08-14"),
    );
    expect(buy).toBeTruthy();
    expect(clear?.reason).toBe("volume_massive_gap_reversal_clear");
    expect(clear?.quantity).toBe(buy.quantity);
    expect(clear?.remaining_quantity).toBe(0);
    expect(clear?.execution_model).toBe("same_day_close");
    expect(clear?.massive_volume_multiple).toBeGreaterThan(3);
    expect(clear?.observed_volume).toBe(53_163_008);
    expect(clear?.observed_open).toBeGreaterThan(clear?.previous_high);
    expect(clear?.observed_close).toBeLessThan(clear?.previous_low);
    expect(
        view.orders.some(
            (order) => order.reason === "wave_gap_reversal_reduce" && order.timestamp.startsWith("2020-08-14"),
        ),
    ).toBe(false);
    expect(view.backtest.open_positions.some((position) => position.symbol === "sh.601086")).toBe(false);

    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page.getByRole("button", { name: /定位 2020-08-14 卖出成交/ }).click();
    await expect(page.locator("#selection-info")).toContainText("巨量高开反包");
    await expect(page.locator("#selection-info")).toContainText("53,163,008");
    await expect(page.locator("#selection-info")).toContainText("当日清空余仓");
});
