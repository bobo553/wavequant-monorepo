import { expect, test } from "@playwright/test";

test("Shilian C entry uses yesterday volume and explicit missing-minute fallback", async ({ page, context }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2020-08-07",
        with_daily: 1,
        stocks: [{ symbol: "sz.002285", name: "世联行", has_data: true, last: "2020-08-07", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = true;
    });
    await expect(page.locator("label.backtest-volume-filter").first()).toContainText("成交量 ＞ 昨日");
    const pending = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    const view = await response.json();
    expect(view.backtest.strategy.volume_filter).toBe(true);
    const reduction = view.orders.find((o) => o.side === "SELL" && o.timestamp.startsWith("2020-07-03"));
    expect(reduction?.status).toBe("filled");
    expect(reduction.reason).toBe("wave_gap_reversal_reduce");
    expect(reduction.exit_target_fraction).toBe(0.8);
    expect(reduction.raw_shares).toBe(700);
    expect(reduction.wave_body_fraction).toBeLessThan(0.05);
    expect(reduction.wave_body_range_fraction).toBeGreaterThan(0.5);
    const buy = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2020-07-20"));
    expect(buy?.status).toBe("filled");
    expect(buy.reason).toBe("system_wave_push_gap");
    expect(buy.minute_fallback.purpose).toBe("wave_continuation_entry");
    const proof = buy.decision_evidence.find((e) => e.wave_entry_path);
    expect(proof.wave_b_low_date).toBe("2020-07-17");
    expect(proof.wave_gap_trigger).toBe("volume");
    expect(proof.wave_breakout_date).toBe("2020-07-14");
    expect(proof.rvol).toBeCloseTo(36_084_567 / 20_451_437, 6);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2020-07-03" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("高开回落");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2020-07-20" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("B 低 2020-07-17");
    await expect(page.locator("#selection-info")).toContainText("确认时累计成交量");
    await page.getByRole("button", { name: "复制 2020-07-20 买入成交信息", exact: true }).click();
    const text = await page.evaluate(() => navigator.clipboard.readText());
    expect(text).toContain("当日缺少完整同源分钟线");
    expect(text).toContain("3.9832");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator("#selection-info")).toContainText("B 低 2020-07-17");
    expect(errors).toEqual([]);
});
