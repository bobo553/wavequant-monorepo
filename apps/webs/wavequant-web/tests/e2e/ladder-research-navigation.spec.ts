import { expect, test } from "@playwright/test";

test("ladder card and table links open that stock's real historical market data", async ({ page }) => {
    test.setTimeout(150000);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/market");
    await page.getByRole("tab", { name: "涨停阶梯", exact: true }).click();
    await page.getByLabel("涨停天梯日期").fill("2026-09-18");
    const card = page.getByLabel("连板天梯").getByRole("link").first();
    await expect(card).toBeVisible({ timeout: 35000 });
    const href = await card.getAttribute("href");
    expect(href).toBeTruthy();
    const target = new URL(href!, "http://localhost");
    const symbol = target.searchParams.get("symbol")!;
    expect(target.searchParams.get("asof")).toBe("2026-09-18");
    expect(await page.getByRole("table").getByRole("link").first().getAttribute("href")).toBe(href);
    const response = page.waitForResponse(
        (item) => {
            const url = new URL(item.url());
            return (
                url.pathname === "/api/market-timeframe" &&
                url.searchParams.get("symbol") === symbol &&
                url.searchParams.get("asof") === "2026-09-18"
            );
        },
        { timeout: 120000 },
    );
    await card.click();
    await expect(page).toHaveURL(/\/research\?/);
    const result = await response;
    expect(result.ok(), await result.text()).toBe(true);
    const data = await result.json();
    expect(data.view.symbol).toBe(symbol);
    expect(data.view.asof <= "2026-09-18").toBe(true);
    expect(data.view.bars.length).toBeGreaterThan(0);
    await expect(page.locator("#symbol-select")).toHaveValue(symbol);
    await expect(page.locator("#selected-stock-summary")).toContainText(data.view.asof, { timeout: 30000 });
    await expect(page.locator("#loading")).toBeHidden();
    await expect(page.locator("#error")).toBeHidden();
    if (data.view.asof !== "2026-09-18")
        await expect(page.locator("#stock-picker-feedback")).toContainText(
            `请求日期 2026-09-18，实际可用行情截至 ${data.view.asof}`,
        );
    await page.reload();
    await expect(page.locator("#symbol-select")).toHaveValue(symbol, { timeout: 30000 });
    await expect(page.locator("#asof-label")).toHaveText(data.view.asof);
    expect(errors).toEqual([]);
});
