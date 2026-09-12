import { expect, test } from "@playwright/test";

test("the original stock project Web workbench is the default page", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("[data-wavequant-react-workbench='true']")).toBeVisible();
    await expect(page.getByRole("heading", { name: /让每一个信号，都有据可循/ })).toBeVisible();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.getByRole("button", { name: "运行当前股票回测" })).toBeVisible();
    await page.getByRole("button", { name: "符合买点" }).click();
    await expect(page.getByRole("button", { name: "扫描买点" })).toBeVisible();
    await expect(page.getByRole("button", { name: "对比四组幅度" })).toBeVisible();

    for (const [pageName, title] of [
        ["策略绩效", "策略绩效"],
        ["订单与信号", "订单与信号"],
        ["系统状态", "系统状态"],
        ["K 线复盘", "K 线复盘"],
    ] as const) {
        await page.getByRole("button", { name: new RegExp(pageName) }).click();
        await expect(page.locator("#page-title")).toHaveText(title);
    }
    expect(pageErrors).toEqual([]);
});

test("market overview and limit-up ladder remain usable across desktop and narrow viewports", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/market");
    await expect(page.getByRole("heading", { name: "市场看盘" })).toBeVisible();
    await expect(page.getByText("大盘与市场广度")).toBeVisible();
    await expect(page.getByText("1,060.36")).toBeVisible();
    await expect(page.locator("canvas")).toHaveCount(1);

    await page.getByRole("tab", { name: "涨停阶梯" }).click();
    await expect(page.getByRole("heading", { name: "涨停阶梯 · 强弱接力" })).toBeVisible();
    await expect(page.getByRole("table", { name: "当前涨停股票池" })).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("tab", { name: "市场总览" }).click();
    const bodyWidth = await page.locator("body").evaluate((body) => body.scrollWidth);
    expect(bodyWidth).toBeLessThanOrEqual(390);
    expect(pageErrors).toEqual([]);
});

test("all original market workflows remain interactive after the Next.js migration", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/market");

    await page.getByLabel("样本日期").selectOption("2026-09-04");
    await page.getByRole("button", { name: "竞价" }).click();
    await expect(page.getByText("虚拟竞价参考")).toBeVisible();

    await page.keyboard.press("Control+K");
    const search = page.getByRole("textbox", { name: "名称、SIM 代码、板块或题材" });
    await search.fill("SIM001");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: /云启算力 · SIM001/ })).toBeVisible();
    await page.getByRole("button", { name: "核心关注" }).click();
    await page.getByRole("button", { name: "关闭对话框" }).click();

    const snapshot = page.waitForEvent("download");
    await page.getByRole("button", { name: "保存快照" }).click();
    await expect((await snapshot).suggestedFilename()).toContain("wavequant-snapshot-2026-09-04");

    await page.getByRole("tab", { name: "板块轮动" }).click();
    await page.getByRole("button", { name: "概念题材" }).click();
    await page.getByRole("button", { name: "3 日同刻" }).click();
    await expect(page.getByRole("button", { name: "1 日同刻" })).toBeVisible();

    await page.getByRole("tab", { name: "热点题材" }).click();
    await expect(page.getByRole("heading", { name: "热点题材 · 证据与反证" })).toBeVisible();
    await page
        .getByRole("button", { name: /核对材料时间/ })
        .first()
        .click();
    await expect(page.getByText(/发布时间与可获取时间分开保存/)).toBeVisible();
    await page.getByRole("button", { name: "关闭对话框" }).click();

    await page.getByRole("tab", { name: "龙头观察" }).click();
    await page.getByRole("tab", { name: "成交中军" }).click();
    await expect(page.getByText(/按当前成交额排序/)).toBeVisible();

    await page.getByRole("tab", { name: /异动雷达/ }).click();
    await page.getByRole("button", { name: "暂停插入" }).click();
    await expect(page.getByRole("button", { name: /恢复插入/ })).toBeVisible();
    await page.getByRole("button", { name: "标为已读" }).first().click();

    await page.getByRole("tab", { name: "多股同屏" }).click();
    await page.getByRole("button", { name: "9 图" }).click();
    await expect(page.getByRole("img", { name: /分时百分比走势/ })).toHaveCount(9);

    await page.getByRole("tab", { name: "盘后复盘" }).click();
    await page.getByRole("textbox", { name: "人工研究笔记" }).fill("核验板块扩散和炸板风险");
    await page.getByRole("button", { name: "保存笔记" }).click();
    await expect(page.getByRole("status")).toContainText("人工笔记已保存在当前样本日");
    const report = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出 Markdown" }).click();
    await expect((await report).suggestedFilename()).toContain("盘中观察");

    await page.getByRole("button", { name: "界面设置" }).click();
    await page.getByLabel("主题").selectOption("light");
    await expect(page.locator("html")).toHaveClass(/light/);
    await page.getByRole("button", { name: "关闭对话框" }).click();

    await expect(page.locator('a[href="/research?page=workspace"]').first()).toContainText("行情与复盘");
    await expect(page.locator('a[href="/research?page=performance"]').first()).toContainText("策略回测");
    expect(pageErrors).toEqual([]);
});

test("the text-equivalent classic v2 prototype keeps every original module reachable", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/wavequant-v2-classic.html");

    for (const [navigation, heading] of [
        ["research", "行情与复盘"],
        ["backtest", "策略回测"],
        ["trading", "模拟交易"],
        ["signals", "信号中心"],
        ["settings", "系统与设置"],
        ["market", "市场看盘"],
    ] as const) {
        await page.locator(`[data-nav="${navigation}"]`).first().click();
        await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
    }

    for (const tab of [
        "市场总览",
        "板块轮动",
        "热点题材",
        "涨停阶梯",
        "龙头观察",
        "异动雷达",
        "多股同屏",
        "盘后复盘",
    ]) {
        await page.getByRole("tab", { name: new RegExp(tab) }).click();
    }

    expect(pageErrors).toEqual([]);
});
