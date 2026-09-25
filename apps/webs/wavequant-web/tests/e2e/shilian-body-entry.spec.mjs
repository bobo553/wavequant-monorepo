import { expect, test } from "@playwright/test";

test("Shilian non-gap body breakout buys September 16 without repeat", async ({ page }) => {
    test.setTimeout(360000);
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.002285", name: "世联行", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (r) => r.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (r) => r.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    const pending = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 300000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    const view = await response.json();
    const buy = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-09-16"));
    expect(buy?.status).toBe("filled");
    expect(buy.execution_model).toBe("intraday_5m_next_open");
    const proof = buy.decision_evidence.find((e) => e.wave_entry_path);
    expect(proof.wave_gap_trigger).toBe("volume_body_breakout");
    expect(proof.wave_b_low_date).toBe("2026-09-16");
    expect(proof.wave_breakout_date).toBe("2026-09-14");
    expect(
        view.orders.some((o) => o.side === "BUY" && o.timestamp.startsWith("2026-09-17") && o.status === "filled"),
    ).toBe(false);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-09-16" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("放量中大阳线突破");
    await expect(page.locator("#selection-info")).toContainText("无需跳空");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-wave-endpoint-count", "3");
});
