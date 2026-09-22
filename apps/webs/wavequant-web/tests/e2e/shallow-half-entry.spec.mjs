import { expect, test } from "@playwright/test";

test("V3 defaults to inclusive third and selecting strict half changes actual backtest", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-06-09",
        with_daily: 1,
        stocks: [{ symbol: "sz.300163", name: "先锋新材", has_data: true, last: "2026-06-09", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator('#variant-select option[value="lecture_v3"]')).toHaveText("V3 · 二/三级交替后 N 轧空");
    await expect(page.getByRole("combobox", { name: "第二类浅回撤" })).toHaveValue("third");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    const response = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const defaultView = await result.json();
    expect(defaultView.variant).toBe("lecture_v3");
    expect(defaultView.backtest.strategy.mature_shallow_ratio).toBeCloseTo(1 / 3);
    expect(defaultView.backtest.strategy.mature_shallow_inclusive).toBe(true);
    expect(defaultView.orders.some((o) => o.side === "BUY" && o.timestamp.startsWith("2026-05-18"))).toBe(false);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    const halfResponse = page.waitForResponse(
        (r) => r.url().includes("/api/akshare-backtest?") && r.url().includes("variant=lecture_v3_c50"),
        { timeout: 180_000 },
    );
    await page.getByRole("combobox", { name: "第二类浅回撤" }).selectOption("half");
    const halfResult = await halfResponse;
    expect(halfResult.ok()).toBe(true);
    const view = await halfResult.json();
    expect(view.variant).toBe("lecture_v3_c50");
    expect(view.backtest.strategy.mature_shallow_ratio).toBe(0.5);
    expect(view.backtest.strategy.mature_shallow_inclusive).toBe(false);
    const buy = view.orders.find((o) => o.side === "BUY" && o.timestamp.startsWith("2026-05-18"));
    expect(buy.status).toBe("filled");
    expect(buy.net_reward_risk_filter).toBe(false);
    const proof = buy.decision_evidence.find((e) => e.buy_point_type);
    expect(proof.counter_operator).toBe("<");
    expect(proof.counter_limit).toBe(0.5);
    expect(proof.counter_ratio).toBeCloseTo(0.4858757062);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-05-18" })
        .locator(".trade-node-button")
        .first()
        .click();
    await expect(page.locator("#selection-info")).toContainText("N 收盘突破 2026-05-12");
    await expect(page.locator("#selection-info")).toContainText("5.1000 > 抵抗阶段高 4.8500");
    const thirdResponse = page.waitForResponse(
        (r) =>
            r.url().includes("/api/akshare-backtest?") && new URL(r.url()).searchParams.get("variant") === "lecture_v3",
        { timeout: 180_000 },
    );
    await page.getByRole("combobox", { name: "第二类浅回撤" }).selectOption("third");
    const thirdView = await (await thirdResponse).json();
    expect(thirdView.backtest.strategy.mature_shallow_inclusive).toBe(true);
    expect(thirdView.orders.some((o) => o.side === "BUY" && o.timestamp.startsWith("2026-05-18"))).toBe(false);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    expect(errors).toEqual([]);
});
