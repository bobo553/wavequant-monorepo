import { expect, test } from "@playwright/test";

test("Guilin volume-down reduction freezes support and clears on its break", async ({ page }) => {
    test.setTimeout(180_000);
    const catalog = {
        available: true,
        latest: "2026-09-18",
        with_daily: 1,
        stocks: [{ symbol: "sz.000978", name: "桂林旅游", has_data: true, last: "2026-09-18", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#symbol-select")).toHaveValue("sz.000978");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
    });
    const response = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 120_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    expect(view.symbol).toBe("sz.000978");
    const novemberBuy = view.orders.find(
        (o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-11-04"),
    );
    expect(novemberBuy.signal_timestamp).toContain("2022-11-03");
    expect(
        view.orders.some((o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-11-16")),
    ).toBe(false);
    const proof = novemberBuy.decision_evidence.find((e) => e.squeeze_confirmation === "local_resistance_failure");
    expect(proof.attack_date).toBe("2022-11-01");
    expect(proof.prior_bar_date).toBe("2022-11-02");
    const decemberBuy = view.orders.find(
        (o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-12-07"),
    );
    expect(decemberBuy.signal_timestamp).toContain("2022-12-06");
    expect(
        view.orders.some((o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-12-14")),
    ).toBe(false);
    const larger = decemberBuy.decision_evidence.find((e) => e.squeeze_confirmation === "local_resistance_failure");
    expect(larger.attack_date).toBe("2022-12-02");
    expect(larger.prior_bar_date).toBe("2022-12-05");
    expect(larger.n_level).toBe(2);
    expect(larger.n_neckline_date).toBe("2022-06-30");
    const sells = view.orders.filter((o) => o.status === "filled" && o.side === "SELL");
    const julySells = sells.filter((o) => o.timestamp.startsWith("2023-07-13"));
    expect(julySells).toHaveLength(1);
    expect(julySells[0].reason).toBe("volume_inverse_n_clear");
    expect(julySells[0].signal_timestamp).toContain("2023-07-13");
    expect(julySells[0].remaining_quantity).toBe(0);
    expect(julySells[0].observed_volume).toBeGreaterThan(julySells[0].previous_volume);
    expect(sells.some((o) => o.timestamp.startsWith("2023-07-18"))).toBe(false);
    const small = sells.find((o) => o.timestamp.startsWith("2021-12-21"));
    expect(small.reason).toBe("volume_down_small_n_reduce_30");
    expect(small.exit_target_fraction).toBe(0.3);
    expect(small.positive_n_date).toBe("2021-12-10");
    expect(small.small_body_fraction).toBeLessThanOrEqual(0.01);
    const reduction = sells.find((o) => o.timestamp.startsWith("2022-09-23"));
    expect(reduction.reason).toBe("volume_down_reduce_70");
    expect(reduction.exit_target_fraction).toBe(0.7);
    expect(reduction.volume_support_date).toBe("2022-09-19");
    const clear = sells.find((o) => o.timestamp.startsWith("2022-09-26"));
    expect(clear.reason).toBe("volume_down_support_break_clear");
    expect(clear.remaining_quantity).toBe(0);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2022-09-23" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("70%");
    await expect(page.locator("#selection-info")).toContainText("2022-09-19");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2022-09-26" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("当日清仓");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2021-12-21" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("30%");
    await expect(page.locator("#selection-info")).toContainText("2021-12-10");
    await expect(page.locator("#selection-info")).toContainText("小实体例外");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2022-11-04" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("2022-11-01");
    await expect(page.locator("#selection-info")).toContainText("2022-11-02");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2022-12-07" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("2022-12-02");
    await expect(page.locator("#selection-info")).toContainText("2022-06-30");
    await expect(page.locator("#selection-info")).toContainText("正 N 级别 2");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2023-07-13" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("倒 N 确认且成交量超过前日，当日直接清仓");
});
