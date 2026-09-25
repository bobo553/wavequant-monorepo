import { expect, test } from "@playwright/test";

test("stock backtest sends configurable capital and per-entry position limit", async ({ page }) => {
    test.setTimeout(180_000);
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });

    const capital = page.getByRole("spinbutton", { name: "回测初始资金，单位万元" });
    const buyRatio = page.getByRole("spinbutton", { name: "每次买入比例，百分比" });
    await expect(capital).toHaveValue("10");
    await expect(buyRatio).toHaveValue("100");

    await capital.fill("25");
    await buyRatio.fill("30");
    const backtestRequests = [];
    await page.route("**/api/akshare-backtest?*", (route) => {
        backtestRequests.push(new URL(route.request().url()).searchParams);
        return route.fulfill({ status: 400, json: { error: "测试拦截，不执行真实回测" } });
    });
    const requestPromise = page.waitForRequest(
        (request) => new URL(request.url()).pathname === "/api/akshare-backtest",
    );
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const request = await requestPromise;
    const params = new URL(request.url()).searchParams;
    expect(params.get("initial_capital")).toBe("250000");
    expect(params.get("max_position_weight")).toBe("0.3");
    await expect(page.locator("#error")).toContainText("测试拦截", { timeout: 10_000 });
    await page.getByRole("button", { name: "对比幅度方案" }).click();
    await expect.poll(() => backtestRequests.length).toBe(7);
    for (const comparisonParams of backtestRequests) {
        expect(comparisonParams.get("initial_capital")).toBe("250000");
        expect(comparisonParams.get("max_position_weight")).toBe("0.3");
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await capital.scrollIntoViewIfNeeded();
    await expect(capital).toBeVisible();
    await expect(buyRatio).toBeVisible();
    expect(
        await page.evaluate(
            () => globalThis.document.documentElement.scrollWidth <= globalThis.document.documentElement.clientWidth,
        ),
    ).toBe(true);
    expect(pageErrors).toEqual([]);
});
