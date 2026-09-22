import { expect, test } from "@playwright/test";

test("real AkShare ladder follows the selected date and exports that day's stocks", async ({ page }) => {
    test.setTimeout(120000);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/market");
    await page.getByRole("tab", { name: "涨停阶梯", exact: true }).click();
    await expect(page.getByText("合成样本", { exact: true })).toHaveCount(0);
    const field = page.getByLabel("涨停天梯日期");
    for (const date of ["2026-09-21", "2026-09-18"]) {
        const pending = page.waitForResponse(
            (response) =>
                response.url().includes(`/api/limit-up-ladder?`) &&
                new URL(response.url()).searchParams.get("date") === date,
        );
        await field.fill(date);
        const response = await pending;
        expect(response.ok()).toBe(true);
        const body = await response.json();
        expect(body.date).toBe(date);
        expect(body.source).toBe("akshare/eastmoney");
        expect(body.stocks.length).toBeGreaterThan(0);
        await expect(page.getByText(`数据日期 ${date}`, { exact: false })).toBeVisible();
        await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(body.stocks.length);
    }
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出当日明细" }).click();
    expect((await download).suggestedFilename()).toBe("wavequant-limit-up-2026-09-18.csv");
    await page.getByLabel("筛选涨停股票").fill("不可能匹配的证券");
    await expect(page.getByText("没有匹配的涨停股票")).toBeVisible();
    await page.getByLabel("筛选涨停股票").fill("");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth))
        .toBe(true);
    await expect(field).toBeVisible();
    await page.screenshot({ path: "test-results/limit-up-ladder-mobile.png" });
    expect(errors).toEqual([]);
});

test("date race, empty pool, provider failure and retry never show another day's stocks", async ({ page }) => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
        release = resolve;
    });
    let failing = true;
    await page.route("**/api/limit-up-ladder?*", async (route) => {
        const date = new URL(route.request().url()).searchParams.get("date") || "2026-09-21";
        if (date === "2026-09-17") await held;
        if (date === "2026-09-20" && failing) {
            await route.fulfill({ status: 503, json: { error: "provider unavailable" } });
            return;
        }
        await route.fulfill({
            json: {
                date,
                source: "akshare/eastmoney",
                fetched_at: "2026-09-22T10:00:00+08:00",
                status: date === "2026-09-19" ? "empty" : "ok",
                notice: "测试数据",
                stocks:
                    date === "2026-09-19"
                        ? []
                        : [
                              {
                                  code: "000001",
                                  name: `日期${date}`,
                                  streak: 2,
                                  price: 10,
                                  change_pct: 10,
                                  amount: null,
                                  turnover_pct: null,
                                  seal_amount: null,
                                  first_seal: "09:30:00",
                                  last_seal: null,
                                  open_count: 0,
                                  industry: "测试行业",
                              },
                          ],
            },
        });
    });
    await page.goto("/market");
    await page.getByRole("tab", { name: "涨停阶梯", exact: true }).click();
    await expect(page.getByText("合成样本", { exact: true })).toHaveCount(0);
    const field = page.getByLabel("涨停天梯日期");
    const slow = page.waitForRequest((request) => request.url().includes("date=2026-09-17"));
    await field.fill("2026-09-17");
    await slow;
    await field.fill("2026-09-18");
    await expect(page.getByRole("table")).toContainText("日期2026-09-18");
    release();
    await expect(page.getByText("日期2026-09-17", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "后一天", exact: true }).click();
    await expect(page.getByText("2026-09-19 暂无可展示的涨停股票")).toBeVisible();
    await expect(page.getByRole("table")).toHaveCount(0);
    await page.getByRole("button", { name: "后一天", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "涨停池读取失败" })).toContainText("涨停池读取失败");
    await expect(page.getByRole("table")).toHaveCount(0);
    failing = false;
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await expect(page.getByRole("table")).toContainText("日期2026-09-20");
    await page.reload();
    await page.getByRole("tab", { name: "涨停阶梯", exact: true }).click();
    await expect(field).toHaveValue("2026-09-20");
});
