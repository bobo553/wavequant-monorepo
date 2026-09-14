import { expect, test } from "@playwright/test";

async function selectTdx(page: import("@playwright/test").Page): Promise<void> {
    await page.locator("#result-scope").selectOption("tdx");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
}

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

test("market candle timeframe switches server data, replay sessions and theory together", async ({ page }) => {
    const pageErrors: string[] = [];
    const requests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("request", (request) => {
        const url = new URL(request.url());
        if (["/api/tdx-view", "/api/tdx-theory"].includes(url.pathname) && url.searchParams.get("timeframe") === "1w") {
            requests.push(url.pathname);
        }
    });

    await page.goto("/research");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    await expect(page.getByLabel("K线周期")).toBeEnabled();
    await expect(page.getByLabel("K线周期").locator("option")).toHaveText(["日线", "周线", "月线", "季线", "年线"]);

    await page.getByLabel("K线周期").selectOption("1w");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#timeframe-tag")).toHaveText("周 K");
    await expect(page.locator("#selected-stock-summary")).toContainText("周线截面");
    await expect.poll(() => requests).toContain("/api/tdx-view");
    await expect.poll(() => requests).toContain("/api/tdx-theory");

    const [weekly, daily] = await page.evaluate(async () => {
        const [weeklyResponse, dailyResponse] = await Promise.all([
            fetch("/api/tdx-view?symbol=sh.600519&asof=2026-09-07&timeframe=1w"),
            fetch("/api/tdx-view?symbol=sh.600519&asof=2026-09-07&timeframe=1d"),
        ]);
        return Promise.all([weeklyResponse.json(), dailyResponse.json()]);
    });
    expect(weekly.timeframe).toBe("1w");
    expect(weekly.timeframe_label).toBe("周线");
    expect(weekly.bars.length).toBeLessThan(daily.bars.length);
    expect(
        await page.evaluate(() => JSON.parse(localStorage.getItem("wavequant.research.chart.v1") || "null").timeframe),
    ).toBe("1w");

    await page.reload();
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.getByLabel("K线周期")).toHaveValue("1w");
    await expect(page.locator("#timeframe-tag")).toHaveText("周 K");

    await page.locator("#result-scope").selectOption("stock");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.getByLabel("K线周期")).toBeDisabled();
    await expect(page.getByLabel("K线周期")).toHaveValue("1d");
    expect(pageErrors).toEqual([]);
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
    const cached = await page.evaluate(
        () =>
            new Promise<Array<{ source: string; etag: string; count: number }>>((resolve, reject) => {
                const request = indexedDB.open("wavequant-market-data", 1);
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
    await page.getByLabel("结构信号类型").selectOption("bullish_turn");
    await structureButton.click();
    await expect(page.locator("#structure-scan-status")).toContainText("科创板、北京");
    await expect(page.locator("#structure-scan-status")).toContainText("转多信号");
    await expect(page.locator("#structure-scan-status")).toContainText("已排除名称含 * 的股票");
    expect(signalRequests).toEqual(["GET buy", "GET structure", "GET structure"]);
    expect(structureMarkets).toEqual(["shanghai,shenzhen,chinext", "star,beijing"]);
    expect(structureSignalTypes).toEqual(["any", "bullish_turn"]);
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
    await page.locator("#symbol-select").selectOption("sz.002896");
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
    await page.getByLabel("结构信号类型").selectOption("bear_bull_alternation");
    await page.getByLabel("结构趋势级别").selectOption("1");
    await page.getByLabel("结构确认窗口").selectOption("1");
    await page.getByRole("button", { name: "读取预计算结果" }).click();
    await expect(page.locator("#structure-scan-status")).toContainText("全市场快照已就绪");
    await expect(page.locator("#structure-scan-status")).toContainText("算法 aaaaaaaa");
    const result = page.locator(".structure-signal-item").filter({ hasText: "中大力德" });
    await expect(result).toContainText("发生 2022-08-30 · 确认可用 2022-09-01");
    await result.click();

    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#price-chart")).toHaveAttribute("data-symbol", "sz.002896");
    await expect(page.locator("#selection-info")).toContainText("空多交替低点");
    await expect(page.locator("#selection-info")).toContainText("54.42% 回档");
    expect(pageErrors).toEqual([]);
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
        (response) => response.url().includes("/api/tdx-theory?symbol=sh.600015") && response.ok(),
        { timeout: 60_000 },
    );
    await page.locator("#symbol-select").selectOption("sh.600015");
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
        (response) => response.url().includes("/api/tdx-theory?symbol=sh.600016") && response.ok(),
        { timeout: 60_000 },
    );
    await page.locator("#symbol-select").selectOption("sh.600016");
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
        (response) => response.url().includes("/api/tdx-theory?symbol=sh.600021") && response.ok(),
        { timeout: 60_000 },
    );
    await page.locator("#symbol-select").selectOption("sh.600021");
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

test("Zhongda Leader promotes the August 2022 high only after causal alternation evidence", async ({ page }) => {
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}`));

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await selectTdx(page);
    const symbolSelect = page.locator("#symbol-select");
    if ((await symbolSelect.inputValue()) !== "sz.002896") {
        await symbolSelect.selectOption("sz.002896");
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
    await page.getByRole("checkbox", { name: "各级空翻多高点", exact: true }).uncheck();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bear-to-bull-high-count", "0");
    await page.getByRole("checkbox", { name: "各级空翻多高点", exact: true }).check();
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
    await page.getByRole("checkbox", { name: "各级空多交替低点", exact: true }).uncheck();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bear-bull-alternation-low-count", "0");
    await page.getByRole("checkbox", { name: "各级空多交替低点", exact: true }).check();
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
    await page.getByRole("checkbox", { name: "各级交替后多头段高点", exact: true }).uncheck();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-post-alternation-bull-high-count", "0");
    await page.getByRole("checkbox", { name: "各级交替后多头段高点", exact: true }).check();
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
    await page.getByRole("checkbox", { name: "各级转多信号", exact: true }).uncheck();
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bullish-turn-signal-count", "0");
    await expect(page.locator("#price-chart")).toHaveAttribute("data-bullish-turn-guides", "0");
    await page.getByRole("checkbox", { name: "各级转多信号", exact: true }).check();
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
    expect(state.landmarkCounts).toEqual([6, 23, 2]);
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
