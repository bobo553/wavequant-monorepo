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
    expect(
        view.orders.some((o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2025-02-18")),
    ).toBe(false);
    expect(view.signals.some((s) => s.side === "LONG" && s.timestamp.startsWith("2025-02-17"))).toBe(false);
    const febN = view.audit.find(
        (e) => e.event === "n_completed" && e.direction === "up" && e.timestamp.startsWith("2025-02-10"),
    );
    expect(
        view.audit.some(
            (e) =>
                e.event === "n_squeeze_invalidated" &&
                e.attack === febN.bar_index &&
                e.timestamp.startsWith("2025-02-14"),
        ),
    ).toBe(true);
    const earlyNovember = view.orders.find(
        (o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2025-11-06"),
    );
    expect(earlyNovember.signal_timestamp).toContain("2025-11-05");
    expect(
        earlyNovember.decision_evidence.find((e) => e.squeeze_confirmation === "uninterrupted_squeeze").attack_date,
    ).toBe("2025-11-03");
    expect(
        view.orders.some((o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2025-11-14")),
    ).toBe(false);
    const novemberBuy = view.orders.find(
        (o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-11-07"),
    );
    expect(novemberBuy.signal_timestamp).toContain("2022-11-04");
    expect(
        view.orders.some((o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-11-16")),
    ).toBe(false);
    const proof = novemberBuy.decision_evidence.find((e) => e.squeeze_confirmation === "local_resistance_failure");
    expect(proof.attack_date).toBe("2022-11-01");
    expect(proof.prior_bar_date).toBe("2022-11-03");
    const decemberBuy = view.orders.find(
        (o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-12-09"),
    );
    expect(decemberBuy.signal_timestamp).toContain("2022-12-08");
    expect(
        view.orders.some((o) => o.status === "filled" && o.side === "BUY" && o.timestamp.startsWith("2022-12-14")),
    ).toBe(false);
    const larger = decemberBuy.decision_evidence.find((e) => e.squeeze_confirmation === "local_resistance_failure");
    expect(larger.attack_date).toBe("2022-12-02");
    expect(larger.prior_bar_date).toBe("2022-12-07");
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
    const pressure = sells.find((o) => o.timestamp.startsWith("2024-12-13"));
    expect(pressure.reason).toBe("pressure_adverse_clear");
    expect(pressure.signal_timestamp).toContain("2024-12-13");
    expect(pressure.remaining_quantity).toBe(0);
    expect(pressure.pressure_date).toBe("2024-10-08");
    expect(pressure.pressure_n_date).toBe("2024-12-12");
    expect(pressure.pressure_adverse_patterns).toEqual(["long_upper_shadow"]);
    expect(sells.some((o) => o.timestamp >= "2024-12-14" && o.timestamp < "2024-12-24")).toBe(false);
    const exhaustion = sells.find((o) => o.timestamp.startsWith("2026-09-11"));
    expect(exhaustion.reason).toBe("wave_volume_shadows_reduce");
    expect(exhaustion.exit_target_fraction).toBe(0.8);
    const originalRaw = (exhaustion.quantity + exhaustion.remaining_quantity) * exhaustion.adjustment_factor;
    const expectedRaw = Math.floor((originalRaw * 0.8 + 1e-7) / 100) * 100;
    expect(exhaustion.quantity * exhaustion.adjustment_factor).toBeCloseTo(expectedRaw);
    expect(exhaustion.wave_reached_stage).toBe("five_top");
    expect(exhaustion.wave_n_date).toBe("2026-08-31");
    const engulf = sells.find((o) => o.timestamp.startsWith("2026-09-15"));
    expect(engulf.reason).toBe("wave_bearish_engulf_clear");
    expect(engulf.signal_timestamp).toContain("2026-09-15");
    expect(engulf.remaining_quantity).toBe(0);
    expect(engulf.quantity).toBeCloseTo(exhaustion.remaining_quantity);
    expect(sells.some((o) => o.timestamp.startsWith("2026-09-18"))).toBe(false);
    const small = sells.find((o) => o.timestamp.startsWith("2021-12-21"));
    expect(small.reason).toBe("volume_down_small_n_reduce_30");
    expect(small.exit_target_fraction).toBe(0.3);
    expect(small.positive_n_date).toBe("2021-12-15");
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
    await expect(page.locator("#selection-info")).toContainText("2021-12-15");
    await expect(page.locator("#selection-info")).toContainText("小实体例外");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2022-11-07" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("2022-11-01");
    await expect(page.locator("#selection-info")).toContainText("2022-11-03");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2022-12-09" })
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
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2024-12-13" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("2024-10-08");
    await expect(page.locator("#selection-info")).toContainText("2024-12-12");
    await expect(page.locator("#selection-info")).toContainText("长上影");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2025-11-06" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("2025-11-03");
    await expect(page.locator("#selection-info")).toContainText("连续上攻确认强轧空");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-09-11" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("80.00%");
    await expect(page.locator("#selection-info")).toContainText("五顶");
    await expect(page.locator("#selection-info")).toContainText("2026-08-31");
    await page
        .locator("#trade-nodes-list .trade-node-row")
        .filter({ hasText: "2026-09-15" })
        .locator(".trade-node-button")
        .click();
    await expect(page.locator("#selection-info")).toContainText("反包");
    await expect(page.locator("#selection-info")).toContainText("当日清仓");
});
