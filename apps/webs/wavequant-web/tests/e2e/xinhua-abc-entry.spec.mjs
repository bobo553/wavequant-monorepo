import { env } from "node:process";

import { expect, test } from "@playwright/test";

test("Xinhua split N defense preserves ABC and September 17 entry", async ({ page }) => {
    test.setTimeout(240000);
    const apiOrigin = env["WAVEQUANT_E2E_API_ORIGIN"];
    if (apiOrigin) {
        await page.route("**/api/**", async (route) => {
            const target = new URL(route.request().url());
            const response = await route.fetch({
                url: `${apiOrigin}${target.pathname}${target.search}`,
                timeout: 180000,
            });
            await route.fulfill({ response });
        });
    }
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sh.601811", name: "新华文轩", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (r) => r.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (r) => r.fulfill({ json: catalog }));
    let orders;
    await page.route("**/api/akshare-backtest?**", async (route) => {
        const target = new URL(route.request().url());
        const response = await route.fetch({
            ...(apiOrigin ? { url: `${apiOrigin}${target.pathname}${target.search}` } : {}),
            timeout: 180000,
        });
        // Keep the real API evidence before Chromium evicts the large historical response.
        orders = (await response.json()).orders;
        await route.fulfill({ response });
    });
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((e) => {
        e.checked = true;
    });
    const pending = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    const buy = orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-09-17"));
    expect(buy?.status).toBe("filled");
    const proof = buy.decision_evidence.find((e) => e.wave_a_class);
    expect(proof.wave_a_class).toBe("ordinary");
    expect(proof.wave_a_high_date).toBe("2026-08-03");
    expect(proof.wave_defense).toBeCloseTo(14.86934875, 6);
    expect(proof.wave_entry_n_date).toBe("2026-07-03");
    expect(proof.wave_b_low_date).toBe("2026-08-31");
    expect(proof.wave_equal_target).toBeCloseTo(17.610927, 5);
    expect(proof.wave_c_1618_target).toBeUndefined();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-09-17" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("普通 A 浪反弹买点");
    await expect(page.locator("#selection-info")).toContainText("17.6109");
    await expect(page.locator("#selection-info")).not.toContainText("大 C 浪扩展目标");
});
