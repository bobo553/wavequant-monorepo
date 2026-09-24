import { expect, test } from "@playwright/test";

test("Guofang ordinary A C target warning reduces then first lower close clears", async ({ page }) => {
    test.setTimeout(240_000);
    const catalog = {
        available: true,
        latest: "2020-06-05",
        with_daily: 1,
        stocks: [{ symbol: "sh.601086", name: "国芳集团", has_data: true, last: "2020-06-05", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace&symbol=sh.601086&asof=2020-06-05&source=akshare");
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
        (order) => order.status === "filled" && order.side === "BUY" && order.timestamp.startsWith("2020-05-21"),
    );
    expect(buy?.reason).toBe("system_wave_push_gap");
    const reduction = view.orders.find(
        (order) => order.status === "filled" && order.side === "SELL" && order.timestamp.startsWith("2020-06-02"),
    );
    expect(reduction?.reason).toBe("wave_ordinary_equal_upper_shadow_reduce");
    expect(reduction?.exit_target_fraction).toBe(0.8);
    expect(reduction?.wave_reached_stage).toBe("ordinary_equal");
    expect(reduction?.wave_a_high).toBeGreaterThan(reduction?.wave_one_p);
    expect(reduction?.wave_a_high).toBeLessThan(reduction?.wave_two_t);
    expect(reduction?.wave_upper_shadow_fraction).toBeGreaterThanOrEqual(0.5);
    expect(reduction?.observed_volume).toBe(17_938_320);
    expect(reduction?.remaining_quantity).toBeGreaterThan(0);
    const clear = view.orders.find(
        (order) => order.status === "filled" && order.side === "SELL" && order.timestamp.startsWith("2020-06-05"),
    );
    expect(clear?.reason).toBe("wave_ordinary_equal_lower_close_clear");
    expect(clear?.abnormal_date).toBe("2020-06-02");
    expect(clear?.observed_close).toBeLessThan(clear?.previous_close);
    expect(clear?.remaining_quantity).toBe(0);
    expect(
        view.orders.some((order) =>
            ["2020-06-03", "2020-06-04"].some((date) => order.timestamp.startsWith(date) && order.side === "SELL"),
        ),
    ).toBe(false);

    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page.getByRole("button", { name: /定位 2020-06-02 卖出成交/ }).click();
    await expect(page.locator("#selection-info")).toContainText("普通 A 的 C 等浪");
    await expect(page.locator("#selection-info")).toContainText("累计 80% 目标减仓");
    await page.getByRole("button", { name: /定位 2020-06-05 卖出成交/ }).click();
    await expect(page.locator("#selection-info")).toContainText("异常后首次收低");
});
