/* Real browser acceptance against the running, read-only dashboard. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.DASHBOARD_URL || "http://127.0.0.1:8765";
const output = path.resolve(
    __dirname,
    "../results/visualization_v1",
    new Date().toISOString().replaceAll(/[:.]/g, "-"),
);
fs.mkdirSync(output, { recursive: true });
const results = [];
(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
    });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
    await context.tracing.start({ screenshots: true, snapshots: true });
    const page = await context.newPage();
    const errors = [],
        external = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
        if (!r.url().startsWith(base) && !r.url().startsWith("blob:")) external.push(r.url());
    });
    async function loaded() {
        await page.waitForFunction(
            () =>
                document.querySelector("#loading").hidden &&
                document.querySelector("#error").hidden &&
                !!document.querySelector("#price-chart").dataset.symbol,
            {},
            { timeout: 60000 },
        );
    }
    async function change(name, value) {
        const response = page.waitForResponse(
            (r) =>
                (r.url().includes("/api/view?") ||
                    r.url().includes("/api/stock-view?") ||
                    r.url().includes("/api/tdx-view?")) &&
                r.status() === 200,
        );
        await page.getByRole("combobox", { name, exact: true }).selectOption(value);
        const data = await (await response).json();
        await loaded();
        return data;
    }
    async function test(name, fn) {
        await fn();
        results.push({ name, status: "PASS" });
        console.log("PASS " + name);
    }
    try {
        await page.goto(base + "/research");
        await loaded();
        const catalog = await (await context.request.get(base + "/api/catalog")).json();
        const run = catalog.runs[0];
        await test("TDX full A-share catalog, right vertical list and honest raw-data scope", async () => {
            const tdx = await (await context.request.get(base + "/api/tdx-catalog")).json();
            assert.ok(tdx.with_daily > 5000);
            assert.ok(tdx.stocks.length > run.symbols.length);
            assert.equal(tdx.stocks.find((s) => s.symbol === "sz.300750").name, "宁德时代");
            assert.equal(await page.getByRole("combobox", { name: "结果口径", exact: true }).inputValue(), "tdx");
            assert.ok(await page.locator(".metric-grid").isHidden());
            assert.ok((await page.locator("#stock-list .stock-item").count()) <= 101);
            const right = await page.locator(".stock-browser").boundingBox(),
                left = await page.locator(".chart-card").boundingBox();
            assert.ok(right.x >= left.x + left.width);
            const view = await change("股票", "sz.300750");
            assert.equal(view.price_basis, "raw_unadjusted");
            assert.equal(view.metrics, null);
            assert.deepEqual(view.markers, []);
            assert.ok((await page.locator("#selected-stock-summary").textContent()).includes("宁德时代"));
            const response = page.waitForResponse(
                (r) => r.url().includes("/api/tdx-view?") && r.url().includes("sh.688001") && r.ok(),
            );
            await page.getByRole("searchbox", { name: "搜索股票", exact: true }).fill("华兴源创");
            await page.getByRole("searchbox", { name: "搜索股票", exact: true }).press("Enter");
            await response;
            await loaded();
            assert.equal(await page.locator("#price-chart").getAttribute("data-symbol"), "sh.688001");
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.lectureVertices) > 0,
            );
            await page.locator(".workspace-grid").screenshot({ path: path.join(output, "tdx-right-stock-list.png") });
            await page.getByRole("searchbox", { name: "搜索股票", exact: true }).fill("");
            await change("结果口径", "stock");
        });
        await test("whole-wave V3 default and preserved V2/V1 sealed-portfolio separation", async () => {
            assert.equal(await page.locator("#variant-select").inputValue(), "lecture_v3");
            await change("策略版本", "lecture_v2");
            assert.equal(await page.locator("#variant-select").inputValue(), "lecture_v2");
            await page.waitForFunction(() =>
                document.querySelector("#backtest-details").textContent.includes("分级双买点 V2"),
            );
            await page.waitForFunction(
                () => document.querySelector("#backtest-summary-status").textContent.includes("只完成"),
                {},
                { timeout: 180000 },
            );
            const v2q = new URLSearchParams({
                run: run.id,
                variant: "lecture_v2",
                symbol: "sh.600519",
                asof: run.end,
                scenario: "base",
            });
            const v2 = await (await context.request.get(base + "/api/stock-view?" + v2q)).json();
            assert.equal(v2.backtest.strategy.entry_policy, "hierarchical_two_buy_points");
            assert.equal(v2.backtest.strategy.mature_shallow_ratio, 1 / 3);
            assert.deepEqual(v2.strategy_profile.definition.trend_levels, [1, 2, 3]);
            assert.equal((await context.request.get(base + "/api/view?" + v2q)).status(), 400);
            await change("策略版本", "lecture_v1");
            await page.waitForFunction(() =>
                document.querySelector("#backtest-details").textContent.includes("当前策略：讲义因果版 V1"),
            );
            const q = new URLSearchParams({
                run: run.id,
                variant: "lecture_v1",
                symbol: "sh.600519",
                asof: run.end,
                scenario: "base",
            });
            const view = await (await context.request.get(base + "/api/stock-view?" + q)).json();
            assert.equal(view.strategy_profile.version, "lecture_causal_squeeze_v1");
            assert.equal(view.backtest.strategy.pivot_mode, "lecture_causal");
            assert.equal(view.backtest.strategy.minimum_rvol, 1.2);
            assert.equal(view.backtest.strategy.minimum_reward_risk, 1.5);
            assert.ok(view.strategy_profile.definition.context_only.includes("level_1_2_3_trends"));
            const sealed = await context.request.get(base + "/api/view?" + q);
            assert.equal(sealed.status(), 400);
            await change("结果口径", "portfolio");
            assert.equal(await page.locator("#variant-select").inputValue(), "strict_full");
            assert.notEqual(
                await page.locator('#variant-select option[value="lecture_v1"]').getAttribute("disabled"),
                null,
            );
            assert.notEqual(
                await page.locator('#variant-select option[value="lecture_v2"]').getAttribute("disabled"),
                null,
            );
            await change("结果口径", "stock");
        });
        await test("individual backtest summary, genuine fills and historical cutoff", async () => {
            assert.equal(await page.getByRole("combobox", { name: "结果口径", exact: true }).inputValue(), "stock");
            await page.waitForFunction(
                () => document.querySelector("#backtest-summary-status").textContent.includes("只完成"),
                {},
                { timeout: 180000 },
            );
            assert.equal(await page.locator("#stock-results-body tr").count(), run.symbols.length);
            assert.ok((await page.locator("#backtest-details").textContent()).includes("未产生成交"));
            const view = await change("策略版本", "proxy_full");
            assert.equal(view.result_scope, "stock");
            assert.ok(view.orders.every((o) => o.symbol === view.symbol));
            assert.equal(Number(await page.locator("#metric-trades").textContent()), view.metrics.trades);
            assert.equal(view.metrics.trades, 1);
            const q = new URLSearchParams({ run: run.id, variant: "proxy_full", asof: view.asof, scenario: "base" });
            const summary = await (
                await context.request.get(base + "/api/stock-summary?" + q, { timeout: 180000 })
            ).json();
            assert.equal(summary.results.length, run.symbols.length);
            assert.equal(
                summary.results.find((r) => r.symbol === view.symbol).metrics.total_return,
                view.metrics.total_return,
            );
            const pnl =
                view.trades.reduce((sum, t) => sum + t.pnl, 0) +
                view.backtest.open_positions.reduce((sum, p) => sum + p.unrealized_pnl, 0);
            assert.ok(Math.abs(view.metrics.equity - view.backtest.initial_capital - pnl) < 0.00001);
            await page.getByRole("button", { name: "定位最近成交 ↗", exact: true }).click();
            await loaded();
            await page.waitForFunction(
                () => document.querySelectorAll('#events .event-row[data-kind="fill"]').length >= 2,
                {},
                { timeout: 60000 },
            );
            await page
                .locator('#events .event-row[data-kind="fill"]')
                .filter({ hasText: "B 买入成交" })
                .first()
                .click();
            assert.ok((await page.locator("#selection-info").textContent()).includes("个股独立回测委托／成交"));
            assert.ok((await page.locator("#selection-info").textContent()).includes("实际成交价"));
            await page.locator(".chart-card").screenshot({ path: path.join(output, "individual-backtest-fills.png") });
            await page
                .locator("#stock-results")
                .screenshot({ path: path.join(output, "individual-backtest-summary.png") });
            const entry = view.trades[0].entry_time.slice(0, 10);
            const prefix = await (
                await context.request.get(
                    base +
                        "/api/stock-view?" +
                        new URLSearchParams({
                            run: run.id,
                            variant: "proxy_full",
                            symbol: view.symbol,
                            asof: entry,
                            scenario: "base",
                        }),
                )
            ).json();
            assert.equal(prefix.trades.length, 0);
            assert.equal(prefix.backtest.open_positions.length, 1);
            assert.ok(prefix.markers.every((m) => m.time <= entry));
            assert.ok(!prefix.markers.some((m) => m.kind === "fill" && m.side === "SELL"));
            await change("策略版本", "strict_full");
            await change("结果口径", "portfolio");
        });
        await test("strict default, empty-state and local SDK", async () => {
            assert.equal(
                await page.getByRole("combobox", { name: "策略版本", exact: true }).inputValue(),
                "strict_full",
            );
            assert.equal(await page.locator("#metric-trades").textContent(), "0");
            assert.ok(await page.locator("#trades-empty").isVisible());
            assert.ok((await page.locator("#price-chart canvas").count()) > 0);
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.sameBarLegs) > 0,
                {},
                { timeout: 60000 },
            );
            await page.locator("#show-teaching").uncheck();
            await page.waitForFunction(
                () => document.querySelector("#price-chart").dataset.teachingHighlight === "false",
            );
            assert.ok(
                Number(await page.locator("#price-chart").getAttribute("data-same-bar-legs")) > 0,
                "hiding teaching emphasis must not break geometry",
            );
            await page.locator("#show-teaching").check();
            await page.waitForFunction(() => Number(document.querySelector("#price-chart").dataset.sameBarLegs) > 0);
            await page.getByRole("combobox", { name: "折线口径", exact: true }).selectOption("strategy");
            assert.equal(await page.locator("#price-chart").getAttribute("data-lecture-vertices"), "0");
            await page.getByRole("combobox", { name: "折线口径", exact: true }).selectOption("lecture");
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.lectureVertices) > 0,
            );
            await page.waitForFunction(() => Number(document.querySelector("#price-chart").dataset.reversalPoints) > 0);
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.reversalPriceLabels) > 0,
            );
            await page.getByRole("checkbox", { name: "一级点位价格", exact: true }).uncheck();
            await page.waitForFunction(
                () => document.querySelector("#price-chart").dataset.reversalPriceLabels === "0",
            );
            assert.ok(Number(await page.locator("#price-chart").getAttribute("data-reversal-points")) > 0);
            assert.equal(
                await page.evaluate(
                    () => JSON.parse(localStorage.getItem("wavequant.research.chart.v1")).showTrendPrices,
                ),
                false,
            );
            await page.getByRole("checkbox", { name: "一级点位价格", exact: true }).check();
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.reversalPriceLabels) > 0,
            );
            await page.locator("#show-trend").uncheck();
            assert.equal(await page.locator("#price-chart").getAttribute("data-reversal-points"), "0");
            await page.waitForFunction(
                () => document.querySelector("#price-chart").dataset.reversalPriceLabels === "0",
            );
            await page.locator("#show-trend").check();
            await page.waitForFunction(() => Number(document.querySelector("#price-chart").dataset.reversalPoints) > 0);
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.reversalPriceLabels) > 0,
            );
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.secondaryPoints) > 0,
            );
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.lastFallHighGuides) > 0,
            );
            assert.ok(Number(await page.locator("#price-chart").getAttribute("data-last-fall-high-count")) > 0);
            await page.getByRole("checkbox", { name: "各级末跌高", exact: true }).uncheck();
            assert.equal(await page.locator("#price-chart").getAttribute("data-last-fall-high-guides"), "0");
            await page.getByRole("checkbox", { name: "各级末跌高", exact: true }).check();
            await page.waitForFunction(
                () => Number(document.querySelector("#price-chart").dataset.lastFallHighGuides) > 0,
            );
            const level1Count = await page.locator("#price-chart").getAttribute("data-reversal-points");
            const level2Count = await page.locator("#price-chart").getAttribute("data-secondary-points");
            await page.getByRole("checkbox", { name: "二级趋势线", exact: true }).uncheck();
            assert.equal(await page.locator("#price-chart").getAttribute("data-secondary-points"), "0");
            assert.equal(await page.locator("#price-chart").getAttribute("data-reversal-points"), level1Count);
            await page.getByRole("checkbox", { name: "二级趋势线", exact: true }).check();
            await page.getByRole("checkbox", { name: "一级趋势线", exact: true }).uncheck();
            assert.equal(await page.locator("#price-chart").getAttribute("data-secondary-points"), level2Count);
            await page.getByRole("checkbox", { name: "一级趋势线", exact: true }).check();
            await page.screenshot({ path: path.join(output, "lecture-drawing.png"), fullPage: true });
            assert.ok((await context.request.get(base + "/vendor/NOTICE")).ok());
        });
        await test("Chinese stock list, search and every available stock select matching data", async () => {
            const search = page.getByRole("searchbox", { name: "搜索股票", exact: true });
            assert.equal(await page.locator("#stock-list button").count(), run.symbols.length);
            await search.fill("茅台");
            assert.equal(await page.locator("#stock-list button").count(), 1);
            assert.ok((await page.locator("#stock-list").textContent()).includes("贵州茅台"));
            await search.fill("000858");
            assert.ok((await page.locator("#stock-list").textContent()).includes("五粮液"));
            const chooseResponse = page.waitForResponse(
                (r) => r.url().includes("/api/view?") && r.url().includes("sz.000858") && r.ok(),
            );
            await search.press("Enter");
            await chooseResponse;
            await loaded();
            assert.equal(await page.locator("#price-chart").getAttribute("data-symbol"), "sz.000858");
            await search.fill("不存在的股票");
            assert.equal(await page.locator("#stock-list button").count(), 0);
            assert.ok((await page.locator("#stock-list").textContent()).includes("没有匹配"));
            await page.getByRole("button", { name: "清空搜索", exact: true }).click();
            assert.equal(await page.locator("#stock-list button").count(), run.symbols.length);
            for (const stock of run.symbols) {
                const response = page.waitForResponse(
                    (r) => r.url().includes("/api/view?") && r.url().includes(stock.symbol) && r.ok(),
                );
                await page.locator(`#stock-list button[data-symbol="${stock.symbol}"]`).click();
                const data = await (await response).json();
                await loaded();
                assert.equal(data.symbol, stock.symbol);
                assert.equal(
                    await page.getByRole("combobox", { name: "股票", exact: true }).inputValue(),
                    stock.symbol,
                );
                assert.equal(await page.locator("#price-chart").getAttribute("data-symbol"), stock.symbol);
                assert.equal(
                    await page.locator('#stock-list button[aria-pressed="true"]').getAttribute("data-symbol"),
                    stock.symbol,
                );
                assert.ok((await page.locator("#selected-stock-summary").textContent()).includes("原始收盘"));
            }
            await change("股票", "sh.600519");
            await page
                .getByRole("complementary", { name: "股票列表", exact: true })
                .screenshot({ path: path.join(output, "stock-list-desktop.png") });
        });
        let proxy;
        await test("actual quoted child-mother path preserves all three known prices", async () => {
            const q = new URLSearchParams({
                run: run.id,
                variant: "strict_full",
                symbol: "sh.600519",
                asof: "2026-09-07",
            });
            const theory = await (await context.request.get(base + "/api/theory?" + q)).json();
            assert.equal(theory.reversal_trends.aggregation_rule, "HH_HL_or_LH_LL_switch_mixed_holds");
            assert.equal(theory.secondary_trends.aggregation_rule, "level1_structural_key_break");
            assert.equal(theory.secondary_trends.source_level, 1);
            assert.equal(theory.tertiary_trends.source_level, 2);
            assert.equal(theory.tertiary_trends.aggregation_rule, "level2_structural_key_break");
            for (const line of theory.tertiary_trends.strokes) {
                const source = theory.secondary_trends.strokes.find((s) => s.id === line.source_path);
                assert.ok(source);
                for (const p of line.points) {
                    assert.equal(p.value, source.points[p.source_level2_position].value);
                    assert.equal(p.available_at, source.points[p.confirmed_on_level2].available_at);
                }
            }
            assert.ok(theory.secondary_trends.confirmed_wave_count > 0);
            assert.ok(theory.secondary_trends.confirmed_wave_count < theory.reversal_trends.confirmed_wave_count);
            for (const line of theory.secondary_trends.strokes) {
                const source = theory.reversal_trends.strokes.find((s) => s.id === line.source_path);
                assert.ok(source);
                for (const p of line.points) {
                    assert.equal(p.value, source.points[p.source_level1_position].value);
                    assert.equal(p.available_at, source.points[p.confirmed_on_level1].available_at);
                }
            }
            assert.ok(theory.reversal_trends.confirmed_wave_count > 0);
            assert.ok(
                theory.reversal_trends.confirmed_wave_count < theory.reversal_trends.input_turn_count,
                "solid wave nodes must aggregate small turns",
            );
            assert.ok(
                theory.reversal_trends.strokes.some((s) =>
                    s.points.slice(1).some((p, i) => p.source_turn_position - s.points[i].source_turn_position > 1),
                ),
                "a wave leg must span intermediate small turns",
            );
            const stroke = theory.lecture_drawing.teaching_paths.find(
                (s) => s.points[0].time === "2026-08-28" && s.points[1].time === "2026-08-31",
            );
            assert.ok(stroke);
            assert.deepEqual(
                stroke.points.map((p) => Number(p.value.toFixed(2))),
                [1578.33, 1563.88, 1586.98],
            );
            assert.deepEqual(
                stroke.points.map((p) => p.ordinal),
                [0, 0, 1],
            );
            const connected = theory.lecture_drawing.strokes.filter((s) => s.teaching_path_ids.includes(stroke.id));
            assert.equal(connected.length, 1, "child and mother must belong to a single main path");
            assert.ok(connected[0].points.some((p) => p.time === "2026-08-31" && p.teaching_ordinal === 2));
            const ordinary = theory.lecture_drawing.strokes.filter((s) => s.kind === "ordinary");
            assert.ok(theory.lecture_drawing.inside_connections.length > 0);
            for (const link of theory.lecture_drawing.inside_connections) {
                assert.equal(theory.lecture_drawing.strokes.filter((s) => s.id === link.path_id).length, 1);
                assert.ok(link.from_index < link.index);
            }
            assert.ok(theory.lecture_drawing.issues.every((i) => !i.reason.startsWith("先母后子")));
            for (const line of ordinary) {
                assert.equal(line.seed_policy, "first_directional_high_low_pair_opposite_extreme");
                for (let i = 1; i < line.points.length; i++) {
                    assert.notEqual(
                        line.points[i - 1].kind,
                        line.points[i].kind,
                        "ordinary endpoints must alternate, never H-H or L-L",
                    );
                    assert.ok(line.points[i].index > line.points[i - 1].index);
                }
            }
        });
        await test("proxy results agree with sealed API; scenarios isolated", async () => {
            proxy = await change("策略版本", "proxy_full");
            assert.equal(proxy.metrics.trades, 2);
            assert.equal(
                Number((await page.locator("#metric-trades").textContent()).replaceAll(",", "")),
                proxy.metrics.trades,
            );
            const expensive = await change("成本场景", "cost_3x");
            assert.ok(expensive.metrics.total_return < proxy.metrics.total_return);
            await change("成本场景", "base");
        });
        await test("filled trade focuses chart and toggles work", async () => {
            await page.getByRole("button", { name: "复盘 ↗", exact: true }).first().click();
            await loaded();
            await page.waitForFunction(() => document.querySelector("#selection-info").textContent.includes("已定位"));
            for (const id of ["show-volume", "show-markers", "show-theory"]) {
                await page.locator("#" + id).uncheck();
                await page.locator("#" + id).check();
            }
            await page.waitForFunction(
                () => ["已确认结构", "当前结构未解"].includes(document.querySelector("#theory-status").textContent),
                {},
                { timeout: 60000 },
            );
            assert.ok((await page.locator("#events .event-row").count()) > 0);
            await page.waitForFunction(() => Number(document.querySelector("#price-chart").dataset.polylinePoints) > 2);
            await page.locator("#show-theory").uncheck();
            assert.equal(await page.locator("#price-chart").getAttribute("data-polyline-segments"), "0");
            await page.locator("#show-theory").check();
            await page.waitForFunction(() => Number(document.querySelector("#price-chart").dataset.polylinePoints) > 2);
            await page.screenshot({ path: path.join(output, "desktop-trade.png"), fullPage: true });
        });
        await test("annotation selection, exact fill levels and independent layer filters", async () => {
            const buy = page.locator('#events .event-row[data-kind="fill"]').filter({ hasText: "B 买入成交" }).first();
            await buy.click();
            assert.ok((await page.locator("#selection-info").textContent()).includes("实际成交价"));
            assert.ok(Number(await page.locator("#price-chart").getAttribute("data-level-count")) >= 1);
            await page.locator("#show-levels").uncheck();
            assert.equal(await page.locator("#price-chart").getAttribute("data-level-count"), "0");
            await page.locator("#show-levels").check();
            await page.locator("#show-fills").uncheck();
            assert.equal(await page.locator('#events [data-kind="fill"]').count(), 0);
            await page.locator("#show-fills").check();
            await page.locator("#show-markers").uncheck();
            assert.equal(await page.locator('#events [data-kind="signal"]').count(), 0);
            await page.locator("#show-markers").check();
            await page.locator("#show-rules").uncheck();
            assert.equal(await page.locator('#events [data-kind="rule"]').count(), 0);
            await page.locator("#show-rules").check();
            await page.screenshot({ path: path.join(output, "annotation-selected.png"), fullPage: true });
        });
        await test("performance, orders, health, and local export", async () => {
            await page.getByRole("button", { name: "策略绩效" }).click();
            assert.ok(await page.locator("#equity-chart").isVisible());
            assert.ok((await page.locator("#drawdown-chart canvas").count()) > 0);
            await page.waitForFunction(
                (count) =>
                    ["equity-chart", "drawdown-chart", "exposure-chart"].every((id) => {
                        const data = document.getElementById(id).dataset;
                        return Number(data.visibleFromIndex) <= 0 && Number(data.visibleToIndex) >= count - 1;
                    }),
                proxy.curve.length,
            );
            await page.screenshot({ path: path.join(output, "performance.png"), fullPage: true });
            await page.getByRole("button", { name: "订单与信号" }).click();
            assert.equal(await page.locator("#orders-body tr").count(), proxy.orders.length);
            const download = page.waitForEvent("download");
            await page.getByRole("button", { name: "导出当前明细", exact: true }).click();
            const exportPath = path.join(output, "orders-export.json");
            await (await download).saveAs(exportPath);
            assert.equal(JSON.parse(fs.readFileSync(exportPath, "utf8")).orders.length, proxy.orders.length);
            await page.getByRole("button", { name: "系统状态" }).click();
            await page.waitForFunction(() => document.querySelector("#health-body").children.length > 0);
            assert.ok((await page.locator("#health-summary").textContent()).includes("实盘关闭"));
        });
        await test("historical replay excludes future exits and preserves cutoff across symbols", async () => {
            await page.getByRole("button", { name: "K 线复盘" }).click();
            const symbol = await page.getByRole("combobox", { name: "股票", exact: true }).inputValue();
            const dates = run.symbols.find((s) => s.symbol === symbol).sessions;
            const entry = proxy.trades.map((t) => t.entry_time.slice(0, 10)).sort()[0];
            const index = dates.findLastIndex((d) => d <= entry);
            assert.ok(index > 0);
            const response = page.waitForResponse((r) => r.url().includes("/api/view?") && r.status() === 200);
            await page.getByRole("slider", { name: "回放日期" }).fill(String(index));
            await page.getByRole("slider", { name: "回放日期" }).dispatchEvent("change");
            const historic = await (await response).json();
            await loaded();
            assert.equal(historic.trades.length, 0);
            assert.ok(historic.bars.every((b) => b.time <= entry));
            assert.ok(historic.orders.every((o) => o.timestamp.slice(0, 10) <= entry));
            assert.equal(await page.locator("#metric-trades").textContent(), "0");
            const other = run.symbols.find((s) => s.symbol !== symbol).symbol;
            const switched = await change("股票", other);
            assert.ok(switched.asof <= historic.asof);
            const listResponse = page.waitForResponse(
                (r) => r.url().includes("/api/view?") && r.url().includes(symbol) && r.ok(),
            );
            await page.locator(`#stock-list button[data-symbol="${symbol}"]`).click();
            const listed = await (await listResponse).json();
            await loaded();
            assert.ok(listed.asof <= historic.asof);
            assert.ok(listed.bars.every((b) => b.time <= historic.asof));
            assert.ok((await page.locator("#selected-stock-summary").textContent()).includes(listed.asof));
        });
        await test("failed API hides old snapshot, navigation does not reveal it, refresh recovers", async () => {
            await page.route(
                "**/api/view?*",
                (route) =>
                    route.fulfill({
                        status: 500,
                        contentType: "application/json",
                        body: JSON.stringify({ error: "E2E injected error" }),
                    }),
                { times: 1 },
            );
            await page.getByRole("button", { name: "刷新", exact: false }).click();
            await page.locator("#error").waitFor({ state: "visible" });
            assert.ok(await page.locator("#price-chart").isHidden());
            assert.ok((await page.locator("#selected-stock-summary").textContent()).includes("加载失败"));
            await page.getByRole("button", { name: "策略绩效" }).click();
            assert.ok(await page.locator("#equity-chart").isHidden());
            await page.getByRole("button", { name: "刷新", exact: false }).click();
            await loaded();
            assert.ok(await page.locator("#equity-chart").isVisible());
        });
        await test("mobile layout without horizontal page overflow", async () => {
            await page.getByRole("button", { name: "K 线复盘" }).click();
            await page.setViewportSize({ width: 390, height: 844 });
            await page.waitForFunction(() => document.documentElement.scrollWidth <= window.innerWidth);
            await page.waitForFunction(
                () => ["已确认结构", "当前结构未解"].includes(document.querySelector("#theory-status").textContent),
                {},
                { timeout: 60000 },
            );
            assert.ok((await page.locator("#price-chart").boundingBox()).width > 200);
            await page.getByRole("searchbox", { name: "搜索股票", exact: true }).fill("招商");
            assert.equal(await page.locator("#stock-list button").count(), 1);
            await page.getByRole("button", { name: "清空搜索", exact: true }).click();
            await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: true });
        });
        await test("actual TradingView canvas click and tooltip select a priced marker", async () => {
            await page.setViewportSize({ width: 1440, height: 1050 });
            // Isolated synthetic component fixture; never mixed into market results.
            const point = await page.evaluate(async () => {
                const { PriceChart } = await import("/charts.js");
                const container = document.createElement("div");
                container.id = "canvas-click-fixture";
                Object.assign(container.style, {
                    position: "fixed",
                    left: "100px",
                    top: "100px",
                    width: "700px",
                    height: "400px",
                    zIndex: 9999,
                });
                document.body.append(container);
                const sample = new PriceChart(
                    container,
                    () => {},
                    (items) => {
                        container.dataset.selected = items[0].id;
                    },
                );
                sample.setData({
                    asof: "2026-01-03",
                    bars: [1, 2, 3].map((i) => ({
                        time: `2026-01-0${i}`,
                        open: 10,
                        high: 12,
                        low: 9,
                        close: 11,
                        volume: 100,
                    })),
                    markers: [
                        { id: "fixture-buy", time: "2026-01-02", kind: "fill", side: "BUY", price: 10.5, levels: [] },
                    ],
                    trades: [],
                });
                await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                sample.chart.timeScale().fitContent();
                await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                window.cleanupClickFixture = () => {
                    sample.destroy();
                    container.remove();
                };
                window.setTeachingFixture = async () => {
                    sample.setTheory({
                        points: [],
                        events: [],
                        shapes: [],
                        lecture_drawing: {
                            strokes: [
                                {
                                    id: "sample-mother",
                                    kind: "teaching",
                                    points: [
                                        {
                                            time: "2026-01-01",
                                            available_at: "2026-01-02",
                                            value: 11,
                                            ordinal: 0,
                                            kind: "H",
                                            state: "teaching",
                                        },
                                        {
                                            time: "2026-01-02",
                                            available_at: "2026-01-02",
                                            value: 9,
                                            ordinal: 0,
                                            kind: "L",
                                            state: "teaching",
                                        },
                                        {
                                            time: "2026-01-02",
                                            available_at: "2026-01-02",
                                            value: 12,
                                            ordinal: 1,
                                            kind: "H",
                                            state: "teaching",
                                        },
                                    ],
                                },
                            ],
                        },
                    });
                    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const point = sample.lectureOverlay.projected[0].points[1];
                    return { x: 100 + point.x, y: 100 + point.y };
                };
                return {
                    x: 100 + sample.chart.timeScale().timeToCoordinate("2026-01-02"),
                    y: 100 + sample.candles.priceToCoordinate(10.5),
                };
            });
            await page.mouse.move(point.x, point.y);
            await page.locator("#canvas-click-fixture .chart-tooltip").waitFor({ state: "visible" });
            await page.mouse.click(point.x, point.y);
            await page.waitForFunction(
                () => document.querySelector("#canvas-click-fixture").dataset.selected === "fixture-buy",
            );
            const motherLow = await page.evaluate(() => window.setTeachingFixture());
            await page.mouse.move(motherLow.x, motherLow.y);
            await page
                .locator("#canvas-click-fixture .chart-tooltip")
                .filter({ hasText: "子母路径第 2 点" })
                .waitFor({ state: "visible" });
            // Separate intentional clicks from the SDK's double-click gesture window.
            await page.waitForTimeout(500);
            await page.mouse.click(motherLow.x, motherLow.y);
            await page.waitForFunction(
                () => document.querySelector("#canvas-click-fixture").dataset.selected === "drawing:sample-mother:1",
            );
            await page.screenshot({ path: path.join(output, "teaching-point-click.png"), fullPage: true });
            await page.evaluate(() => window.cleanupClickFixture());
        });
        await test("N and inverse N are connected four-point solid lines", async () => {
            const drawn = await page.evaluate(async () => {
                const { PriceChart } = await import("/charts.js");
                const container = document.createElement("div");
                container.id = "n-line-fixture";
                Object.assign(container.style, {
                    position: "fixed",
                    left: "100px",
                    top: "100px",
                    width: "800px",
                    height: "420px",
                    zIndex: 9999,
                });
                document.body.append(container);
                const sample = new PriceChart(container, () => {});
                const days = Array.from({ length: 8 }, (_, i) => `2026-01-0${i + 1}`);
                sample.setData({
                    asof: days.at(-1),
                    bars: days.map((time) => ({ time, open: 10, high: 13, low: 8, close: 11, volume: 100 })),
                    markers: [],
                    trades: [],
                });
                const shapes = [
                    { direction: "up", points: [8, 11, 9, 13].map((value, i) => ({ time: days[i], value })) },
                    { direction: "down", points: [13, 10, 12, 8].map((value, i) => ({ time: days[i + 4], value })) },
                ];
                sample.setTheory({ points: [], events: [], shapes });
                sample.chart.timeScale().fitContent();
                await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                window.cleanupNFixture = () => {
                    sample.destroy();
                    container.remove();
                };
                return sample.lines.map((s) => ({
                    data: s.data(),
                    style: s.options().lineStyle,
                    circles: s.options().pointMarkersVisible,
                    crosshairCircle: s.options().crosshairMarkerVisible,
                }));
            });
            assert.equal(drawn.length, 2);
            assert.deepEqual(
                drawn.map((s) => s.data.map((p) => p.value)),
                [
                    [8, 11, 9, 13],
                    [13, 10, 12, 8],
                ],
            );
            for (const line of drawn) {
                assert.equal(line.style, 0);
                assert.equal(line.circles, false);
                assert.equal(line.crosshairCircle, false);
            }
            await page
                .locator("#n-line-fixture")
                .screenshot({ path: path.join(output, "n-solid-lines-synthetic.png") });
            await page.evaluate(() => window.cleanupNFixture());
        });
        await test("solid reversal line supports H/L selection and causal key line", async () => {
            const point = await page.evaluate(async () => {
                const { PriceChart } = await import("/charts.js");
                const container = document.createElement("div");
                container.id = "reversal-fixture";
                Object.assign(container.style, {
                    position: "fixed",
                    left: "100px",
                    top: "100px",
                    width: "800px",
                    height: "420px",
                    zIndex: 9999,
                });
                document.body.append(container);
                const chart = new PriceChart(
                    container,
                    () => {},
                    (items) => {
                        container.dataset.selected = items[0].id;
                    },
                );
                const days = Array.from({ length: 6 }, (_, i) => `2026-01-0${i + 1}`);
                chart.setData({
                    asof: days.at(-1),
                    bars: days.map((time) => ({ time, open: 11, high: 17, low: 8, close: 12, volume: 100 })),
                    markers: [],
                    trades: [],
                });
                const points = [9, 14, 10, 16].map((value, i) => ({
                    index: i,
                    ordinal: 0,
                    time: days[i],
                    available_at: days[i + 1],
                    value,
                    kind: i % 2 ? "H" : "L",
                    label: `${i % 2 ? "H" : "L"}${Math.floor(i / 2) + 1}`,
                    reversal: i % 2 ? "负反转" : "正反转",
                    trend: "多头趋势",
                    observations: [],
                    levels: i ? [{ name: i % 2 ? "末升低" : "末跌高", price: [9, 14, 10][i - 1] }] : [],
                }));
                chart.setTheory({
                    points: [],
                    events: [],
                    shapes: [],
                    lecture_drawing: { strokes: [] },
                    reversal_trends: { strokes: [{ id: "reversal-sample", kind: "reversal", points }] },
                });
                chart.chart.timeScale().fitContent();
                await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
                window.inspectReversalFixture = () =>
                    chart.levelLines.map((s) => ({
                        style: s.options().lineStyle,
                        data: s.data(),
                        circles: s.options().pointMarkersVisible,
                    }));
                window.cleanupReversalFixture = () => {
                    chart.destroy();
                    container.remove();
                };
                const p = chart.lectureOverlay.projected[0].points[1];
                return { x: 100 + p.x, y: 100 + p.y };
            });
            await page.mouse.move(point.x, point.y);
            await page
                .locator("#reversal-fixture .chart-tooltip")
                .filter({ hasText: "H1 · 负反转高点" })
                .waitFor({ state: "visible" });
            await page.mouse.click(point.x, point.y);
            await page.waitForFunction(
                () => document.querySelector("#reversal-fixture").dataset.selected === "drawing:reversal-sample:1",
            );
            const levels = await page.evaluate(() => window.inspectReversalFixture());
            assert.equal(levels[0].style, 0);
            assert.equal(levels[0].circles, false);
            assert.equal(levels[0].data[0].time, "2026-01-03");
            assert.equal(levels[0].data[0].value, 9);
            await page
                .locator("#reversal-fixture")
                .screenshot({ path: path.join(output, "reversal-solid-synthetic.png") });
            await page.evaluate(() => window.cleanupReversalFixture());
        });
        await test("Shanghai Airport level-one connects July low via July high to August low", async () => {
            const result = await page.evaluate(async () => {
                const { PriceChart } = await import("/charts.js");
                const q = "symbol=sh.600009&asof=2026-09-07";
                const [view, theory] = await Promise.all([
                    fetch("/api/tdx-view?" + q).then((r) => r.json()),
                    fetch("/api/tdx-theory?" + q).then((r) => r.json()),
                ]);
                const before = JSON.stringify(theory),
                    container = document.createElement("div");
                container.id = "airport-continuity";
                Object.assign(container.style, {
                    position: "fixed",
                    left: "10px",
                    top: "10px",
                    width: "1400px",
                    height: "750px",
                    zIndex: 9999,
                });
                document.body.append(container);
                const c = new PriceChart(container, () => {});
                c.setData(view);
                c.setTheory(theory);
                c.setSecondaryTrendVisible(false);
                c.setTertiaryTrendVisible(false);
                c.chart.timeScale().setVisibleRange({ from: "2026-07-13", to: "2026-09-07" });
                await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
                c.lectureOverlay.updateAllViews();
                const link = c.lectureOverlay.projected.find(
                    (s) =>
                        s.stroke.kind === "reversal-connection" &&
                        s.stroke.points[0].time === "2026-07-22" &&
                        s.stroke.points.at(-1).time === "2026-08-17",
                );
                const p = link?.points || [];
                const allAlternating = c.lectureOverlay.strokes
                    .filter((s) => s.kind === "reversal" || s.kind === "reversal-connection")
                    .every((s) => s.points.slice(1).every((p, i) => p.kind !== s.points[i].kind));
                const secondHit =
                    p.length === 3 ? c.lectureOverlay.hitTest((p[1].x + p[2].x) / 2, (p[1].y + p[2].y) / 2) : null;
                window.cleanupAirport = () => {
                    c.chart.remove();
                    container.remove();
                };
                return {
                    points: p.map((p) => ({
                        date: p.point.time,
                        kind: p.point.kind,
                        value: p.point.value,
                        x: p.x,
                        y: p.y,
                    })),
                    allAlternating,
                    unchanged: before === JSON.stringify(theory),
                    description: secondHit && c.lectureOverlay.annotation(secondHit.externalId)?.description,
                };
            });
            assert.deepEqual(
                result.points.map((p) => [p.date, p.kind, p.value]),
                [
                    ["2026-07-22", "L", 23.4],
                    ["2026-07-30", "H", 24.95],
                    ["2026-08-17", "L", 22.86],
                ],
            );
            assert.ok(result.points.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y)));
            assert.ok(result.points[1].y < result.points[0].y && result.points[1].y < result.points[2].y);
            assert.ok(result.allAlternating);
            assert.ok(result.unchanged);
            assert.match(result.description, /2026-07-30/);
            await page
                .locator("#airport-continuity")
                .screenshot({ path: path.join(output, "airport-level1-low-high-low.png") });
            await page.evaluate(() => window.cleanupAirport());
        });
        await test("SAIC level-one April-June continuity and display-only source-gap links", async () => {
            const result = await page.evaluate(async (runId) => {
                const { PriceChart } = await import("/charts.js");
                const q = new URLSearchParams({
                    run: runId,
                    variant: "strict_full",
                    symbol: "sh.600104",
                    asof: "2026-09-07",
                });
                const [view, theory] = await Promise.all([
                    fetch("/api/view?" + q + "&scenario=base").then((r) => r.json()),
                    fetch("/api/theory?" + q).then((r) => r.json()),
                ]);
                const before = JSON.stringify(theory),
                    container = document.createElement("div");
                container.id = "saic-continuity";
                Object.assign(container.style, {
                    position: "fixed",
                    left: "10px",
                    top: "10px",
                    width: "1400px",
                    height: "800px",
                    zIndex: 9999,
                });
                document.body.append(container);
                const c = new PriceChart(container, () => {});
                c.setData(view);
                c.setTheory(theory);
                c.setSecondaryTrendVisible(false);
                c.setTertiaryTrendVisible(false);
                const refresh = async () => {
                    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
                    c.lectureOverlay.updateAllViews();
                };
                c.chart.timeScale().setVisibleRange({ from: "2026-04-01", to: "2026-06-30" });
                await refresh();
                const points = c.lectureOverlay.projected
                    .filter((s) => s.stroke.kind === "reversal")
                    .flatMap((s) => s.points)
                    .filter((p) => p.point.time >= "2026-04-01" && p.point.time <= "2026-06-30");
                const edges = points.slice(1).map((b, i) => ({
                    from: points[i].point.time,
                    to: b.point.time,
                    coordinates: [points[i].x, points[i].y, b.x, b.y],
                }));
                // A viewport inside the source gap still renders its crossing connector.
                c.chart.timeScale().setVisibleRange({ from: "2024-07-15", to: "2024-07-25" });
                await refresh();
                const link = c.lectureOverlay.projected.find(
                    (s) =>
                        s.stroke.kind === "reversal-connection" &&
                        s.stroke.points[0].time === "2024-07-09" &&
                        s.stroke.points[1].time === "2024-08-01",
                );
                const gap = !!link && link.points.every((p) => p.x !== null && p.y !== null);
                const info = link && c.lectureOverlay.annotation(`drawing:${link.stroke.id}:0`);
                c.setTrendVisible(false);
                await refresh();
                const hidden = Number(container.dataset.reversalConnections) === 0;
                c.setTrendVisible(true);
                c.chart.timeScale().setVisibleRange({ from: "2026-04-01", to: "2026-06-30" });
                await refresh();
                window.cleanupSaicContinuity = () => {
                    c.chart.remove();
                    container.remove();
                };
                return { edges, gap, hidden, scope: info?.raw.scope, unchanged: before === JSON.stringify(theory) };
            }, run.id);
            assert.deepEqual(
                result.edges.map((e) => [e.from, e.to]),
                [
                    ["2026-04-01", "2026-05-28"],
                    ["2026-05-28", "2026-06-02"],
                    ["2026-06-02", "2026-06-29"],
                ],
            );
            assert.ok(result.edges.every((e) => e.coordinates.every(Number.isFinite)));
            for (let i = 1; i < result.edges.length; i++)
                assert.deepEqual(result.edges[i - 1].coordinates.slice(2), result.edges[i].coordinates.slice(0, 2));
            assert.ok(result.gap);
            assert.ok(result.hidden);
            assert.ok(result.unchanged);
            assert.equal(result.scope, "display_only_connection");
            await page
                .locator("#saic-continuity")
                .screenshot({ path: path.join(output, "saic-level1-april-june-continuous.png") });
            await page.evaluate(() => window.cleanupSaicContinuity());
        });
        for (const third of [false, true])
            await test(
                third
                    ? "actual tertiary trend depends on level two and has independent visibility"
                    : "actual secondary trend selection exposes level-one break evidence",
                async () => {
                    await page.setViewportSize({ width: 1440, height: 1050 });
                    const target = await page.evaluate(
                        async ({ runId, third }) => {
                            const { PriceChart } = await import("/charts.js");
                            const q = new URLSearchParams({
                                run: runId,
                                variant: "strict_full",
                                symbol: third ? "sz.000858" : "sh.600519",
                                asof: "2026-09-07",
                            });
                            const [view, theory] = await Promise.all([
                                fetch("/api/view?" + q + "&scenario=base").then((r) => r.json()),
                                fetch("/api/theory?" + q).then((r) => r.json()),
                            ]);
                            const container = document.createElement("div");
                            container.id = "secondary-actual";
                            Object.assign(container.style, {
                                position: "fixed",
                                left: "50px",
                                top: "50px",
                                width: "1200px",
                                height: "620px",
                                zIndex: 9999,
                            });
                            document.body.append(container);
                            const chart = new PriceChart(
                                container,
                                () => {},
                                (items) => {
                                    container.dataset.selected = items[0].id;
                                },
                            );
                            chart.setData(view);
                            chart.setTheory(theory);
                            if (!third) chart.setTertiaryTrendVisible(false);
                            const source = third ? theory.tertiary_trends : theory.secondary_trends;
                            const line = third
                                    ? source.strokes.find((s) => s.points.length >= 2)
                                    : source.strokes.at(-1),
                                last = line.points.at(-1),
                                prior = line.points.at(-2) || last;
                            chart.chart.timeScale().setVisibleLogicalRange({
                                from: Math.max(0, prior.index - 10),
                                to: third ? last.index + 30 : view.bars.length + 1,
                            });
                            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
                            if (third) {
                                const count = container.dataset.tertiaryPoints;
                                chart.setSecondaryTrendVisible(false);
                                chart.setTrendVisible(false);
                                if (container.dataset.tertiaryPoints !== count)
                                    throw new Error("hiding lower layers changed tertiary");
                                chart.setSecondaryTrendVisible(true);
                                chart.setTrendVisible(true);
                                const second = container.dataset.secondaryPoints,
                                    first = container.dataset.reversalPoints;
                                chart.setTertiaryTrendVisible(false);
                                if (
                                    container.dataset.tertiaryPoints !== "0" ||
                                    container.dataset.secondaryPoints !== second ||
                                    container.dataset.reversalPoints !== first
                                )
                                    throw new Error("tertiary toggle changed lower layers");
                                chart.setTertiaryTrendVisible(true);
                            }
                            const projected = chart.lectureOverlay.projected
                                .find((s) => s.stroke.id === line.id)
                                .points.at(-1);
                            window.inspectSecondaryActual = () => ({
                                item: chart.selected,
                                levels: chart.levelLines.map((s) => ({ style: s.options().lineStyle, data: s.data() })),
                            });
                            window.cleanupSecondaryActual = () => {
                                chart.destroy();
                                container.remove();
                            };
                            return {
                                x: 50 + projected.x,
                                y: 50 + projected.y,
                                id: `drawing:${line.id}:${line.points.length - 1}`,
                                known: last.available_at,
                                price: last.broken_key.value,
                            };
                        },
                        { runId: run.id, third },
                    );
                    await page.mouse.move(target.x, target.y);
                    await page
                        .locator("#secondary-actual .chart-tooltip")
                        .filter({ hasText: third ? "三级" : "二级" })
                        .waitFor({ state: "visible" });
                    await page.mouse.click(target.x, target.y);
                    await page.waitForFunction(
                        (id) => document.querySelector("#secondary-actual").dataset.selected === id,
                        target.id,
                    );
                    const evidence = await page.evaluate(() => window.inspectSecondaryActual());
                    assert.equal(evidence.item.raw.trend_level, third ? 3 : 2);
                    assert.equal(evidence.item.time, target.known);
                    assert.ok(evidence.item.description.includes(third ? "基于二级趋势线" : "基于一级趋势线"));
                    assert.equal(evidence.levels[0].style, 0);
                    assert.equal(evidence.levels[0].data[0].value, target.price);
                    assert.equal(evidence.levels[0].data[0].time, target.known);
                    await page.locator("#secondary-actual").screenshot({
                        path: path.join(output, third ? "tertiary-trend-actual.png" : "secondary-trend-actual.png"),
                    });
                    await page.evaluate(() => window.cleanupSecondaryActual());
                },
            );
        await test("current TDX stock -> adjusted backtest -> B/S ledger -> causal decision details", async () => {
            await page.setViewportSize({ width: 1440, height: 1050 });
            await change("结果口径", "tdx");
            await change("股票", "sh.600519");
            const firstResponse = page.waitForResponse((r) => r.url().includes("/api/tdx-backtest?") && r.ok(), {
                timeout: 60000,
            });
            await page.locator("#run-stock-backtest").click();
            await firstResponse;
            await loaded();
            // The now-enabled selector explicitly labels this as a research proxy.
            const response = page.waitForResponse((r) => r.url().includes("/api/tdx-backtest?") && r.ok(), {
                timeout: 60000,
            });
            await page.locator("#variant-select").selectOption("proxy_full");
            const view = await (await response).json();
            await loaded();
            assert.equal(view.data_source, "tdx");
            assert.equal(view.variant, "proxy_full");
            assert.equal(view.price_basis, "causal_adjusted_equivalent");
            assert.equal(view.theory.price_basis, view.price_basis);
            assert.equal(view.run_id, view.theory.run_id);
            const fills = view.markers.filter((m) => m.kind === "fill");
            assert.equal(fills.length, 2);
            assert.equal(await page.locator("#fills-body tr").count(), 2);
            for (const f of fills) {
                const bar = view.bars.find((b) => b.time === f.time);
                assert.equal(f.adjustment_factor, bar.factor);
                assert.ok(f.signal_time < f.time);
                assert.ok(Math.abs(f.raw_price * f.adjustment_factor - f.price) < 1e-8);
            }
            await page.locator("#fills-body button").filter({ hasText: "查看入场条件" }).click();
            let detail = await page.locator("#selection-info").textContent();
            assert.ok(detail.includes("2019-02-11"));
            assert.ok(detail.includes("2019-02-26"));
            assert.ok(detail.includes("2019-02-27"));
            assert.ok(detail.includes("开盘费用后盈亏比"));
            assert.ok(!detail.includes("undefined"));
            await page.locator(".chart-card").screenshot({ path: path.join(output, "tdx-backtest-buy-marker.png") });
            await page
                .locator("#selection-info")
                .screenshot({ path: path.join(output, "tdx-backtest-entry-evidence.png") });
            await page.locator("#fills-body button").filter({ hasText: "查看退出原因" }).click();
            detail = await page.locator("#selection-info").textContent();
            assert.ok(detail.includes("退出原因"));
            assert.ok(detail.includes("2019-03-29"));
            const download = page.waitForEvent("download");
            await page.locator("#download-backtest").click();
            const artifact = await download;
            await artifact.saveAs(path.join(output, "tdx-backtest-ledger.json"));
            const saved = JSON.parse(fs.readFileSync(path.join(output, "tdx-backtest-ledger.json"), "utf8"));
            assert.equal(saved.run_id, view.run_id);
            assert.deepEqual(saved.orders, view.orders);
            assert.ok(saved.backtest.source.gbbq_sha256);
            const lectureResponse = page.waitForResponse(
                (r) => r.url().includes("/api/tdx-backtest?") && r.url().includes("lecture_v1") && r.ok(),
                { timeout: 60000 },
            );
            await page.locator("#variant-select").selectOption("lecture_v1");
            const lecture = await (await lectureResponse).json();
            await loaded();
            assert.equal(lecture.theory.strategy_pivot_mode, "lecture_causal");
            assert.equal(lecture.theory.price_basis, lecture.price_basis);
            assert.equal(lecture.backtest.counts.long_signals, 1);
            assert.equal(lecture.metrics.entry_fills, 0);
            assert.ok(lecture.orders.some((o) => o.reason === "risk_budget_below_one_lot"));
            assert.ok(lecture.markers.some((m) => m.kind === "signal" && m.side === "LONG" && m.time === "2022-07-05"));
            assert.ok(!lecture.markers.some((m) => m.kind === "fill"));
            await page
                .locator("#backtest-details details")
                .getByText("当前策略：讲义因果版 V1（查看生效规则）", { exact: true })
                .click();
            await page
                .locator("#backtest-details")
                .screenshot({ path: path.join(output, "lecture-v1-rules-and-rejections.png") });
            const lectureDownload = page.waitForEvent("download");
            await page.locator("#download-backtest").click();
            await (await lectureDownload).saveAs(path.join(output, "lecture-v1-ledger.json"));
            const exported = JSON.parse(fs.readFileSync(path.join(output, "lecture-v1-ledger.json"), "utf8"));
            assert.equal(exported.strategy_profile.version, "lecture_causal_squeeze_v1");
            assert.deepEqual(exported.orders, lecture.orders);
            const v2Response = page.waitForResponse(
                (r) => r.url().includes("/api/tdx-backtest?") && r.url().includes("lecture_v2") && r.ok(),
                { timeout: 60000 },
            );
            await page.locator("#variant-select").selectOption("lecture_v2");
            const v2 = await (await v2Response).json();
            await loaded();
            assert.equal(v2.backtest.counts.buy_point_transition_squeeze, 1);
            assert.equal(v2.backtest.counts.buy_point_mature_shallow_squeeze, 1);
            const second = v2.markers.find((m) => m.kind === "signal" && m.reason === "system_mature_shallow_squeeze");
            assert.equal(second.time, "2022-07-05");
            assert.equal(second.decision_evidence[0].trend_level, 1);
            assert.ok(second.decision_evidence[0].counter_ratio < 1 / 3);
            await page.getByRole("button", { name: "≡ 订单与信号", exact: true }).click();
            await page
                .locator("#orders-body tr")
                .filter({ hasText: "2022-07-06" })
                .getByRole("button", { name: "定位", exact: true })
                .click();
            await page
                .locator("#events button")
                .filter({ hasText: "2022-07-05" })
                .filter({ hasText: "买入信号" })
                .click();
            const v2detail = await page.locator("#selection-info").textContent();
            assert.ok(v2detail.includes("第二类 · 重点"));
            assert.ok(v2detail.includes("2022-06-10"));
            assert.ok(v2detail.includes("2022-06-21"));
            assert.ok(v2detail.includes("30.43"));
            assert.ok(!v2detail.includes("undefined"));
            assert.equal(v2.metrics.entry_fills, 0);
            await page
                .locator("#selection-info")
                .screenshot({ path: path.join(output, "lecture-v2-second-buy-evidence.png") });
            await page.locator("#backtest-start").fill("2018-01-01");
            const unsupported = page.waitForResponse(
                (r) => r.url().includes("/api/tdx-backtest?") && r.status() === 400,
            );
            await page.locator("#symbol-select").selectOption("sz.300750");
            await unsupported;
            await page.locator("#error").waitFor({ state: "visible" });
            assert.ok(await page.locator("#page-workspace").isHidden());
            assert.ok(await page.locator("#download-backtest").isDisabled());
            await change("结果口径", "tdx");
        });
        await test("buy-point screen uses dated LONG evidence, links to chart, and invalidates changed context", async () => {
            await change("结果口径", "stock");
            await change("股票", "sh.600519");
            await change("策略版本", "proxy_full");
            const index = run.symbols.find((s) => s.symbol === "sh.600519").sessions.indexOf("2019-03-11");
            assert.ok(index >= 0);
            const response = page.waitForResponse(
                (r) => r.url().includes("/api/stock-view?") && r.url().includes("2019-03-11") && r.ok(),
            );
            await page.locator("#replay-slider").fill(String(index));
            await page.locator("#replay-slider").dispatchEvent("change");
            await response;
            await loaded();
            await page.getByRole("button", { name: "符合买点", exact: true }).click();
            assert.ok(await page.locator("#stock-list").isHidden());
            await page.getByRole("button", { name: "扫描买点", exact: true }).click();
            await page.waitForFunction(
                () => document.querySelector("#scan-status").textContent.includes("扫描完成"),
                {},
                { timeout: 90000 },
            );
            const match = page.locator('#buy-points-list .buy-point-item[data-symbol="sh.600519"]');
            assert.equal(await match.count(), 1);
            assert.ok((await match.textContent()).includes("待次开盘验证"));
            await page
                .locator(".stock-browser")
                .screenshot({ path: path.join(output, "buy-points-historical-signal.png") });
            await match.click();
            await loaded();
            await page.waitForFunction(() =>
                document.querySelector("#selection-info").textContent.includes("买点筛选证据"),
            );
            assert.equal(await page.locator("#asof-label").textContent(), "2019-03-11");
            assert.ok((await page.locator("#selection-info").textContent()).includes("2019-02-26"));
            await change("策略版本", "strict_full");
            assert.equal(await page.locator("#buy-points-list .buy-point-item").count(), 0);
            assert.ok((await page.locator("#scan-status").textContent()).includes("重新扫描"));
            await page.getByRole("button", { name: "扫描买点", exact: true }).click();
            await page.waitForFunction(
                () => document.querySelector("#scan-status").textContent.includes("扫描完成"),
                {},
                { timeout: 90000 },
            );
            assert.equal(await page.locator("#buy-points-list .buy-point-item").count(), 0);
            assert.ok((await page.locator("#buy-points-list").textContent()).includes("查看买点条件诊断"));
            assert.ok((await page.locator("#buy-points-list").textContent()).includes("窗口内无入场信号"));
            await change("结果口径", "tdx");
            assert.ok(await page.locator("#variant-select").isEnabled());
            await page.getByRole("button", { name: "扫描买点", exact: true }).click();
            await page.waitForFunction(() => !document.querySelector("#scan-cancel").disabled, {}, { timeout: 30000 });
            await page.getByRole("button", { name: "取消扫描", exact: true }).click();
            await page.waitForFunction(
                () => document.querySelector("#scan-status").textContent.includes("已取消"),
                {},
                { timeout: 60000 },
            );
            await page.getByRole("button", { name: "全部股票", exact: true }).click();
            assert.ok(await page.locator("#stock-list").isVisible());
        });
        await test("no runtime exceptions or external network dependency", async () => {
            assert.deepEqual(errors, []);
            assert.deepEqual(external, []);
        });
        await context.tracing.stop();
    } catch (error) {
        results.push({ status: "FAIL", error: error.stack });
        await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
        await context.tracing.stop({ path: path.join(output, "failure-trace.zip") });
        process.exitCode = 1;
        console.error(error);
    } finally {
        fs.writeFileSync(path.join(output, "report.json"), JSON.stringify({ base, results }, null, 2));
        console.log("Evidence: " + output);
        await browser.close();
    }
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
