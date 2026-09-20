import { expect, test } from "@playwright/test";

test("current-stock backtest recovers when the old engine rejects its first request", async ({ page }) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    let attempts = 0;
    await page.route("**/api/tdx-backtest?*", async (route) => {
        attempts++;
        if (attempts === 1) {
            await route.fulfill({
                status: 409,
                contentType: "application/json",
                body: JSON.stringify({ error: "运行中策略代码已变更，请重启服务后重试" }),
            });
        } else await route.continue();
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#run-stock-backtest").click();
    await expect.poll(() => attempts, { timeout: 180_000 }).toBeGreaterThanOrEqual(2);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#result-scope")).toHaveValue("tdx-backtest");
    await expect(page.locator("#trade-nodes-tab")).toBeVisible();
    expect(pageErrors).toEqual([]);
});
