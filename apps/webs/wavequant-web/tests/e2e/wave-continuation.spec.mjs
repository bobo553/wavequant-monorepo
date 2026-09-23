import { expect, test } from "@playwright/test";

test("Huaci buys the C-wave gap with dated A/B evidence", async ({ page, context }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.001216", name: "华瓷股份", has_data: true, last: "2026-09-21", source: "akshare" }],
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
    const response = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    const buy = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-09-15"));
    expect(buy?.status).toBe("filled");
    expect(buy.reason).toBe("system_wave_push_gap");
    expect(buy.execution_model).toBe("same_day_close");
    const proof = buy.decision_evidence.find((e) => e.wave_entry_path);
    expect(proof.wave_b_low_date).toBe("2026-08-21");
    expect(proof.wave_a_high_date).toBe("2026-08-11");
    expect(proof.wave_equal_target).toBeCloseTo(19.6974877, 5);
    expect(proof.wave_c_1618_target).toBeCloseTo(22.0548955, 5);
    expect(proof.wave_c_2618_target).toBeCloseTo(25.8694713, 5);
    const event = view.theory.events.find((e) => e.event === "long_signal" && e.time === "2026-09-15");
    expect(event.levels).toEqual(
        expect.arrayContaining([
            expect.objectContaining({ name: "C 浪扩展 1.618×A（非保证）", price: proof.wave_c_1618_target }),
            expect.objectContaining({ name: "C 浪扩展 2.618×A（非保证）", price: proof.wave_c_2618_target }),
        ]),
    );
    expect(proof.rvol).toBeGreaterThan(1.2);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-09-15" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("堆箱买点");
    await expect(page.locator("#selection-info")).toContainText("B 低 2026-08-21");
    await expect(page.locator("#selection-info")).toContainText("19.6975");
    await page.getByRole("button", { name: "复制 2026-09-15 买入成交信息", exact: true }).click();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain("C 浪确认：放量跳空阳线");
    expect(copied).toContain("19.6975");
    expect(copied).toContain("22.0549");
    expect(copied).toContain("25.8695");
    expect(copied).toContain("2.618 倍不是上涨上限");
    expect(copied).toContain("2026-08-11 达到二吐");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator("#selection-info")).toContainText("19.6975");
    expect(errors).toEqual([]);
});
