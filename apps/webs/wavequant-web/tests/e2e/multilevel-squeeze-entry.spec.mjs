import { expect, test } from "@playwright/test";

test("Xingwang February 13 displays both squeeze scales and copies their dates", async ({ page, context }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.002396", name: "星网锐捷", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (r) => r.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (r) => r.fulfill({ json: catalog }));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const response = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    expect(view.orders.some((o) => o.side === "BUY" && o.timestamp.startsWith("2026-05-28"))).toBe(false);
    expect(view.signals.some((s) => s.side === "LONG" && s.time === "2026-05-28")).toBe(false);
    const order = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2019-02-13"));
    expect(order.status).toBe("filled");
    expect(order.execution_model).toBe("same_day_close");
    const proof = order.decision_evidence.find((e) => e.buy_point_type);
    expect(proof.buy_point_type).toBe("multilevel_breakout_squeeze");
    expect(proof.key_source_index_date).toBe("2018-12-03");
    expect(order.entry_conditions[0].passed).toBe(true);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2019-02-13" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("双重轧空");
    await expect(page.locator("#selection-info")).toContainText("2018-12-03");
    await expect(page.locator("#selection-info")).not.toContainText("NaN");
    await page.getByRole("button", { name: "复制 2019-02-13 买入成交信息", exact: true }).click();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain("双重轧空");
    expect(copied).toContain("正 N 2019-02-11");
    expect(copied).toContain("2018-12-03");
    expect(errors).toEqual([]);
});
