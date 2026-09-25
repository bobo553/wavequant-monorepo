import { expect, test } from "@playwright/test";

test("V3 shallow alternation breakout defaults on and can be disabled for Guofang", async ({ page }) => {
    test.setTimeout(360_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2025-04-07",
        with_daily: 1,
        stocks: [{ symbol: "sh.601086", name: "国芳集团", has_data: true, last: "2025-04-07", source: "tdx" }],
    };
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace&symbol=sh.601086&asof=2025-04-07&source=tdx");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    const checkbox = page.getByRole("checkbox", { name: "启用浅回撤横盘突破买点" });
    await expect(checkbox).toBeVisible();
    await expect(checkbox).toBeChecked();
    await page.locator("#backtest-start").fill("2018-01-02");

    const backtestResponse = (enabled) =>
        page.waitForResponse(
            (response) => {
                const url = new URL(response.url());
                return (
                    url.pathname === "/api/tdx-backtest" &&
                    url.searchParams.get("shallow_base_breakout_enabled") === String(enabled)
                );
            },
            { timeout: 300_000 },
        );

    let pending = backtestResponse(true);
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const onResponse = await pending;
    expect(onResponse.ok(), await onResponse.text()).toBe(true);
    const on = await onResponse.json();
    expect(on.backtest.strategy.shallow_base_breakout_enabled).toBe(true);
    expect(
        on.audit.some((event) => event.event === "shallow_alternation_candidate" && event.pullback_index != null),
    ).toBe(true);
    expect(
        on.signals.some((signal) => signal.time === "2025-04-03" && signal.reason === "system_shallow_base_breakout"),
    ).toBe(true);
    expect(
        on.signals.some((signal) => signal.time === "2025-04-07" && signal.reason === "system_shallow_base_breakout"),
    ).toBe(false);
    const fill = on.orders.find(
        (order) => order.timestamp.startsWith("2025-04-03") && order.side === "BUY" && order.status === "filled",
    );
    expect(fill?.reason).toBe("system_shallow_base_breakout");
    expect(fill?.entry_conditions.some((condition) => condition.name === "浅回撤交替待选" && condition.passed)).toBe(
        true,
    );
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#backtest-details")).toContainText("浅回撤横盘突破开启");

    pending = backtestResponse(false);
    await checkbox.uncheck();
    const offResponse = await pending;
    expect(offResponse.ok(), await offResponse.text()).toBe(true);
    const off = await offResponse.json();
    expect(off.backtest.strategy.shallow_base_breakout_enabled).toBe(false);
    expect(
        off.signals.some((signal) => signal.time === "2025-04-03" && signal.reason === "system_shallow_base_breakout"),
    ).toBe(false);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#backtest-details")).toContainText("浅回撤横盘突破关闭");
    expect(errors).toEqual([]);
});
