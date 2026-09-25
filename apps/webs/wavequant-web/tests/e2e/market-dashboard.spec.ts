import { expect, test } from "@playwright/test";

async function selectTdx(page: import("@playwright/test").Page): Promise<void> {
    await page.locator("#result-scope").selectOption("tdx");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
}

async function selectSymbol(page: import("@playwright/test").Page, symbol: string): Promise<void> {
    await page.locator("#symbol-select").evaluate((field, value) => {
        (field as HTMLInputElement).value = value;
        field.dispatchEvent(new Event("change", { bubbles: true }));
    }, symbol);
}

test("hovering a candle shows its prices and copies the selected bar", async ({ page, context }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    const chart = page.locator("#price-chart");
    await chart.scrollIntoViewIfNeeded();
    const viewport = await chart.boundingBox();
    expect(viewport).not.toBeNull();
    await page.mouse.move(viewport!.x + viewport!.width * 0.55, viewport!.y + viewport!.height * 0.4);

    const tooltip = chart.locator(".chart-tooltip");
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText("开盘");
    await expect(tooltip).toContainText("最高");
    await expect(tooltip).toContainText("最低");
    await expect(tooltip).toContainText("收盘");
    await expect(tooltip).toContainText("涨跌幅");
    await expect(tooltip).toContainText("成交量");
    const changeText = await tooltip
        .locator(".chart-tooltip-price")
        .filter({ hasText: "涨跌幅" })
        .locator("span")
        .last()
        .textContent();
    const hoveredDate = (await tooltip.locator("b").first().textContent())!.split(" · ")[0];
    await expect(page.locator("#copy-candle")).toHaveAttribute("aria-label", `复制 ${hoveredDate} K 线数据`);

    await page.evaluate(() => navigator.clipboard.writeText("other clipboard content"));
    await page.keyboard.press("ControlOrMeta+C");
    await expect(page.locator("#candle-copy-feedback")).toContainText(`已复制 ${hoveredDate}`);
    const shortcutCopied = await page.evaluate(() => navigator.clipboard.readText());
    expect(shortcutCopied).toContain("股票：600519 贵州茅台（sh.600519）");
    expect(shortcutCopied).toContain(`日期：${hoveredDate}`);
    expect(shortcutCopied).toContain("成交量：");
    expect(shortcutCopied).toContain(`涨跌幅：${changeText}`);

    const tooltipCopy = tooltip.getByRole("button", { name: `复制 ${hoveredDate} K 线数据，也可按 Ctrl+C` });
    await expect(tooltipCopy).toHaveText("Ctrl+C 复制");
    expect(await tooltip.evaluate((element) => getComputedStyle(element).pointerEvents)).toBe("auto");
    expect(await tooltip.evaluate((element) => getComputedStyle(element).userSelect)).toBe("text");
    const anchoredPosition = await tooltip.evaluate((element) => ({
        left: element.style.left,
        top: element.style.top,
    }));
    await tooltipCopy.click();
    await expect(tooltipCopy).toHaveText("已复制");
    await expect(page.locator("#candle-copy-feedback")).toContainText(`已复制 ${hoveredDate}`);
    const clickedCopy = await page.evaluate(() => navigator.clipboard.readText());
    expect(clickedCopy).toContain("股票：600519 贵州茅台（sh.600519）");
    expect(clickedCopy).toContain(`日期：${hoveredDate}`);
    expect(await tooltip.evaluate((element) => ({ left: element.style.left, top: element.style.top }))).toEqual(
        anchoredPosition,
    );
    await tooltip.locator(".chart-tooltip-price span").first().click({ clickCount: 3 });
    expect(await page.evaluate(() => window.getSelection()?.toString())).toContain("开盘");
    await page.keyboard.press("ControlOrMeta+C");
    const selectedText = await page.evaluate(() => navigator.clipboard.readText());
    expect(selectedText).toContain("开盘");
    expect(selectedText).not.toContain("日期：");

    await page.locator("#copy-candle").click();
    await expect(page.locator("#candle-copy-feedback")).toContainText(`已复制 ${hoveredDate}`);
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain("股票：600519 贵州茅台（sh.600519）");
    expect(copied).toContain(`日期：${hoveredDate}`);
    for (const field of ["开盘", "最高", "最低", "收盘", "成交量"]) {
        expect(copied).toContain(`${field}：`);
    }
    const search = page.locator("#header-stock-search");
    await search.fill("copy native input");
    await search.selectText();
    await page.keyboard.press("ControlOrMeta+C");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("copy native input");
    await page.setViewportSize({ width: 390, height: 844 });
    const copyButton = page.locator("#copy-candle");
    await copyButton.scrollIntoViewIfNeeded();
    await expect(copyButton).toBeVisible();
    await copyButton.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#candle-copy-feedback")).toContainText(`已复制 ${hoveredDate}`);
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(copied);
    expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
    ).toBe(true);
    expect(pageErrors).toEqual([]);
});

test("level-two and level-three guides follow their key and breakout candles into the viewport", async ({ page }) => {
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });

    const guideCounts = await page.evaluate(async () => {
        const { PriceChart } = await new Function("return import('/charts.js')")();
        const container = document.createElement("div");
        container.style.cssText = "position:fixed;left:-10000px;top:0;width:800px;height:400px";
        document.body.append(container);
        const chart = new PriceChart(container, () => {});
        const guides = [
            {
                id: "level-2-guide",
                time: "2025-01-10",
                price: 12,
                color: "#d6a3ff",
                title: "Ⅱ 末跌高",
                raw: {
                    trend_level: 2,
                    selected_low: { time: "2025-01-20" },
                    breakout: { time: "2025-02-10" },
                },
            },
            {
                id: "level-3-guide",
                time: "2025-01-15",
                price: 13,
                color: "#ffad72",
                title: "Ⅲ 末跌高",
                raw: {
                    trend_level: 3,
                    selected_low: { time: "2025-01-25" },
                    breakout: { time: "2025-02-15" },
                },
            },
        ];
        const count = (from: string, to: string) => {
            chart.drawLastFallHighGuides(guides, from, to);
            return Number(container.dataset.lastFallHighGuides);
        };
        const result = {
            keyCandlesVisible: count("2025-01-01", "2025-01-31"),
            neitherAnchorVisible: count("2025-03-01", "2025-03-31"),
            breakoutCandlesVisible: count("2025-02-01", "2025-02-28"),
        };
        chart.destroy();
        container.remove();
        return result;
    });

    expect(guideCounts).toEqual({
        neitherAnchorVisible: 0,
        keyCandlesVisible: 2,
        breakoutCandlesVisible: 2,
    });
});

test("experiment and strategy selectors remain usable across market tabs and scopes", async ({ page }) => {
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    const run = page.locator("#run-select");
    const variant = page.locator("#variant-select");
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    await expect(page.locator("#all-stocks-tab")).toHaveCount(0);
    await expect(page.locator("#stock-picker-toggle")).toHaveAttribute("aria-expanded", "true");
    await expect(run).toBeEnabled();
    await expect(variant).toBeEnabled();

    const alternateRun = await run.locator("option").nth(1).getAttribute("value");
    expect(alternateRun).toBeTruthy();
    await run.selectOption(alternateRun!);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await variant.selectOption("lecture_v2");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#buy-points-tab").click();
    await page.locator("#stock-picker-toggle").click();
    await expect(page.locator("#stock-picker-panel")).toBeVisible();
    await expect(run).toBeEnabled();
    await expect(variant).toBeEnabled();
    await expect(run).toHaveValue(alternateRun!);
    await expect(variant).toHaveValue("lecture_v2");

    await page.locator("#result-scope").selectOption("stock");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(run).toBeEnabled();
    await expect(variant).toBeEnabled();
    await page.locator("#result-scope").selectOption("portfolio");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(variant).toBeEnabled();
    await expect(variant).toHaveValue("strict_full");
    await expect(variant.locator('option[value="lecture_v2"]')).toHaveAttribute("disabled", "");
});

test("AkShare stock selection requests only the selected source's backtest", async ({ page }) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    const tdxBacktestRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/api/tdx-backtest") tdxBacktestRequests.push(request.url());
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#all-stocks-tab")).toHaveCount(0);
    await expect(page.locator("#stock-picker-panel")).toBeVisible();

    const backtest = page.waitForRequest(
        (request) => request.url().includes("/api/akshare-backtest?") && request.url().includes("symbol=sh.600519"),
        { timeout: 60_000 },
    );
    await page.locator("#header-stock-search").fill("600519");
    await page.locator("#header-stock-search").press("Enter");
    await backtest;
    await expect(page.locator("#result-scope")).toHaveValue("akshare-backtest", { timeout: 60_000 });
    await expect(page.locator("#stock-picker-current")).toContainText("600519");

    await page.locator("#stock-picker-toggle").click();
    await expect(page.locator("#header-stock-search")).toBeVisible();
    await page.locator("#header-stock-search").focus();
    await expect(page.locator("#header-stock-search")).toBeFocused();

    await page.setViewportSize({ width: 390, height: 844 });
    const rail = await page.locator(".stock-browser").boundingBox();
    expect(rail?.width).toBeLessThanOrEqual(390);
    expect(tdxBacktestRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
});

test("an unavailable TDX catalog does not disable an available AkShare stock", async ({ page }) => {
    const tdxRequests: string[] = [];
    page.on("request", (request) => {
        if (["/api/tdx-catalog", "/api/tdx-backtest"].includes(new URL(request.url()).pathname))
            tdxRequests.push(request.url());
    });
    await page.route("**/api/tdx-catalog", (route) =>
        route.fulfill({ json: { available: false, latest: null, with_daily: 0, stocks: [] } }),
    );

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#header-stock-search").fill("600519");
    await page.locator("#header-stock-search").press("Enter");
    await expect(page.locator("#stock-picker-feedback")).toBeHidden();
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    await expect(page.locator("#run-stock-backtest")).toBeEnabled();
    expect(tdxRequests).toEqual([]);
});

test("close-based half-wave profile reaches the current-stock backtest", async ({ page }) => {
    test.setTimeout(180_000);
    // Keep catalog loading deterministic; the backtest itself still goes to
    // the real API with an explicitly selected local TDX stock.
    const catalog = {
        available: true,
        latest: "2026-09-07",
        with_daily: 1,
        stocks: [
            {
                symbol: "sh.600519",
                name: "贵州茅台",
                has_data: true,
                last: "2026-09-07",
                bar_count: 2000,
                source: "tdx",
            },
        ],
    };
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace&symbol=sh.600519&asof=2026-09-07&source=tdx");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#result-scope")).toHaveValue("tdx");
    await page.locator("#variant-select").selectOption("lecture_v3_close_d50_c50");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    const backtest = page.waitForResponse(
        (response) =>
            response.url().includes("/api/tdx-backtest?") &&
            response.url().includes("variant=lecture_v3_close_d50_c50") &&
            response.ok(),
        { timeout: 180_000 },
    );
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const view = await (await backtest).json();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#result-scope")).toHaveValue("tdx-backtest");
    await expect(page.locator("#variant-select")).toHaveValue("lecture_v3_close_d50_c50");
    expect(view.backtest.strategy.first_pullback_basis).toBe("minimum_close");
    expect(view.backtest.strategy.mature_shallow_inclusive).toBe(false);
    await expect(page.locator("#backtest-details")).toContainText("最低收盘价");
    await expect(page.locator("#backtest-details")).toContainText("< 50.00%");
    const expectedCandidates = view.theory.events.filter(
        (event: { event: string }) => event.event === "entry_rejected" || event.event === "entry_preflight_rejected",
    ).length;
    expect(expectedCandidates).toBeGreaterThan(0);
    const blockedDates = new Set([
        ...view.theory.events
            .filter(
                (event: { event: string }) =>
                    event.event === "entry_rejected" || event.event === "entry_preflight_rejected",
            )
            .map((event: { available_at: string }) => event.available_at),
        ...view.markers
            .filter(
                (marker: { kind: string; status?: string }) => marker.kind === "order" && marker.status === "cancelled",
            )
            .map((marker: { time: string }) => marker.time),
    ]);
    await expect(page.locator("#trade-nodes-blocked-count")).toHaveText(String(blockedDates.size));
    await page.locator("#trade-nodes-blocked-tab").click();
    await expect(page.locator("#trade-nodes-blocked-list .trade-node-item")).toHaveCount(blockedDates.size);
    const candidate = page.locator('#trade-nodes-blocked-list [data-blocked-stage="screening"]').first();
    await candidate.click();
    await expect(page.locator("#selection-info")).toContainText("未提交买单");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-level-count", "1");
});

test("chart stock label replaces the dropdown and copies the selected stock", async ({ page, context }) => {
    test.setTimeout(120_000);
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 90_000 });
    await expect(page.locator("select#symbol-select")).toHaveCount(0);

    await page.locator("#header-stock-search").fill("600015");
    await page.locator("#header-stock-search").press("Enter");
    await expect(page.locator("#symbol-copy-text")).toContainText("600015");
    const displayed = (await page.locator("#symbol-copy-text").textContent())?.trim();
    expect(displayed).toMatch(/^600015\s+\S+/);
    await page.locator("#symbol-copy").click();
    await expect(page.locator("#symbol-copy-feedback")).toHaveText(`已复制：${displayed}`);
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(displayed);
    await page.locator("#symbol-copy").focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#symbol-copy")).toHaveAttribute("data-copy-state", "success");
});

test("the original stock project Web workbench is the default page", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("[data-wavequant-react-workbench='true']")).toBeVisible();
    await expect(page.getByRole("heading", { name: /让每一个信号，都有据可循/ })).toBeVisible();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    await expect(page.locator("#stock-source-notice")).toContainText("买点与结构仅读服务器预计算结果");
    await expect(page.getByRole("button", { name: "运行当前股票回测" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "运行当前股票回测" })).toHaveAttribute(
        "title",
        "回测将切换到通达信因果复权口径",
    );
    await page.getByRole("button", { name: "符合买点" }).click();
    await expect(page.getByRole("button", { name: "查询当前股票买点" })).toBeVisible();
    await expect(page.getByRole("button", { name: "对比五组幅度" })).toBeVisible();

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

test("running a stock backtest reveals the trade journal and any B/S executions", async ({ page }) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    await selectSymbol(page, "sh.600519");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });

    await expect(page.locator("#result-scope")).toHaveValue("tdx-backtest");
    await expect(page.locator("#show-fills")).toBeChecked();
    await expect(page.locator("#all-stocks-tab")).toHaveCount(0);
    await expect(page.locator("#trade-nodes-tab")).toBeVisible();
    await expect(page.locator("#trade-nodes-tab")).toHaveAttribute("aria-pressed", "true");
    expect((await page.locator("#trade-nodes-tab").boundingBox())?.y).toBeGreaterThan(60);
    await expect(page.locator("#trade-nodes-panel")).toBeVisible();
    const fillCount = await page.locator("#fills-body tr").count();
    await expect(page.locator("#trade-nodes-list .trade-node-button")).toHaveCount(fillCount);
    if (fillCount) {
        const buyCount = Number(await page.locator("#trade-nodes-buy-count").textContent());
        await page.locator('[data-trade-node-filter="BUY"]').click();
        await expect(page.locator("#trade-nodes-list .trade-node-item:visible")).toHaveCount(buyCount);
        await page.locator('[data-trade-node-filter="all"]').click();
        await page.locator("#trade-nodes-list .trade-node-button").first().click();
        await expect(page.locator("#trade-nodes-list .trade-node-button").first()).toHaveAttribute(
            "aria-current",
            "true",
        );
        const selectedFillId = await page
            .locator("#trade-nodes-list .trade-node-button")
            .first()
            .getAttribute("data-trade-marker-id");
        if (!selectedFillId) throw new Error("买卖成交节点缺少图表标识");
        await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-active", "true");
        await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-id", selectedFillId);
        const flashPoint = await page.locator("#price-chart").evaluate((element) => ({
            x: Number(element.dataset.focusFlashX),
            y: Number(element.dataset.focusFlashY),
            width: element.clientWidth,
            height: element.clientHeight,
        }));
        expect(flashPoint.x).toBeGreaterThan(0);
        expect(flashPoint.x).toBeLessThan(flashPoint.width);
        expect(flashPoint.y).toBeGreaterThan(0);
        expect(flashPoint.y).toBeLessThan(flashPoint.height);
        if (fillCount > 1) {
            const earlierFill = page.locator("#trade-nodes-list .trade-node-button").last();
            const earlierFillId = await earlierFill.getAttribute("data-trade-marker-id");
            if (!earlierFillId) throw new Error("较早成交节点缺少图表标识");
            await earlierFill.click();
            await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-id", earlierFillId);
        }
        await expect
            .poll(async () => Number(await page.locator("#price-chart").getAttribute("data-trade-label-count")))
            .toBeGreaterThan(0);
        await expect(page.locator("#selection-info")).toHaveAttribute("data-annotation-id", /^stock-order-/);
        await expect(page.locator("#price-chart")).toBeInViewport({ ratio: 0.2 });
    } else {
        await expect(page.locator("#trade-nodes-empty")).toContainText("没有模拟成交");
    }
    await page.locator("#buy-points-tab").click();
    await expect(page.locator("#trade-nodes-panel")).toBeHidden();
    await expect(page.locator("#buy-points-panel")).toBeVisible();
    await page.locator("#trade-nodes-tab").click();
    await expect(page.locator("#trade-nodes-panel")).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
        page.locator(fillCount ? "#trade-nodes-list .trade-node-button" : "#trade-nodes-empty").first(),
    ).toBeVisible();
    const railBounds = await page.locator(".stock-browser").boundingBox();
    expect(railBounds?.width).toBeLessThanOrEqual(390);
    await page.locator("#result-scope").selectOption("akshare");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#trade-nodes-tab")).toBeHidden();
    await expect(page.locator("#stock-picker-toggle")).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("#stock-list")).toBeVisible();
    expect(pageErrors).toEqual([]);
});

test("each buy shows its own entry weight while average exposure stays a full-period metric", async ({ page }) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.route("**/api/tdx-backtest?*", async (route) => {
        const target = new URL(route.request().url());
        if (process.env.WAVEQUANT_API_PORT) {
            target.hostname = "127.0.0.1";
            target.port = process.env.WAVEQUANT_API_PORT;
        }
        const response = await route.fetch({ url: target.toString() });
        if (!response.ok()) throw new Error(`回测测试数据接口返回 ${response.status()}`);
        const view = await response.json();
        const bars = view.bars;
        const dates = [bars.at(-8).time, bars.at(-7).time, bars.at(-4).time, bars.at(-3).time];
        const fills = [
            {
                side: "BUY",
                time: dates[0],
                price: 20,
                quantity: 6035,
                equity_at_open: 1_000_000,
                fee: 0,
                entry_position_weight: 0.1207,
                trade_id: "fixture-1",
            },
            { side: "SELL", time: dates[1], price: 22, quantity: 6035, fee: 0, trade_id: "fixture-1" },
            {
                side: "BUY",
                time: dates[2],
                price: 25,
                quantity: 2188,
                equity_at_open: 1_000_000,
                fee: 0,
                trade_id: "fixture-2",
            },
            { side: "SELL", time: dates[3], price: 27, quantity: 2188, fee: 0, trade_id: "fixture-2" },
        ].map((fill, index) => ({
            ...fill,
            id: `stock-order-${index}`,
            timestamp: `${fill.time}T00:00:00`,
            signal_time: fill.time,
            status: "filled",
            kind: "fill",
            reason: "fixture_entry_or_exit",
            raw_price: fill.price,
            symbol: view.symbol,
        }));
        view.markers = fills;
        view.orders = fills;
        view.trades = [
            [dates[0], dates[1]],
            [dates[2], dates[3]],
        ].map(([entry, exit]) => ({
            symbol: view.symbol,
            entry_time: `${entry}T00:00:00`,
            exit_time: `${exit}T00:00:00`,
            bars_held: 1,
            pnl: 100,
            net_return: 0.01,
            fees: 0,
            entry_reason: "fixture",
        }));
        view.metrics = { ...view.metrics, entry_fills: 2, trades: 2, open_positions: 0, average_exposure: 0.0004 };
        await route.fulfill({ json: view });
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    await selectSymbol(page, "sh.600519");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#metric-exposure")).toHaveText("0.04%");
    await expect(page.locator('#trade-nodes-list .trade-node-item[data-side="BUY"]')).toHaveCount(2);
    await expect(page.locator('#trade-nodes-list .trade-node-item[data-side="BUY"]')).toContainText([
        "买入后仓位 5.47%",
        "买入后仓位 12.07%",
    ]);
    await expect(page.locator("#fills-body tr").filter({ hasText: "B 买入" })).toContainText(["5.47%", "12.07%"]);
    expect(pageErrors).toEqual([]);
});

test("backtest right rail distinguishes an empty trade journal from strategy signals", async ({ page }) => {
    test.setTimeout(180_000);
    await page.route("**/api/tdx-backtest?*", async (route) => {
        const response = await route.fetch();
        if (!response.ok())
            throw new Error(`当前股票回测接口返回 ${response.status()}：${(await response.text()).slice(0, 300)}`);
        const data = await response.json();
        data.markers = data.markers.filter(
            (marker: { kind: string; status?: string }) =>
                marker.kind !== "fill" && !(marker.kind === "order" && marker.status === "cancelled"),
        );
        data.theory.events = data.theory.events.filter(
            (event: { event: string }) =>
                event.event !== "entry_rejected" && event.event !== "entry_preflight_rejected",
        );
        data.trades = [];
        data.metrics = { ...data.metrics, entry_fills: 0, trades: 0, open_positions: 0 };
        await route.fulfill({ response, json: data });
    });
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#trade-nodes-tab")).toBeVisible();
    await expect(page.locator("#trade-nodes-list .trade-node-item")).toHaveCount(0);
    await expect(page.locator("#trade-nodes-empty")).toContainText("没有模拟成交");
    await page.locator("#trade-nodes-blocked-tab").click();
    await expect(page.locator("#trade-nodes-blocked-list .trade-node-item")).toHaveCount(0);
    await expect(page.locator("#trade-nodes-blocked-empty")).toContainText("没有被拦截的候选或委托");
    await expect(page.locator("#trade-nodes-copy-all")).toBeDisabled();
    await expect(page.locator("#all-stocks-tab")).toHaveCount(0);
});

test("blocked order tab explains a rejected buy and locates its attempted candle", async ({ page }) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    let duplicatedDate = "";
    let groupedDateCount = 0;
    let blockedEventCount = 0;
    let duplicatedDateCount = 0;
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.route("**/api/tdx-backtest?*", async (route) => {
        const response = await route.fetch();
        if (!response.ok())
            throw new Error(`当前股票回测接口返回 ${response.status()}：${(await response.text()).slice(0, 300)}`);
        const data = await response.json();
        const candidate = data.theory.events.find(
            (event: { event: string }) =>
                event.event === "entry_rejected" || event.event === "entry_preflight_rejected",
        );
        if (!candidate) throw new Error("测试样本缺少被筛选的候选");
        duplicatedDate = candidate.available_at.slice(0, 10);
        data.theory.events.push({
            ...candidate,
            id: `${candidate.id}-same-day-check`,
            reason:
                candidate.reason === "attack_volume_unavailable_or_low"
                    ? "not_squeeze_regime"
                    : "attack_volume_unavailable_or_low",
        });
        const dates = [
            ...data.theory.events
                .filter(
                    (event: { event: string }) =>
                        event.event === "entry_rejected" || event.event === "entry_preflight_rejected",
                )
                .map((event: { available_at: string }) => event.available_at.slice(0, 10)),
            ...data.markers
                .filter(
                    (marker: { kind: string; status?: string }) =>
                        marker.kind === "order" && marker.status === "cancelled",
                )
                .map((marker: { time: string }) => marker.time.slice(0, 10)),
        ];
        blockedEventCount = dates.length;
        groupedDateCount = new Set(dates).size;
        duplicatedDateCount = dates.filter((date) => date === duplicatedDate).length;
        await route.fulfill({ response, json: data });
    });
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    await selectSymbol(page, "sh.600519");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#variant-select").selectOption("lecture_v3_d67_c50");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await expect(page.locator("#fills-body tr")).toHaveCount(0);
    await expect(page.locator("#trade-nodes-filled-tab")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#trade-nodes-empty")).toContainText("没有模拟成交");
    await expect(page.locator("#trade-nodes-blocked-count")).toHaveText(String(groupedDateCount));

    await page.locator("#trade-nodes-blocked-tab").click();
    await expect(page.locator("#trade-nodes-blocked-panel")).toBeVisible();
    await expect(page.locator("#trade-nodes-filled-panel")).toBeHidden();
    await expect(page.locator("#trade-nodes-blocked-list .trade-node-item")).toHaveCount(groupedDateCount);
    await expect(page.locator("#trade-nodes-blocked-summary")).toContainText(`${blockedEventCount} 次`);
    const sameDayCard = page.locator(`#trade-nodes-blocked-list .trade-node-item[data-date="${duplicatedDate}"]`);
    await expect(sameDayCard).toHaveCount(1);
    await expect(sameDayCard).toContainText("量能不足或无法计算");
    await sameDayCard.locator("summary").click();
    await expect(sameDayCard.locator("details button")).toHaveCount(duplicatedDateCount);
    const injectedEvent = sameDayCard.locator('details button[data-trade-marker-id$="-same-day-check"]');
    await injectedEvent.click();
    await expect(sameDayCard.locator(".trade-node-button")).toHaveAttribute("aria-current", "true");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-id", /same-day-check$/);
    const blocked = page.locator('#trade-nodes-blocked-list .trade-node-button[data-blocked-stage="execution"]');
    await expect(blocked).toHaveCount(1);
    await expect(blocked).toContainText("2022-07-06");
    await expect(blocked).toContainText("决定 2022-07-05");
    await expect(blocked).toContainText("单笔风险预算不足以买入一手");
    await blocked.click();
    await expect(blocked).toHaveAttribute("aria-current", "true");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-active", "true");
    const blockedId = await blocked.getAttribute("data-trade-marker-id");
    if (blockedId === null) throw new Error("被拦截委托缺少图表标识");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-id", blockedId);
    const flashPoint = await page.locator("#price-chart").evaluate((element) => ({
        x: Number(element.dataset.focusFlashX),
        y: Number(element.dataset.focusFlashY),
        width: element.clientWidth,
        height: element.clientHeight,
    }));
    expect(flashPoint.x).toBeGreaterThan(0);
    expect(flashPoint.x).toBeLessThan(flashPoint.width);
    expect(flashPoint.y).toBeGreaterThan(0);
    expect(flashPoint.y).toBeLessThan(flashPoint.height);
    await expect(page.locator("#selection-info")).toHaveAttribute("data-annotation-id", /^stock-order-/);
    await expect(page.locator("#selection-info")).toContainText("2022-07-06");
    await expect(page.locator("#selection-info")).toContainText("单笔风险预算不足以买入一手");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-level-count", "1");
    await expect(page.locator("#price-chart")).toBeInViewport({ ratio: 0.2 });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-active", "false", { timeout: 5_000 });
    await blocked.click();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-active", "true");
    const candidate = page
        .locator('#trade-nodes-blocked-list .trade-node-button[data-blocked-stage="screening"]')
        .first();
    await expect(candidate).toContainText("筛 · 未下单");
    await candidate.click();
    await expect(candidate).toHaveAttribute("aria-current", "true");
    const candidateId = await candidate.getAttribute("data-trade-marker-id");
    if (candidateId === null) throw new Error("被拦截候选缺少图表标识");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-id", candidateId);
    await expect(page.locator("#selection-info")).toContainText("当日入场候选未通过策略筛选");
    await expect(page.locator("#selection-info")).toContainText("未提交买单");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-level-count", "1");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await candidate.click();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-motion", "static");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-active", "false", { timeout: 5_000 });
    await page.locator("#trade-nodes-blocked-tab").focus();
    await page.keyboard.press("ArrowLeft");
    await expect(page.locator("#trade-nodes-filled-tab")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#trade-nodes-empty")).toContainText("没有模拟成交");
    await expect(page.locator("#trade-nodes-blocked-panel")).toBeHidden();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator("#trade-nodes-blocked-panel")).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(blocked).toBeVisible();
    expect((await page.locator(".stock-browser").boundingBox())?.width).toBeLessThanOrEqual(390);
    await candidate.click();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-active", "true");
    await page.locator("#result-scope").selectOption("akshare");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-active", "false");
    expect(pageErrors).toEqual([]);
});

test("blocked date cards and the complete list copy their underlying evidence", async ({ page, context }) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    await selectSymbol(page, "sh.600519");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#variant-select").selectOption("lecture_v3_d67_c50");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 180_000 });
    await page.locator("#trade-nodes-blocked-tab").click();
    const cards = page.locator("#trade-nodes-blocked-list .trade-node-item");
    const cardCount = await cards.count();
    expect(cardCount).toBeGreaterThan(0);
    const firstCard = cards.first();
    const firstDate = await firstCard.getAttribute("data-date");
    expect(firstDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const selectedBefore = await page.locator("#selection-info").getAttribute("data-annotation-id");
    await firstCard.locator(".trade-node-copy").click();
    await expect(page.locator("#trade-nodes-copy-feedback")).toContainText(`已复制${firstDate}`);
    const oneDayCopy = await page.evaluate(() => navigator.clipboard.readText());
    expect(oneDayCopy).toContain("股票：600519 贵州茅台（sh.600519）");
    expect(oneDayCopy).toContain(`${firstDate} ·`);
    expect(oneDayCopy).toContain("拦截原因：");
    expect(oneDayCopy).toContain("事件 ID：");
    expect(await page.locator("#selection-info").getAttribute("data-annotation-id")).toBe(selectedBefore);

    await page.locator("#trade-nodes-copy-all").click();
    await expect(page.locator("#trade-nodes-copy-feedback")).toContainText("已复制全部日期");
    const allCopy = await page.evaluate(() => navigator.clipboard.readText());
    expect(allCopy).toContain(`被拦截：${cardCount} 日`);
    expect((allCopy.match(/事件 ID：/g) || []).length).toBeGreaterThanOrEqual(cardCount);
    expect(allCopy.length).toBeGreaterThanOrEqual(oneDayCopy.length);
    await page.setViewportSize({ width: 390, height: 844 });
    const copyButton = firstCard.locator(".trade-node-copy");
    await expect(copyButton).toBeVisible();
    await copyButton.scrollIntoViewIfNeeded();
    await expect(copyButton).toBeInViewport();
    await copyButton.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#trade-nodes-copy-feedback")).toContainText(`已复制${firstDate}`);
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(oneDayCopy);
    expect(pageErrors).toEqual([]);
});

test("market candle timeframe switches server data, replay sessions and theory together", async ({ page }) => {
    test.setTimeout(90_000);
    const pageErrors: string[] = [];
    const requests: string[] = [];
    const responseStatuses: number[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("request", (request) => {
        const url = new URL(request.url());
        if (url.pathname === "/api/market-timeframe" && url.searchParams.get("timeframe") === "1w") {
            requests.push(url.pathname);
        }
    });
    page.on("response", (response) => {
        const url = new URL(response.url());
        if (url.pathname === "/api/market-timeframe" && url.searchParams.get("timeframe") === "1w") {
            responseStatuses.push(response.status());
        }
    });

    await page.goto("/research");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    const timeframeTabs = page.getByRole("tablist", { name: "K线周期" });
    await expect(timeframeTabs).toBeVisible();
    await expect(timeframeTabs.getByRole("tab")).toHaveText(["日", "周", "月", "季", "年"]);

    await timeframeTabs.getByRole("tab", { name: "周" }).click();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#timeframe-tag")).toHaveText("周 K");
    await expect(page.locator("#selected-stock-summary")).toContainText("周线截面");
    await expect.poll(() => requests).toContain("/api/market-timeframe");

    await timeframeTabs.getByRole("tab", { name: "周" }).press("ArrowRight");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(timeframeTabs.getByRole("tab", { name: "月" })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#selected-stock-summary")).toContainText("月线截面");
    await timeframeTabs.getByRole("tab", { name: "月" }).press("ArrowLeft");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(timeframeTabs.getByRole("tab", { name: "周" })).toHaveAttribute("aria-selected", "true");

    const [weekly, daily] = await page.evaluate(async () => {
        const [weeklyResponse, dailyResponse] = await Promise.all([
            fetch("/api/market-timeframe?source=tdx&symbol=sh.600519&asof=2026-09-07&timeframe=1w"),
            fetch("/api/market-timeframe?source=tdx&symbol=sh.600519&asof=2026-09-07&timeframe=1d"),
        ]);
        return Promise.all([weeklyResponse.json(), dailyResponse.json()]);
    });
    expect(weekly.timeframe).toBe("1w");
    expect(weekly.view.timeframe_label).toBe("周线");
    expect(weekly.view.bars.length).toBeLessThan(daily.view.bars.length);
    expect(weekly.theory.data_version).toBe(weekly.view.data_version);
    expect(
        await page.evaluate(() => JSON.parse(localStorage.getItem("wavequant.research.chart.v1") || "null").timeframe),
    ).toBe("1w");

    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.getByRole("tab", { name: "周" })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#timeframe-tag")).toHaveText("周 K");
    await expect.poll(() => responseStatuses).toContain(304);

    const cachedPeriods = await page.evaluate(async () => {
        const request = indexedDB.open("wavequant-market-data", 2);
        const database = await new Promise<IDBDatabase>((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
        const transaction = database.transaction("market-timeframes", "readonly");
        const all = transaction.objectStore("market-timeframes").getAll();
        const rows = await new Promise<Array<{ timeframe: string }>>((resolve, reject) => {
            all.onsuccess = () => resolve(all.result);
            all.onerror = () => reject(all.error);
        });
        database.close();
        return rows.map((row) => row.timeframe);
    });
    expect(cachedPeriods).toContain("1w");

    await page.locator("#result-scope").selectOption("stock");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    for (const tab of await timeframeTabs.getByRole("tab").all()) await expect(tab).toBeDisabled();
    await expect(page.getByRole("tab", { name: "日" })).toHaveAttribute("aria-selected", "true");
    expect(pageErrors).toEqual([]);
});

test("chart tools reveal detailed overlays without reducing the candle viewport", async ({ page }) => {
    test.setTimeout(90_000);
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
        if (message.type() === "error") consoleErrors.push(message.text());
    });

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/research");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });

    const chartCard = await page.locator(".chart-card").boundingBox();
    const chartHeader = await page.locator(".chart-card-header").boundingBox();
    const chartViewTabs = await page.locator(".chart-view-tabs").boundingBox();
    const ohlc = await page.locator("#ohlc").boundingBox();
    const candleViewport = await page.locator("#price-chart").boundingBox();
    const replay = await page.locator(".chart-card .replay").boundingBox();
    expect(chartCard).not.toBeNull();
    expect(chartHeader).not.toBeNull();
    expect(chartViewTabs).not.toBeNull();
    expect(ohlc).not.toBeNull();
    expect(candleViewport).not.toBeNull();
    expect(replay).not.toBeNull();
    expect(candleViewport!.y - chartCard!.y - chartViewTabs!.height).toBeLessThan(115);
    expect(chartCard!.height).toBeGreaterThanOrEqual(874);
    expect(candleViewport!.height + chartViewTabs!.height).toBeGreaterThan(650);
    expect(
        Math.abs(
            chartCard!.height -
                chartHeader!.height -
                chartViewTabs!.height -
                ohlc!.height -
                candleViewport!.height -
                replay!.height -
                2,
        ),
    ).toBeLessThanOrEqual(2);
    await expect
        .poll(() =>
            page.locator("#price-chart > .tv-lightweight-charts").evaluate((element) => ({
                host: element.parentElement?.getBoundingClientRect().height,
                chart: element.getBoundingClientRect().height,
            })),
        )
        .toEqual({ host: candleViewport!.height, chart: candleViewport!.height });

    const layersTrigger = page.getByRole("button", { name: /图层/ });
    const layersPopover = page.locator("#chart-layers-popover");
    await expect(layersPopover).toBeHidden();
    await layersTrigger.hover();
    await expect(layersPopover).toBeVisible();
    await expect(layersTrigger).toHaveAttribute("aria-expanded", "true");
    expect(await layersPopover.evaluate((element) => getComputedStyle(element).position)).toBe("fixed");
    expect((await page.locator("#price-chart").boundingBox())!.y).toBe(candleViewport!.y);

    const bullishTurnLayer = layersPopover.locator(".layer-option", { hasText: "各级转多信号" });
    await bullishTurnLayer.hover();
    await expect(bullishTurnLayer.getByRole("tooltip")).toContainText("首次收盘严格突破");
    await expect(bullishTurnLayer.getByRole("tooltip")).toBeVisible();

    await layersTrigger.click();
    await layersPopover.locator("#show-diagnostics").check();
    await expect(page.locator("#layer-toggle-count")).toHaveText("20/20");
    await page.locator("#ohlc").click();
    await expect(layersPopover).toBeHidden();

    await layersTrigger.focus();
    await expect(layersPopover).toBeVisible();
    await layersPopover.locator("#show-volume").focus();
    await page.keyboard.press("Escape");
    await expect(layersPopover).toBeHidden();
    await expect(layersTrigger).toBeFocused();

    const guideTrigger = page.getByRole("button", { name: "图例 ?" });
    await guideTrigger.hover();
    await expect(page.locator("#chart-guide-popover")).toBeVisible();
    await expect(page.locator("#drawing-status")).toContainText("讲义绘图");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#ohlc").click();
    const narrowCard = await page.locator(".chart-card").boundingBox();
    const narrowViewport = await page.locator("#price-chart").boundingBox();
    expect(narrowCard).not.toBeNull();
    expect(narrowViewport).not.toBeNull();
    expect(narrowCard!.height).toBeGreaterThanOrEqual(818);
    expect(narrowViewport!.height).toBeGreaterThan(500);
    await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth))
        .toBe(true);
    await expect(layersTrigger).toBeVisible();
    await expect(page.getByRole("button", { name: "图例 ?" })).toBeVisible();

    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
});

test("stock catalogs persist in IndexedDB and only transfer again after an ETag change", async ({ page }) => {
    const responses: Array<{ path: string; status: number }> = [];
    page.on("response", (response) => {
        const path = new URL(response.url()).pathname;
        if (path === "/api/tdx-catalog" || path === "/api/akshare-catalog") {
            responses.push({ path, status: response.status() });
        }
    });

    await page.goto("/research");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#stock-source-notice")).toContainText("IndexedDB 缓存已更新");
    await selectTdx(page);
    await page.locator("#result-scope").selectOption("akshare");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    const cached = await page.evaluate(
        () =>
            new Promise<Array<{ source: string; etag: string; count: number }>>((resolve, reject) => {
                const request = indexedDB.open("wavequant-market-data", 2);
                request.onerror = () => reject(request.error);
                request.onsuccess = () => {
                    const database = request.result;
                    const transaction = database.transaction("stock-catalogs", "readonly");
                    const entries = transaction.objectStore("stock-catalogs").getAll();
                    entries.onerror = () => reject(entries.error);
                    entries.onsuccess = () =>
                        resolve(
                            entries.result.map((entry) => ({
                                source: entry.source,
                                etag: entry.etag,
                                count: entry.catalog.stocks.length,
                            })),
                        );
                };
            }),
    );
    expect(cached.map((entry) => entry.source).sort()).toEqual(["akshare", "tdx"]);
    expect(cached.every((entry) => entry.etag.startsWith('"') && entry.count > 5_000)).toBe(true);

    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#stock-source-notice")).toContainText("IndexedDB 缓存已校验");
    await selectTdx(page);
    expect(responses.filter((response) => response.status === 200)).toHaveLength(2);
    expect(responses.filter((response) => response.status === 304)).toHaveLength(2);
});

test("AkShare current-stock buy points and market structure signals are operable", async ({ page }) => {
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));
    const signalRequests: string[] = [];
    let structureSymbol: string | null = "not-requested";
    const structureMarkets: Array<string | null> = [];
    const structureSignalTypes: Array<string | null> = [];
    await page.route("**/api/buy-signals?**", async (route) => {
        signalRequests.push(`${route.request().method()} buy`);
        const query = Object.fromEntries(new URL(route.request().url()).searchParams);
        await route.fulfill({
            json: {
                status: "ready",
                params: { ...query, lookback: Number(query.lookback) },
                snapshot: { id: "buy-snapshot", computed_at: "2026-09-14T01:00:00+00:00" },
                total: 1,
                processed: 1,
                skipped: 0,
                failed: 0,
                stale: 0,
                results: [],
                errors: [],
                skip_reasons: {},
                funnel: {},
            },
        });
    });
    await page.route("**/api/structure-signals?**", async (route) => {
        signalRequests.push(`${route.request().method()} structure`);
        const query = Object.fromEntries(new URL(route.request().url()).searchParams);
        structureSymbol = new URL(route.request().url()).searchParams.get("symbol");
        structureMarkets.push(new URL(route.request().url()).searchParams.get("markets"));
        structureSignalTypes.push(new URL(route.request().url()).searchParams.get("signal_type"));
        await route.fulfill({
            json: {
                status: "ready",
                params: {
                    ...query,
                    lookback: Number(query.lookback),
                    trend_level: Number(query.trend_level),
                },
                snapshot: {
                    id: "structure-snapshot",
                    computed_at: "2026-09-14T01:00:00+00:00",
                    data_version: "d".repeat(64),
                    algorithm_version: "a".repeat(64),
                },
                total: 1,
                processed: 1,
                skipped: 0,
                failed: 0,
                stale: 0,
                results: [],
                errors: [],
                skip_reasons: {},
                matched_stocks: 0,
                coverage: { scope: "akshare_market", published_stocks: 128 },
            },
        });
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator('#result-scope option[value="akshare"]')).toBeEnabled({ timeout: 60_000 });
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    await expect(page.locator("#stock-source-notice")).toContainText("买点与结构仅读服务器预计算结果");

    await page.getByRole("button", { name: "符合买点" }).click();
    const buyButton = page.getByRole("button", { name: "查询当前股票买点" });
    await expect(buyButton).toBeEnabled();
    await buyButton.click();
    await expect(page.locator("#scan-status")).toContainText("预计算结果已就绪 1 / 1", { timeout: 60_000 });
    await expect(page.locator("#scan-status")).toContainText("AkShare 当前股票");

    await page.getByRole("button", { name: "结构信号" }).click();
    await expect(page.getByLabel("上证")).toBeChecked();
    await expect(page.getByLabel("深证")).toBeChecked();
    await expect(page.getByLabel("创业板")).toBeChecked();
    await expect(page.getByLabel("科创板")).not.toBeChecked();
    await expect(page.getByLabel("北京")).not.toBeChecked();
    const structureButton = page.getByRole("button", { name: "查询全市场结构" });
    await expect(structureButton).toBeEnabled();
    await structureButton.click();
    await expect(page.locator("#structure-scan-status")).toContainText("AkShare 后台重建中：已发布 128 /", {
        timeout: 60_000,
    });
    await expect(page.locator("#structure-scan-status")).toContainText("当前筛选市场");
    await expect(page.locator("#structure-scan-status")).toContainText("预计算完成");
    await expect(page.locator("#structure-scan-status")).toContainText("上证、深证、创业板");
    await page.getByLabel("上证").uncheck();
    await page.getByLabel("深证").uncheck();
    await page.getByLabel("创业板").uncheck();
    await page.getByLabel("科创板").check();
    await page.getByLabel("北京").check();
    await page.getByRole("checkbox", { name: "转多信号" }).setChecked(true, { force: true });
    await structureButton.click();
    await expect(page.locator("#structure-scan-status")).toContainText("科创板、北京");
    await expect(page.locator("#structure-scan-status")).toContainText("转多信号");
    await expect(page.locator("#structure-scan-status")).toContainText("已排除名称含 * 的股票");
    expect(signalRequests).toEqual(["GET buy", "GET structure", "GET structure"]);
    expect(structureMarkets).toEqual(["shanghai,shenzhen,chinext", "star,beijing"]);
    expect(structureSignalTypes).toEqual(["bullish_turn", "bullish_turn"]);
    expect(structureSymbol).toBeNull();
    expect(pageErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
});

test("confirmed structure search distinguishes event and availability dates and opens the chart evidence", async ({
    page,
}) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);

    const replayIndex = await page.evaluate(async () => {
        const view = await fetch("/api/tdx-view?symbol=sz.002896&asof=2026-09-07").then((response) => response.json());
        return view.bars.findIndex((bar: { time: string }) => bar.time === "2022-09-01");
    });
    await selectSymbol(page, "sz.002896");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#replay-slider").evaluate((slider, index) => {
        (slider as HTMLInputElement).value = String(index);
        slider.dispatchEvent(new Event("change", { bubbles: true }));
    }, replayIndex);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#asof-label")).toHaveText("2022-09-01");

    const landmark = await page.evaluate(async () => {
        const theory = await fetch("/api/tdx-theory?symbol=sz.002896&asof=2022-09-01").then((response) =>
            response.json(),
        );
        return theory.reversal_trends.bear_bull_alternation_lows.find(
            (point: { time: string }) => point.time === "2022-08-30",
        );
    });
    expect(landmark).toMatchObject({ time: "2022-08-30", available_at: "2022-09-01", value: 29.75 });

    await page.route("**/api/structure-signals?*", async (route) => {
        const query = new URL(route.request().url()).searchParams;
        const params = {
            run: query.get("run"),
            variant: query.get("variant"),
            source: query.get("source"),
            asof: query.get("asof"),
            lookback: Number(query.get("lookback")),
            signal_type: query.get("signal_type"),
            trend_level: Number(query.get("trend_level")),
        };
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                params,
                status: "ready",
                snapshot: {
                    id: "e2e-structure-snapshot",
                    asof: "2022-09-01",
                    computed_at: "2022-09-01T16:10:00+08:00",
                    algorithm_version: "a".repeat(64),
                    data_version: "b".repeat(64),
                },
                total: 1,
                processed: 1,
                failed: 0,
                stale: 0,
                skipped: 0,
                skip_reasons: {},
                results: [
                    {
                        id: landmark.id,
                        symbol: "sz.002896",
                        name: "中大力德",
                        signal_type: "bear_bull_alternation",
                        trend_level: 1,
                        event_date: landmark.time,
                        available_at: landmark.available_at,
                        session_age: 1,
                        label: landmark.label,
                        kind: landmark.kind,
                        value: landmark.value,
                        price_basis: "raw_unadjusted",
                        evidence: landmark,
                    },
                ],
                matched_stocks: 1,
                errors: [],
            }),
        });
    });

    await page.getByRole("button", { name: "结构信号" }).click();
    await page.getByRole("checkbox", { name: "转多信号" }).setChecked(false, { force: true });
    await page.getByRole("checkbox", { name: "空多交替" }).setChecked(true, { force: true });
    await page.getByRole("radio", { name: "Ⅰ 一级" }).setChecked(true, { force: true });
    await page.getByRole("radio", { name: "当天" }).setChecked(true, { force: true });
    await page.getByRole("button", { name: "读取预计算结果" }).click();
    await expect(page.locator("#structure-scan-status")).toContainText("全市场快照已就绪");
    await expect(page.locator("#structure-scan-status")).toContainText("算法 aaaaaaaa");
    const result = page.locator(".structure-signal-item").filter({ hasText: "中大力德" });
    await expect(result).toContainText("发生 2022-08-30 · 确认可用 2022-09-01");
    const favorite = page.locator(".structure-watchlist-add");
    await favorite.click();
    await expect(favorite.locator("svg.tabler-icon-star-filled")).toHaveCount(1);
    await expect(favorite).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#watchlist-stock-list")).toContainText("中大力德");
    await favorite.click();
    await expect(favorite.locator("svg.tabler-icon-star")).toHaveCount(1);
    await expect(favorite).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator("#watchlist-stock-list")).not.toContainText("中大力德");
    await favorite.click();
    const railFavorite = page.locator(".watchlist-stock-remove");
    await expect(railFavorite.locator("svg.tabler-icon-star-filled")).toHaveCount(1);
    await expect(railFavorite).toHaveAttribute("aria-pressed", "true");
    await railFavorite.click();
    await expect(favorite.locator("svg.tabler-icon-star")).toHaveCount(1);
    await expect(page.locator("#watchlist-stock-list")).not.toContainText("中大力德");
    await result.click();

    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", "sz.002896");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-focus-flash-id", /^bear-bull-alternation-low:/);
    await expect(page.locator("#selection-info")).toContainText("空多交替低点");
    await expect(page.locator("#selection-info")).toContainText("54.42% 回档");
    expect(pageErrors).toEqual([]);
});

test("categorized watchlists persist locally and preserve members when a category is deleted", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });

    await expect(page.locator("#watchlist-rail")).toBeVisible();
    await page.getByRole("button", { name: "收起自选股列表" }).click();
    await expect(page.locator("#watchlist-rail-body")).toBeHidden();
    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#watchlist-rail-body")).toBeHidden();
    await page.getByRole("button", { name: "展开自选股列表" }).click();
    await expect(page.getByRole("button", { name: "新建" })).toBeEnabled();
    await page.getByRole("button", { name: "新建" }).click();
    await page.locator("#watchlist-group-name").fill("重点跟踪");
    await page.locator("#watchlist-group-form").getByRole("button", { name: "保存" }).click();
    await expect(page.locator("#watchlist-group-select")).toContainText("重点跟踪（0）");
    await expect(page.locator("#watchlist-group-select")).not.toHaveValue("default");

    const selectedSymbol = await page.locator("#symbol-select").inputValue();
    await expect(page.locator("#watchlist-toggle-current")).toHaveAttribute("aria-pressed", "false");
    await page.locator("#watchlist-toggle-current").click();
    await expect(page.locator("#watchlist-toggle-current")).toHaveAttribute("aria-pressed", "true");
    await page.locator("#watchlist-toggle-current").click();
    await expect(page.locator("#watchlist-toggle-current")).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator("#watchlist-count")).toHaveText("0 只");
    await page.locator("#watchlist-toggle-current").click();
    await expect(page.locator("#watchlist-toggle-current")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#watchlist-count")).toHaveText("1 只");
    await expect(page.locator("#watchlist-stock-list")).toContainText(selectedSymbol);

    await page.locator("#header-stock-search").fill("600015");
    await page.locator("#header-stock-search").press("Enter");
    await expect(page.locator("#symbol-select")).toHaveValue("sh.600015");
    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#watchlist-group-select")).toContainText("重点跟踪（1）");
    await expect(page.locator("#watchlist-stock-list")).toContainText(selectedSymbol);
    await expect(page.locator("#symbol-select")).toHaveValue(selectedSymbol);

    await page.getByRole("button", { name: "重命名" }).click();
    await page.locator("#watchlist-group-name").fill("核心观察");
    await page.locator("#watchlist-group-form").getByRole("button", { name: "保存" }).click();
    await expect(page.locator("#watchlist-group-select")).toContainText("核心观察（1）");

    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "删除", exact: true }).click();
    await expect(page.locator("#watchlist-group-select")).toHaveValue("default");
    await expect(page.locator("#watchlist-stock-list")).toContainText(selectedSymbol);
    await expect(page.getByRole("button", { name: "重命名" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "删除", exact: true })).toBeDisabled();
    expect(pageErrors).toEqual([]);
});

test("watchlist rows show one-line names and codes with borderless icon stars", async ({ page }, testInfo) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });

    const chartStar = page.locator("#watchlist-toggle-current");
    await expect(chartStar.locator("svg")).toHaveCount(1);
    await chartStar.click();
    const row = page.locator("#watchlist-stock-list .watchlist-stock-row");
    await expect(row).toHaveCount(1);
    await expect(row).not.toContainText("点击查看");
    await expect(row.locator(".watchlist-stock-remove svg")).toHaveCount(1);
    await expect(chartStar.locator("svg")).toHaveCount(1);
    await page.locator("#watchlist-rail").screenshot({ path: testInfo.outputPath("watchlist-rail.png") });

    for (const width of [1920, 390]) {
        await page.setViewportSize({ width, height: 900 });
        const layout = await row.evaluate((element) => {
            const name = element.querySelector("strong")!.getBoundingClientRect();
            const code = element.querySelector("small")!.getBoundingClientRect();
            const star = element.querySelector(".watchlist-stock-remove")!;
            return {
                sameLine: Math.abs(name.y + name.height / 2 - (code.y + code.height / 2)) < 6,
                rowBorder: getComputedStyle(element).borderTopWidth,
                starBorder: getComputedStyle(star).borderTopWidth,
            };
        });
        expect(layout).toEqual({ sameLine: true, rowBorder: "0px", starBorder: "0px" });
    }

    await row.locator(".watchlist-stock-remove").click();
    await expect(row).toHaveCount(0);
    await expect(chartStar).toHaveAttribute("aria-pressed", "false");
    expect(pageErrors).toEqual([]);
});

test("switching stocks shows a spinner until the new chart data is ready", async ({ page }) => {
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });

    await page.route("**/api/market-timeframe?**", async (route) => {
        if (route.request().url().includes("symbol=sh.600015")) {
            await new Promise((resolve) => setTimeout(resolve, 500));
        }
        await route.continue();
    });

    const overlay = page.locator("#chart-loading-overlay");
    await selectSymbol(page, "sh.600015");
    await expect(overlay).toBeVisible();
    await expect(overlay).toContainText("正在加载股票数据…");
    await expect(overlay).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", "sh.600015");
});

test("Huaxia Bank level-two last-fall-high reanchors after the old low close break", async ({ page }) => {
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    const huaxiaTheory = page.waitForResponse(
        (response) =>
            response.url().includes("/api/market-timeframe?") &&
            response.url().includes("symbol=sh.600015") &&
            response.ok(),
        { timeout: 60_000 },
    );
    await selectSymbol(page, "sh.600015");
    await huaxiaTheory;
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

test("Minsheng Bank level-one guide reaches the confirmed same-level breakout bar", async ({ page }) => {
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    const minshengTheory = page.waitForResponse(
        (response) =>
            response.url().includes("/api/market-timeframe?") &&
            response.url().includes("symbol=sh.600016") &&
            response.ok(),
        { timeout: 60_000 },
    );
    await selectSymbol(page, "sh.600016");
    await minshengTheory;
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#trend-summary")).toContainText("视窗末跌高 3.65");

    const state = await page.evaluate(async () => {
        const [theory, view, annotations] = await Promise.all([
            fetch("/api/tdx-theory?symbol=sh.600016&asof=2026-09-07").then((response) => response.json()),
            fetch("/api/tdx-view?symbol=sh.600016&asof=2026-09-07").then((response) => response.json()),
            new Function("return import('/annotations.js')")(),
        ]);
        const stroke = theory.reversal_trends.strokes.find((candidate: { points: { time: string }[] }) =>
            candidate.points.some((point) => point.time === "2026-06-15"),
        );
        const summary = annotations.reversalWindowSummary(
            stroke ? [stroke] : [],
            "2026-06-01",
            "2026-09-07",
            view.bars,
        );
        const breakoutBar = view.bars.find((bar: { time: string }) => bar.time === "2026-08-03");
        return { key: summary.lastFallHigh, low: summary.low, breakout: summary.lastFallHighBreakout, breakoutBar };
    });
    expect(state.key).toMatchObject({ time: "2026-06-15", value: 3.65 });
    expect(state.low).toMatchObject({ time: "2026-06-30", value: 3.2 });
    expect(state.breakout).toMatchObject({
        time: "2026-08-03",
        value: 3.67,
        available_at: "2026-08-11",
        breakout_basis: "confirmed_same_level_high",
    });
    expect(state.breakoutBar.close).toBe(3.65);
    expect(pageErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
});

test("Shanghai Electric Power shows the complete level-three development path after its key break", async ({
    page,
}) => {
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    const shanghaiPowerTheory = page.waitForResponse(
        (response) =>
            response.url().includes("/api/market-timeframe?") &&
            response.url().includes("symbol=sh.600021") &&
            response.ok(),
        { timeout: 60_000 },
    );
    await selectSymbol(page, "sh.600021");
    await shanghaiPowerTheory;
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-tertiary-developing-points", "14");
    await expect(page.locator("#secondary-trend-summary")).toContainText("二级末跌高 22.35");
    await expect(page.locator("#tertiary-trend-summary")).toContainText("完整发展路径 14 点");
    await expect(page.locator("#tertiary-trend-summary")).toContainText("2026-05-29 H28 22.35");

    const latestState = await page.evaluate(async () => {
        const theory = await fetch("/api/tdx-theory?symbol=sh.600021&asof=2026-09-07").then((response) =>
            response.json(),
        );
        return {
            confirmedCount: theory.tertiary_trends.confirmed_wave_count,
            developingPointCount: theory.tertiary_trends.developing_point_count,
            path: theory.tertiary_trends.developing_strokes[0].points,
            transition: theory.secondary_trends.strokes[0].key_transitions.find(
                (event: { broken_low: { time: string } }) => event.broken_low.time === "2026-04-28",
            ),
        };
    });
    expect(latestState.confirmedCount).toBe(3);
    expect(latestState.developingPointCount).toBe(14);
    expect(latestState.path.slice(-4)).toMatchObject([
        { time: "2025-12-16", kind: "L", value: 18.96, development_role: "pending_evidence" },
        { time: "2026-01-26", kind: "H", value: 24.85, development_role: "pending_evidence" },
        { time: "2026-04-28", kind: "L", value: 16.73, development_role: "pending_evidence" },
        { time: "2026-05-29", kind: "H", value: 22.35, development_role: "active_endpoint" },
    ]);
    expect(latestState.transition).toMatchObject({
        available_at: "2026-07-17",
        active_low_source_level: 1,
        broken_low: { time: "2026-04-28", kind: "L", value: 16.73 },
        new_key: { time: "2026-05-29", kind: "H", value: 22.35 },
        active_low: { time: "2026-07-14", kind: "L", value: 13.26 },
        confirmed_by: { time: "2026-06-23", value: 16.29 },
    });

    const replayIndex = await page.evaluate(async () => {
        const view = await fetch("/api/tdx-view?symbol=sh.600021&asof=2026-09-07").then((response) => response.json());
        return view.bars.findIndex((bar: { time: string }) => bar.time === "2015-07-15");
    });
    expect(replayIndex).toBeGreaterThan(0);
    await page.locator("#replay-slider").evaluate((slider, index) => {
        (slider as HTMLInputElement).value = String(index);
        slider.dispatchEvent(new Event("change", { bubbles: true }));
    }, replayIndex);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#asof-label")).toHaveText("2015-07-15");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-tertiary-developing-points", "2");
    await expect(page.locator("#tertiary-trend-summary")).toContainText("完整发展路径 2 点");
    await expect(page.locator("#tertiary-trend-summary")).toContainText("当前上涨候选 2015-06-02 H13 34.5");

    const state = await page.evaluate(async () => {
        const theory = await fetch("/api/tdx-theory?symbol=sh.600021&asof=2015-07-15").then((response) =>
            response.json(),
        );
        return {
            confirmedCount: theory.tertiary_trends.confirmed_wave_count,
            tail: theory.tertiary_trends.developing_strokes[0],
        };
    });
    expect(state.confirmedCount).toBe(3);
    expect(state.tail).toMatchObject({
        kind: "tertiary-developing",
        wave_direction: "up",
        display_only: true,
        points: [
            { time: "2008-11-06", kind: "L", value: 2.68 },
            { time: "2015-06-02", kind: "H", value: 34.5, state: "developing" },
        ],
    });
    expect(pageErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
});

test("Guofang Group cancels bull-flip highs only after their preceding lows are broken", async ({ page }) => {
    test.setTimeout(90_000);
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    await selectSymbol(page, "sh.601086");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#selection-info")).toContainText("601086 国芳集团");

    const state = await page.evaluate(async () => {
        type TBullFlip = { time: string; confirmed_low?: { value: number } };
        type TFormalPoint = { time: string; flip: string; confirmed_by: { value: number } };
        type TTheory = {
            secondary_trends: {
                bear_to_bull_highs: TBullFlip[];
                strokes: Array<{ points: TFormalPoint[] }>;
            };
        };
        const fetchTheory = (asof: string) =>
            fetch(`/api/tdx-theory?symbol=sh.601086&asof=${asof}`).then(
                async (response) => (await response.json()) as TTheory,
            );
        const [beforeFirstBreak, atFirstBreak, beforeSecondBreak, atSecondBreak, latest] = await Promise.all([
            fetchTheory("2026-05-06"),
            fetchTheory("2026-05-07"),
            fetchTheory("2026-07-14"),
            fetchTheory("2026-07-15"),
            fetchTheory("2026-09-14"),
        ]);
        const findFlip = (theory: TTheory, time: string) =>
            theory.secondary_trends.bear_to_bull_highs.find((point) => point.time === time);
        const formal = latest.secondary_trends.strokes.flatMap((stroke) => stroke.points);
        return {
            firstBefore: findFlip(beforeFirstBreak, "2026-04-21"),
            firstAt: findFlip(atFirstBreak, "2026-04-21"),
            secondBefore: findFlip(beforeSecondBreak, "2026-05-14"),
            secondAt: findFlip(atSecondBreak, "2026-05-14"),
            latestFirst: findFlip(latest, "2026-04-21"),
            latestSecond: findFlip(latest, "2026-05-14"),
            converted: formal
                .filter((point) => ["2026-04-21", "2026-05-14"].includes(point.time))
                .map((point) => ({
                    time: point.time,
                    flip: point.flip,
                    confirmingLow: point.confirmed_by.value,
                })),
        };
    });

    expect(state.firstBefore).toMatchObject({ time: "2026-04-21", confirmed_low: { value: 8.15 } });
    expect(state.firstAt).toBeUndefined();
    expect(state.secondBefore).toMatchObject({ time: "2026-05-14", confirmed_low: { value: 8.03 } });
    expect(state.secondAt).toBeUndefined();
    expect(state.latestFirst).toBeUndefined();
    expect(state.latestSecond).toBeUndefined();
    expect(state.converted).toEqual([
        { time: "2026-04-21", flip: "翻多为空", confirmingLow: 8.03 },
        { time: "2026-05-14", flip: "翻多为空", confirmingLow: 6.38 },
    ]);
    expect(pageErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
});

test("Zhongda Leader promotes the August 2022 high only after causal alternation evidence", async ({ page }) => {
    test.setTimeout(90_000);
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));

    const layersPopover = page.locator("#chart-layers-popover");
    const openChartLayers = async () => {
        if (!(await layersPopover.isVisible())) {
            await page.locator("#chart-layers-trigger").click();
        }
    };

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    const symbolSelect = page.locator("#symbol-select");
    if ((await symbolSelect.inputValue()) !== "sz.002896") {
        await selectSymbol(page, "sz.002896");
    }
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#selection-info")).toContainText("002896 中大力德");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-secondary-developing-points", "2");
    await expect(page.locator("#secondary-trend-summary")).toContainText("发展路径");
    await expect(page.locator("#secondary-trend-summary")).toContainText("2026-07-06 H23 88.60");
    await expect(page.locator("#secondary-trend-summary")).toContainText("2026-07-30 L128 58.51");

    const flipReplayIndex = await page.evaluate(async () => {
        const view = await fetch("/api/tdx-view?symbol=sz.002896&asof=2026-09-07").then((response) => response.json());
        return view.bars.findIndex((bar: { time: string }) => bar.time === "2022-08-09");
    });
    expect(flipReplayIndex).toBeGreaterThan(0);
    await page.locator("#replay-slider").evaluate((slider, index) => {
        (slider as HTMLInputElement).value = String(index);
        slider.dispatchEvent(new Event("change", { bubbles: true }));
    }, flipReplayIndex);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.locator("#asof-label")).toHaveText("2022-08-09");
    await expect.poll(() => page.locator("#price-chart").getAttribute("data-bear-to-bull-high-count")).not.toBe("0");
    const levelTwoFlipHigh = page.locator('[data-annotation-id^="bear-to-bull-high:2:"]').filter({
        hasText: "Ⅱ 空翻多高点 · H70 49.56",
    });
    await expect(levelTwoFlipHigh).toBeVisible();
    await levelTwoFlipHigh.click();
    await expect(page.locator("#selection-info")).toContainText("L9（2022-05-27，13.16）");
    await expect(page.locator("#selection-info")).toContainText("H69（2022-05-24，18.28）");
    await openChartLayers();
    await layersPopover.locator("#show-bear-to-bull-highs").setChecked(false, { force: true });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bear-to-bull-high-count", "0");
    await openChartLayers();
    await layersPopover.locator("#show-bear-to-bull-highs").setChecked(true, { force: true });
    await expect.poll(() => page.locator("#price-chart").getAttribute("data-bear-to-bull-high-count")).not.toBe("0");

    const alternationReplayIndex = await page.evaluate(async () => {
        const view = await fetch("/api/tdx-view?symbol=sz.002896&asof=2026-09-07").then((response) => response.json());
        return view.bars.findIndex((bar: { time: string }) => bar.time === "2022-09-01");
    });
    await page.locator("#replay-slider").evaluate((slider, index) => {
        (slider as HTMLInputElement).value = String(index);
        slider.dispatchEvent(new Event("change", { bubbles: true }));
    }, alternationReplayIndex);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#asof-label")).toHaveText("2022-09-01");
    await expect
        .poll(() => page.locator("#price-chart").getAttribute("data-bear-bull-alternation-low-count"))
        .not.toBe("0");
    const levelOneAlternationLow = page.locator('[data-annotation-id^="bear-bull-alternation-low:1:"]').filter({
        hasText: "Ⅰ 空多交替低点 · L71 29.75",
    });
    await expect(levelOneAlternationLow).toBeVisible();
    await levelOneAlternationLow.click();
    await expect(page.locator("#selection-info")).toContainText("H70（2022-08-03，49.56）");
    await expect(page.locator("#selection-info")).toContainText("54.42% 回档");
    await openChartLayers();
    await layersPopover.locator("#show-bear-bull-alternation-lows").setChecked(false, { force: true });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bear-bull-alternation-low-count", "0");
    await openChartLayers();
    await layersPopover.locator("#show-bear-bull-alternation-lows").setChecked(true, { force: true });
    await expect
        .poll(() => page.locator("#price-chart").getAttribute("data-bear-bull-alternation-low-count"))
        .not.toBe("0");

    const bullLegHighReplayIndex = await page.evaluate(async () => {
        const view = await fetch("/api/tdx-view?symbol=sz.002896&asof=2026-09-07").then((response) => response.json());
        return view.bars.findIndex((bar: { time: string }) => bar.time === "2022-09-08");
    });
    await page.locator("#replay-slider").evaluate((slider, index) => {
        (slider as HTMLInputElement).value = String(index);
        slider.dispatchEvent(new Event("change", { bubbles: true }));
    }, bullLegHighReplayIndex);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#asof-label")).toHaveText("2022-09-08");
    await expect
        .poll(() => page.locator("#price-chart").getAttribute("data-post-alternation-bull-high-count"))
        .not.toBe("0");
    const levelOneBullLegHigh = page.locator('[data-annotation-id^="post-alternation-bull-high:1:"]').filter({
        hasText: "Ⅰ 交替后多头段高点 · H71 33.89",
    });
    await expect(levelOneBullLegHigh).toBeVisible();
    await levelOneBullLegHigh.click();
    await expect(page.locator("#selection-info")).toContainText("L71（2022-08-30，29.75）");
    await expect(page.locator("#selection-info")).toContainText("第一个 L→H 高点");
    await openChartLayers();
    await layersPopover.locator("#show-post-alternation-bull-highs").setChecked(false, { force: true });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-post-alternation-bull-high-count", "0");
    await openChartLayers();
    await layersPopover.locator("#show-post-alternation-bull-highs").setChecked(true, { force: true });
    await expect
        .poll(() => page.locator("#price-chart").getAttribute("data-post-alternation-bull-high-count"))
        .not.toBe("0");

    const bullishTurnReplayIndex = await page.evaluate(async () => {
        const view = await fetch("/api/tdx-view?symbol=sz.002896&asof=2026-09-07").then((response) => response.json());
        return view.bars.findIndex((bar: { time: string }) => bar.time === "2025-01-24");
    });
    await page.locator("#replay-slider").evaluate((slider, index) => {
        (slider as HTMLInputElement).value = String(index);
        slider.dispatchEvent(new Event("change", { bubbles: true }));
    }, bullishTurnReplayIndex);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#asof-label")).toHaveText("2025-01-24");
    await expect.poll(() => page.locator("#price-chart").getAttribute("data-bullish-turn-signal-count")).not.toBe("0");
    await expect.poll(() => page.locator("#price-chart").getAttribute("data-bullish-turn-guides")).not.toBe("0");
    const levelOneBullishTurn = page.locator('[data-annotation-id^="bullish-turn-signal:1:"]').filter({
        hasText: "Ⅰ 转多信号",
    });
    await expect(levelOneBullishTurn).toBeVisible();
    await levelOneBullishTurn.click();
    await expect(page.locator("#selection-info")).toContainText("首次从下向上严格突破此前空翻多高点");
    await openChartLayers();
    await layersPopover.locator("#show-bullish-turn-signals").setChecked(false, { force: true });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bullish-turn-signal-count", "0");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bullish-turn-guides", "0");
    await openChartLayers();
    await layersPopover.locator("#show-bullish-turn-signals").setChecked(true, { force: true });
    await expect.poll(() => page.locator("#price-chart").getAttribute("data-bullish-turn-signal-count")).not.toBe("0");
    await expect.poll(() => page.locator("#price-chart").getAttribute("data-bullish-turn-guides")).not.toBe("0");

    const bullishTurnCausality = await page.evaluate(async () => {
        const [before, at] = await Promise.all([
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2025-01-23").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2025-01-24").then((response) => response.json()),
        ]);
        return {
            before: before.reversal_trends.bullish_turn_signals.find(
                (signal: { time: string }) => signal.time === "2025-01-24",
            ),
            at: at.reversal_trends.bullish_turn_signals.find(
                (signal: { time: string }) => signal.time === "2025-01-24",
            ),
        };
    });
    expect(bullishTurnCausality.before).toBeUndefined();
    expect(bullishTurnCausality.at).toMatchObject({
        time: "2025-01-24",
        kind: "K",
        value: 54.21,
        previous_close: 49.28,
        breakout_level: 49.56,
        confirmed_alternation_low: { time: "2022-08-30", value: 29.75 },
        confirmed_flip_high: { time: "2022-08-03", value: 49.56 },
    });

    const state = await page.evaluate(async () => {
        const [
            theory,
            beforeFlip,
            atFlip,
            beforeAlternation,
            atAlternation,
            beforeBullLegHigh,
            atBullLegHigh,
            beforePromotion,
            atPromotion,
        ] = await Promise.all([
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2026-09-07").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2022-08-08").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2022-08-09").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2022-08-31").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2022-09-01").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2022-09-07").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2022-09-08").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2023-02-03").then((response) => response.json()),
            fetch("/api/tdx-theory?symbol=sz.002896&asof=2023-02-06").then((response) => response.json()),
        ]);
        const formal = theory.secondary_trends.strokes[0].points;
        const developing = theory.secondary_trends.developing_strokes[0];
        const promoted = formal.find((point: { time: string }) => point.time === "2022-08-03");
        const landmarkLevels = [
            theory.reversal_trends.bear_to_bull_highs,
            theory.secondary_trends.bear_to_bull_highs,
            theory.tertiary_trends.bear_to_bull_highs,
        ];
        return {
            formalCount: theory.secondary_trends.confirmed_wave_count,
            formalEnd: formal.at(-1),
            developingCount: theory.secondary_trends.developing_point_count,
            developingStart: developing.points[0],
            developingEnd: developing.points.at(-1),
            developing,
            promoted,
            flipHigh: theory.secondary_trends.bear_to_bull_highs.find(
                (point: { time: string }) => point.time === "2022-08-03",
            ),
            beforeFlipHigh: beforeFlip.secondary_trends.bear_to_bull_highs.find(
                (point: { time: string }) => point.time === "2022-08-03",
            ),
            atFlipHigh: atFlip.secondary_trends.bear_to_bull_highs.find(
                (point: { time: string }) => point.time === "2022-08-03",
            ),
            beforeAlternationLow: beforeAlternation.reversal_trends.bear_bull_alternation_lows.find(
                (point: { time: string }) => point.time === "2022-08-30",
            ),
            atAlternationLow: atAlternation.reversal_trends.bear_bull_alternation_lows.find(
                (point: { time: string }) => point.time === "2022-08-30",
            ),
            beforeBullLegHigh: beforeBullLegHigh.reversal_trends.post_alternation_bull_highs.find(
                (point: { time: string }) => point.time === "2022-09-01",
            ),
            atBullLegHigh: atBullLegHigh.reversal_trends.post_alternation_bull_highs.find(
                (point: { time: string }) => point.time === "2022-09-01",
            ),
            beforePromotionCount: beforePromotion.secondary_trends.confirmed_wave_count,
            atPromotionCount: atPromotion.secondary_trends.confirmed_wave_count,
            atPromotionEnd: atPromotion.secondary_trends.strokes[0].points.at(-1),
            landmarkCounts: landmarkLevels.map((landmarks) => landmarks.length),
            allLandmarksStrictlyBreakTheirKey: landmarkLevels
                .flat()
                .every(
                    (landmark) =>
                        landmark.kind === "H" &&
                        landmark.broken_key?.kind === "H" &&
                        landmark.value > landmark.broken_key.value,
                ),
        };
    });
    expect(state.formalCount).toBe(46);
    expect(state.formalEnd).toMatchObject({ time: "2026-07-06", kind: "H", value: 88.6 });
    expect(state.developingCount).toBe(2);
    expect(state.developingStart).toMatchObject({ time: "2026-07-06", kind: "H", value: 88.6 });
    expect(state.developingEnd).toMatchObject({ time: "2026-07-30", kind: "L", value: 58.51 });
    expect(state.beforePromotionCount).toBe(17);
    expect(state.atPromotionCount).toBe(18);
    expect(state.atPromotionEnd).toMatchObject({ time: "2022-08-03", kind: "H", value: 49.56 });
    expect(state.landmarkCounts).toEqual([1, 6, 1]);
    expect(state.allLandmarksStrictlyBreakTheirKey).toBe(true);
    expect(state.promoted).toMatchObject({
        time: "2022-08-03",
        kind: "H",
        value: 49.56,
        available_at: "2023-02-06",
        confirmation_rule: "level1_old_level2_key_break_alternation_and_nested_reversal",
        broken_key: { time: "2021-12-01", value: 27.71 },
        alternation: {
            point: { time: "2022-08-30", value: 29.75 },
        },
        provisional_reversal: { time: "2022-12-23", value: 22.06 },
    });
    expect(state.promoted.alternation.retracement_ratio).toBeCloseTo(0.5442307692, 8);
    expect(state.beforeFlipHigh).toBeUndefined();
    expect(state.atFlipHigh).toMatchObject({
        time: "2022-08-03",
        kind: "H",
        value: 49.56,
        available_at: "2022-08-09",
        confirmed_low: { time: "2022-05-27", kind: "L", value: 13.16 },
        broken_key: { time: "2022-05-24", kind: "H", value: 18.28 },
    });
    expect(state.beforeAlternationLow).toBeUndefined();
    expect(state.atAlternationLow).toMatchObject({
        time: "2022-08-30",
        kind: "L",
        value: 29.75,
        available_at: "2022-09-01",
        confirmed_flip_high: { time: "2022-08-03", value: 49.56 },
        confirmed_bear_low: { time: "2022-05-27", value: 13.16 },
        broken_key: { time: "2022-05-24", value: 18.28 },
    });
    expect(state.atAlternationLow.retracement_ratio).toBeCloseTo(0.5442307692, 8);
    expect(state.beforeBullLegHigh).toBeUndefined();
    expect(state.atBullLegHigh).toMatchObject({
        time: "2022-09-01",
        kind: "H",
        value: 33.89,
        available_at: "2022-09-08",
        confirmed_alternation_low: { time: "2022-08-30", value: 29.75 },
        confirmed_flip_high: { time: "2022-08-03", value: 49.56 },
        broken_key: { time: "2022-05-24", value: 18.28 },
    });
    expect(state.flipHigh).toMatchObject({
        time: state.atFlipHigh.time,
        kind: state.atFlipHigh.kind,
        value: state.atFlipHigh.value,
        available_at: state.atFlipHigh.available_at,
        confirmed_low: state.atFlipHigh.confirmed_low,
        broken_key: state.atFlipHigh.broken_key,
    });
    expect(state.developing).toMatchObject({
        kind: "secondary-developing",
        source_level: 1,
        trend_level: 2,
        display_only: true,
    });
    expect(pageErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
});

test("the shared dashboard shell works on desktop and mobile", async ({ page }) => {
    test.setTimeout(90_000);
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
    await expect(page.getByRole("searchbox", { name: "搜索股票" })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看通知" })).toBeVisible();
    await expect(page.getByRole("button", { name: "界面设置" })).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新当前视图" })).toBeVisible();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#error")).toBeHidden();
    await expect(page.getByRole("button", { name: "运行当前股票回测" })).toBeVisible();
    await page.getByRole("searchbox", { name: "搜索股票" }).click();
    await expect(page.locator("#header-stock-search")).toBeFocused();
    await expect(page.locator("#stock-picker-panel")).toBeVisible();
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
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.getByRole("button", { name: "打开侧栏" }).click();
    await expect(page.locator("#wavequant-mobile-sidebar")).toBeVisible();
    await page.getByRole("button", { name: "系统与设置" }).click();
    await expect(page.locator("#page-title")).toHaveText("系统状态");
    await expect(page).toHaveURL(/page=health/);
    await expect(page.locator("#wavequant-mobile-sidebar")).toHaveCount(0);
    expect(pageErrors).toEqual([]);
});

test("Ctrl+K searches the real research stock universe", async ({ page }) => {
    test.setTimeout(90_000);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#buy-points-tab").click();
    await expect(page.locator("#stock-picker-panel")).toBeHidden();

    await page.keyboard.press("Control+K");
    const search = page.locator("#header-stock-search");
    await expect(search).toBeFocused();
    await expect(page.locator("#stock-picker-panel")).toBeVisible();
    await expect(page.getByRole("dialog", { name: "搜索股票或题材" })).toHaveCount(0);

    await search.fill("600519");
    await expect(page.locator("#stock-list [data-symbol='sh.600519']")).toContainText("贵州茅台");
    await page.keyboard.press("Enter");
    await expect(page.locator("#symbol-select")).toHaveValue("sh.600519");

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(search).toBeVisible();
    expect(await page.locator("body").evaluate((body) => body.scrollWidth)).toBeLessThanOrEqual(390);
    expect(pageErrors).toEqual([]);
});

test("market overview and limit-up ladder remain usable across desktop and narrow viewports", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/market");
    await expect(page.locator("[data-shell-structure='market']")).toHaveAttribute("data-shell-variant", "market");
    await expect(page.getByRole("heading", { name: "市场看盘" })).toBeVisible();
    await expect(page.locator("#market-main .market-view-tab[aria-selected='true']")).toHaveText("市场总览");
    await expect(page.locator("#market-main .market-view-bar")).toHaveCSS("background-color", "rgb(17, 29, 45)");
    await expect(page.getByText("大盘与市场广度")).toBeVisible();
    await expect(page.getByText("1,060.36")).toBeVisible();
    await expect(page.locator("canvas")).toHaveCount(1);

    await page.getByRole("tab", { name: "涨停阶梯" }).click();
    await expect(page.getByRole("heading", { name: "每日涨停天梯" })).toBeVisible();
    await expect(page.getByLabel("涨停天梯日期")).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("tab", { name: "市场总览" }).click();
    await expect(page.getByRole("button", { name: "保存快照" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
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
