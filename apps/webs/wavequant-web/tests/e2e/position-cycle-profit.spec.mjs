import { expect, test } from "@playwright/test";

test("Ruiling cumulative sale returns reconcile to one trade per complete holding", async ({
    page,
    context,
}, testInfo) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const catalog = {
        available: true,
        latest: "2026-09-18",
        with_daily: 1,
        stocks: [{ symbol: "sz.300154", name: "瑞凌股份", has_data: true, last: "2026-09-18", source: "tdx" }],
    };
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const response = page.waitForResponse((item) => item.url().includes("/api/akshare-backtest?"), {
        timeout: 180_000,
    });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    const fills = view.orders.filter((item) => item.status === "filled");
    const closed = fills.filter((item) => item.side === "SELL" && item.position_closed);
    expect(view.trades.length).toBe(closed.length);
    expect(view.metrics.trades).toBe(closed.length);
    for (const last of closed) {
        const holding = fills.filter((item) => item.trade_id === last.trade_id);
        const buy = holding.find((item) => item.side === "BUY");
        const cost = buy.price * buy.quantity + buy.fee;
        const proceeds = holding
            .filter((item) => item.side === "SELL")
            .reduce((sum, item) => sum + item.price * item.quantity - item.fee, 0);
        const trade = view.trades.find((item) => item.exit_time === last.timestamp);
        expect(trade.pnl).toBeCloseTo(proceeds - cost, 6);
        expect(trade.net_return).toBeCloseTo((proceeds - cost) / cost, 10);
        expect(last.position_net_return).toBeCloseTo(trade.net_return, 10);
    }
    await expect(page.locator("#trades-body tr")).toHaveCount(closed.length);
    const partial = fills.find((item) => item.side === "SELL" && item.position_closed === false);
    expect(partial).toBeTruthy();
    const row = page.locator("#trade-nodes-list .trade-node-row").filter({ hasText: partial.timestamp.slice(0, 10) });
    await expect(row).toContainText("累计已实现盈亏");
    await expect(row).toContainText(`${(partial.position_net_return * 100).toFixed(2)}%`);
    await expect(row).toContainText("以整笔买入含费成本为基数，尚未清仓");
    await row.locator(".trade-node-copy").click();
    await expect(row.locator(".trade-node-copy")).toHaveText("已复制");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("累计已实现收益率");
    const lastRow = page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: closed[0].timestamp.slice(0, 10) });
    await expect(lastRow).toContainText("整笔清仓盈亏");
    await expect(lastRow).toContainText(`${(closed[0].position_net_return * 100).toFixed(2)}%`);
    await page.setViewportSize({ width: 390, height: 844 });
    await row.locator(".trade-node-copy").focus();
    await page.keyboard.press("Enter");
    await expect(row.locator(".trade-node-copy")).toHaveText("已复制");
    expect(
        await page.evaluate(
            () => globalThis.document.documentElement.scrollWidth <= globalThis.document.documentElement.clientWidth,
        ),
    ).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("position-cycle-profit.png") });
    expect(errors).toEqual([]);
});
