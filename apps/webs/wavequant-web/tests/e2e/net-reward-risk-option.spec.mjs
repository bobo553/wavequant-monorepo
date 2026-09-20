import { expect, test } from "@playwright/test";

test("net reward risk is unchecked by default and toggles the actual execution gate", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    const checkbox = page.getByRole("checkbox", { name: "启用次开盘含费净盈亏比过滤" });
    await expect(checkbox).not.toBeChecked();
    await page.evaluate(() => {
        globalThis.document.getElementById("variant-select").value = "lecture_v3_d50_c50";
        globalThis.document.getElementById("symbol-select").value = "sz.300154";
        globalThis.document.getElementById("backtest-start").value = "2018-01-02";
    });
    const responseFor = (enabled) =>
        page.waitForResponse(
            (response) => {
                const url = new URL(response.url());
                return (
                    url.pathname === "/api/tdx-backtest" &&
                    url.searchParams.get("net_reward_risk_filter") === String(enabled)
                );
            },
            { timeout: 180_000 },
        );
    let pending = responseFor(false);
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const offResponse = await pending;
    expect(offResponse.ok(), await offResponse.text()).toBe(true);
    const off = await offResponse.json();
    expect(off.symbol).toBe("sz.300154");
    expect(off.backtest.execution.net_reward_risk_filter).toBe(false);
    expect(off.orders.filter((order) => order.reason === "insufficient_net_reward_risk")).toEqual([]);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#backtest-details")).toContainText("次开盘含费净盈亏比过滤关闭");

    pending = responseFor(true);
    await checkbox.focus();
    await page.keyboard.press("Space");
    const onResponse = await pending;
    expect(onResponse.ok(), await onResponse.text()).toBe(true);
    const on = await onResponse.json();
    expect(on.backtest.execution.net_reward_risk_filter).toBe(true);
    expect(on.signals).toEqual(off.signals);
    expect(on.run_id).not.toBe(off.run_id);
    expect(on.orders.some((order) => order.reason === "insufficient_net_reward_risk")).toBe(true);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#backtest-details")).toContainText("次开盘含费净盈亏比过滤开启");

    pending = responseFor(false);
    await checkbox.uncheck();
    const again = await (await pending).json();
    expect(again.run_id).toBe(off.run_id);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    const comparisonFlags = [];
    await page.route("**/api/tdx-backtest?*", async (route) => {
        const url = new URL(route.request().url());
        comparisonFlags.push(url.searchParams.get("net_reward_risk_filter"));
        await route.fulfill({ json: off });
    });
    await page.locator("#compare-ratios").click();
    await expect.poll(() => comparisonFlags.length).toBe(5);
    expect(comparisonFlags).toEqual(Array(5).fill("false"));
    await page.setViewportSize({ width: 390, height: 844 });
    await checkbox.scrollIntoViewIfNeeded();
    await expect(checkbox).toBeVisible();
    expect(
        await page.evaluate(
            () => globalThis.document.documentElement.scrollWidth <= globalThis.document.documentElement.clientWidth,
        ),
    ).toBe(true);
    await page.screenshot({ path: "test-results/net-reward-risk-option.png" });
    expect(errors).toEqual([]);
});
