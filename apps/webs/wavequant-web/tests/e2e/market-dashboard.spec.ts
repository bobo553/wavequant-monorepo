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
        ["策略回测", "策略绩效"],
        ["模拟交易", "订单与信号"],
        ["系统与设置", "系统状态"],
        ["行情与复盘", "K 线复盘"],
    ] as const) {
        await page.getByRole("button", { name: new RegExp(pageName) }).click();
        await expect(page.locator("#page-title")).toHaveText(title);
    }
    expect(pageErrors).toEqual([]);
});

test("Huaxia Bank level-two last-fall-high reanchors after the old low close break", async ({ page }) => {
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#symbol-select").selectOption("sh.600015");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#secondary-trend-summary")).toContainText("二级末跌高 7.52");

    const state = await page.evaluate(async () => {
        const [theory, annotations] = await Promise.all([
            fetch("/api/tdx-theory?symbol=sh.600015&asof=2026-09-07").then((response) => response.json()),
            new Function("return import('/annotations.js')")(),
        ]);
        const stroke = theory.secondary_trends.strokes.find((candidate: { points: { time: string }[] }) =>
            candidate.points.some((point) => point.time === "2026-01-23"),
        );
        const summary = annotations.reversalWindowSummary([stroke], "2025-01-01", "2026-09-07");
        return {
            transition: stroke.key_transitions.find(
                (event: { broken_low: { time: string } }) => event.broken_low.time === "2026-01-23",
            ),
            low: summary.low,
            key: summary.lastFallHigh,
        };
    });
    expect(state.transition.available_at).toBe("2026-08-31");
    expect(state.transition.confirmed_by.value).toBe(6.23);
    expect(state.low).toMatchObject({ time: "2026-06-29", value: 6.34 });
    expect(state.key).toMatchObject({ time: "2026-04-02", value: 7.52 });
    expect(pageErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
});

test("the shared dashboard shell works on desktop and mobile", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/market");
    await page.locator("[data-shell-structure='market']").evaluate((shell) => {
        shell.setAttribute("data-stability-probe", "preserved");
    });
    await page.locator('a[href="/research?page=workspace"]').first().click();
    await expect(page).toHaveURL(/\/research\?page=workspace/);
    await expect(page.locator("[data-shell-structure='market']")).toHaveAttribute("data-stability-probe", "preserved");
    await expect(page.locator("[data-shell-structure='market']")).toHaveAttribute("data-shell-variant", "research");
    await expect(page.getByText("Market & Research")).toBeVisible();
    await expect(page.getByText("Trading & Control")).toBeVisible();
    await expect(page.locator("header").getByText("本地研究")).toBeVisible();
    await expect(page.getByRole("button", { name: /搜索股票/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看通知" })).toBeVisible();
    await expect(page.getByRole("button", { name: "界面设置" })).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新当前视图" })).toBeVisible();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.getByRole("button", { name: "运行当前股票回测" })).toBeVisible();
    await page.getByRole("button", { name: /搜索股票/ }).click();
    await expect(page.getByRole("dialog", { name: "搜索股票或题材" })).toBeVisible();
    await page.getByRole("button", { name: "关闭对话框" }).click();
    await page.getByRole("button", { name: "刷新当前视图" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.getByRole("button", { name: "界面设置" }).click();
    await expect(page.getByRole("dialog", { name: "工作区对话框" })).toBeVisible();
    await page.getByRole("button", { name: "关闭对话框" }).click();
    await page.getByRole("button", { name: "收起侧栏" }).click();
    await expect(page.getByRole("button", { name: "展开侧栏" })).toBeVisible();
    await expect.poll(() => page.evaluate(() => localStorage.getItem("wavequant.sidebar.collapsed.v1"))).toBe("true");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/research?page=performance");
    await page.getByRole("button", { name: "打开侧栏" }).click();
    await expect(page.locator("#wavequant-mobile-sidebar")).toBeVisible();
    await page.getByRole("button", { name: "系统与设置" }).click();
    await expect(page.locator("#page-title")).toHaveText("系统状态");
    await expect(page).toHaveURL(/page=health/);
    await expect(page.locator("#wavequant-mobile-sidebar")).toHaveCount(0);
    expect(pageErrors).toEqual([]);
});

test("market overview and limit-up ladder remain usable across desktop and narrow viewports", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/market");
    await expect(page.locator("[data-shell-structure='market']")).toHaveAttribute("data-shell-variant", "market");
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
    await page.getByLabel("UI 主题色").selectOption("market-blue");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "market-blue");
    await expect
        .poll(() =>
            page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--primary").trim()),
        )
        .toBe("#4ea1ff");
    await page.getByLabel("主题", { exact: true }).selectOption("light");
    await expect(page.locator("html")).toHaveClass(/light/);
    await page.getByRole("button", { name: "关闭对话框" }).click();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "market-blue");
    await expect(page.locator("html")).toHaveClass(/light/);

    await expect(page.locator('a[href="/research?page=workspace"]').first()).toContainText("行情与复盘");
    await expect(page.locator('a[href="/research?page=performance"]').first()).toContainText("策略回测");
    await page.locator('a[href="/research?page=workspace"]').first().click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect
        .poll(() => page.locator(".research-content").evaluate((element) => getComputedStyle(element).backgroundColor))
        .toBe("rgb(243, 246, 250)");
    await expect
        .poll(() =>
            page
                .locator(".panel")
                .first()
                .evaluate((element) => getComputedStyle(element).backgroundColor),
        )
        .toBe("rgb(255, 255, 255)");
    await page.getByRole("button", { name: "界面设置" }).click();
    await page.getByLabel("UI 主题色", { exact: true }).selectOption("wavequant-teal");
    await expect
        .poll(() => page.locator(".eyebrow").evaluate((element) => getComputedStyle(element).color))
        .toBe("rgb(8, 127, 114)");
    await page.getByLabel("UI 主题色", { exact: true }).selectOption("market-blue");
    await expect
        .poll(() => page.locator(".eyebrow").evaluate((element) => getComputedStyle(element).color))
        .toBe("rgb(29, 100, 216)");
    await page.getByRole("button", { name: "关闭对话框" }).click();
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
