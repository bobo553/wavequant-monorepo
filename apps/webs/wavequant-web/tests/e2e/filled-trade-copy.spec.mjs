import { expect, test } from "@playwright/test";

test("filled trade copies evidence without selecting another chart node", async ({ page, context }) => {
    test.setTimeout(180_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/research?page=workspace&symbol=sz.300154&asof=2026-09-07&source=tdx");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await expect(page.locator("#result-scope")).toHaveValue("tdx");
    await page.locator("#variant-select").selectOption("lecture_v3");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    await page.locator("#backtest-volume-filter").evaluate((field) => {
        field.checked = false;
        field.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    const response = page.waitForResponse((item) => item.url().includes("/api/tdx-backtest?"), { timeout: 120_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const backtest = await response;
    expect(backtest.ok()).toBe(true);
    const view = await backtest.json();
    const fill = view.markers.find(
        (item) => item.kind === "fill" && item.side === "SELL" && item.time === "2025-05-15",
    );
    expect(fill).toBeDefined();
    expect(fill.signal_time).toBe("2025-05-15");
    expect(fill.exit_target_fraction).toBe(0.65);
    expect(fill.decision_timestamp).toBe("2025-05-15T14:30:00+08:00");
    expect(fill.timestamp).toBe("2025-05-15T14:30:00+08:00");
    expect(fill.execution_model).toBe("intraday_5m_next_open");
    expect(fill.minute_next_open_raw).toBe(9.97);
    expect(fill.raw_price).toBeCloseTo(9.97 * (1 - 0.0005), 5);
    const proportion = ((fill.quantity / (fill.quantity + fill.remaining_quantity)) * 100).toFixed(2) + "%";
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    const row = page.locator("#trade-nodes-list .trade-node-row").filter({ hasText: "2025-05-15" });
    await expect(row).toBeVisible();
    await expect(row).toContainText("减仓");
    await expect(row).toContainText("成交 2025-05-15T14:30:00+08:00");
    await expect(row).toContainText("剩余");
    await expect(row).toContainText("平仓比例：");
    await expect(row).toContainText(`平仓比例：${proportion}`);
    await expect(row).toContainText("占卖出前该股票持仓");
    const locator = row.locator(".trade-node-button");
    const copy = row.locator(".trade-node-copy");
    const current = await locator.getAttribute("aria-current");
    const date = await row.locator("time").textContent();
    await copy.click();
    await expect(copy).toHaveText("已复制");
    await expect(locator).toHaveAttribute("aria-current", current);
    const text = await page.evaluate(() => navigator.clipboard.readText());
    for (const value of [
        "300154",
        `成交日期：${date}`,
        "成交时间：2025-05-15T14:30:00+08:00",
        "决定时间：2025-05-15T14:30:00+08:00",
        "原因：",
        "成交数量：",
        `平仓比例：${proportion}`,
        "占卖出前该股票持仓",
        "成交日 K 线：",
        "开盘：",
        "最高：",
        "最低：",
        "收盘：",
        "成交量：",
        "前回踩 K 线：2025-05-09",
        "反弹 2/3 位：12.9645",
        "按最高价判断",
        "目标累计减仓：65.00%",
        "下一根五分钟开盘原价：9.9700 元",
        "成交口径：已完成五分钟线判定，下一根五分钟线开盘价加回测滑点模拟成交",
    ]) {
        expect(text).toContain(value);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await copy.scrollIntoViewIfNeeded();
    await copy.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#trade-nodes-filled-copy-feedback")).toContainText(`已复制 ${date}`);
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
    expect(
        await page.evaluate(
            () => globalThis.document.documentElement.scrollWidth <= globalThis.document.documentElement.clientWidth,
        ),
    ).toBe(true);
    expect(errors).toEqual([]);
});
