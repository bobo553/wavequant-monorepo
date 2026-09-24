import { expect, test } from "@playwright/test";

test("Guofang volume-down warning reduces 70% and next bearish low break clears", async ({ page }) => {
    test.setTimeout(240_000);
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
        (order) => order.status === "filled" && order.side === "BUY" && order.timestamp.startsWith("2020-05-11"),
    );
    expect(buy?.trigger_timestamp).toContain("2020-05-07");
    const reduction = view.orders.find(
        (order) => order.status === "filled" && order.side === "SELL" && order.timestamp.startsWith("2020-05-13"),
    );
    expect(reduction?.reason).toBe("volume_down_reduce_70");
    expect(reduction?.execution_model).toBe("same_day_close");
    expect(reduction?.signal_timestamp).toContain("2020-05-13");
    expect(reduction?.exit_target_fraction).toBe(0.7);
    expect(reduction?.trigger_volume).toBe(9_716_400);
    expect(reduction?.previous_volume).toBe(6_451_974);
    expect(reduction?.raw_shares).toBe(Math.floor((buy.raw_shares * 0.7 + 1e-7) / 100) * 100);
    expect(reduction?.remaining_quantity).toBeGreaterThan(0);
    const nextBar = view.bars.find((bar) => bar.time === "2020-05-14");
    expect(nextBar.open).toBeGreaterThan(reduction.trigger_close);
    const clear = view.orders.find(
        (order) => order.status === "filled" && order.side === "SELL" && order.timestamp.startsWith("2020-05-14"),
    );
    expect(clear?.reason).toBe("volume_down_next_followthrough_clear");
    expect(clear?.execution_model).toBe("same_day_close");
    expect(clear?.signal_timestamp).toContain("2020-05-14");
    expect(clear?.warning_low).toBeGreaterThan(clear?.observed_close);
    expect(clear?.remaining_quantity).toBe(0);
    expect(
        view.orders.some((order) => order.timestamp.startsWith("2020-05-20") && order.trade_id === buy.trade_id),
    ).toBe(false);

    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page.getByRole("button", { name: /定位 2020-05-13 卖出成交/ }).click();
    await expect(page.locator("#selection-info")).toContainText("当日收盘累计减仓原持仓 70%");
    await expect(page.locator("#selection-info")).toContainText("9,716,400");
    await page.getByRole("button", { name: /定位 2020-05-14 卖出成交/ }).click();
    await expect(page.locator("#selection-info")).toContainText("收盘跌破警示日低点");
});
