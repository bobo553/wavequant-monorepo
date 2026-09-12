/* Actual TDX engine + browser; no mocked prices or ledger in the happy path. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.DASHBOARD_URL || "http://127.0.0.1:8765";
const output = path.resolve(__dirname, "../results/ratio_ui_v3", new Date().toISOString().replaceAll(/[:.]/g, "-"));
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
    });
    const context = await browser.newContext({ viewport: { width: 1550, height: 1050 } }),
        page = await context.newPage();
    const errors = [],
        results = [],
        responses = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("response", async (r) => {
        if (r.url().includes("/api/tdx-backtest?") && r.ok()) responses.push(await r.json());
    });
    await context.tracing.start({ screenshots: true, snapshots: true });
    async function loaded() {
        await page.waitForFunction(
            () =>
                document.querySelector("#loading").hidden &&
                document.querySelector("#error").hidden &&
                document.querySelector("#price-chart").dataset.symbol,
            {},
            { timeout: 120000 },
        );
    }
    async function test(name, fn) {
        await fn();
        results.push({ name, status: "PASS" });
        console.log("PASS " + name);
    }
    try {
        await page.goto(base + "/research");
        await loaded();
        await test("four selectable V3 plans and fresh context", async () => {
            assert.equal(
                await page.getByRole("combobox", { name: "策略版本", exact: true }).inputValue(),
                "lecture_v3",
            );
            assert.equal(await page.locator('#variant-select option[value^="lecture_v3"]').count(), 4);
            const response = page.waitForResponse(
                (r) => r.url().includes("/api/tdx-view?") && r.url().includes("sh.600009") && r.ok(),
            );
            await page.getByRole("combobox", { name: "股票", exact: true }).selectOption("sh.600009");
            await response;
            await loaded();
        });
        await test("real four-plan comparison streams rows, including costs and sample count", async () => {
            await page.getByRole("button", { name: "对比四组幅度", exact: true }).click();
            await page.waitForFunction(
                () => document.querySelectorAll("#ratio-results tr").length >= 1,
                {},
                { timeout: 120000 },
            );
            await page.waitForFunction(
                () => document.querySelector("#ratio-status").textContent.includes("四组对比结束"),
                {},
                { timeout: 240000 },
            );
            assert.equal(await page.locator("#ratio-results tr").count(), 4);
            assert.ok((await page.locator("#ratio-status").textContent()).includes("失败 0 组"));
            for (let i = 0; i < 4; i++) {
                const cells = await page.locator("#ratio-results tr").nth(i).locator("td").allTextContents();
                const variant = ["lecture_v3", "lecture_v3_d67_c33", "lecture_v3_d50_c50", "lecture_v3_d67_c50"][i];
                const view = responses.find((v) => v.variant === variant);
                assert.ok(view, variant);
                assert.equal(Number(cells[1]), view.backtest.counts.long_signals);
                assert.equal(Number(cells[3]), view.metrics.trades);
                assert.ok(view.backtest.strategy.buy_point_definition === "whole_flip_wave_v3");
            }
            await page
                .getByRole("region", { name: "幅度方案对比" })
                .screenshot({ path: path.join(output, "four-ratios.png") });
        });
        await test("review selected plan uses its actual ledger and V3 evidence", async () => {
            const response = page.waitForResponse(
                (r) => r.url().includes("/api/tdx-backtest?") && r.url().includes("lecture_v3_d50_c50") && r.ok(),
            );
            await page.locator("#ratio-results tr").nth(2).getByRole("button", { name: "查看 B/S" }).click();
            const view = await (await response).json();
            await loaded();
            assert.equal(
                await page.getByRole("combobox", { name: "策略版本", exact: true }).inputValue(),
                "lecture_v3_d50_c50",
            );
            assert.equal(
                await page.getByRole("combobox", { name: "结果口径", exact: true }).inputValue(),
                "tdx-backtest",
            );
            assert.equal(await page.locator("#ratio-results tr").count(), 4);
            assert.ok(view.orders.some((o) => o.side === "BUY" && o.status === "filled"));
            assert.ok(
                view.orders
                    .filter((o) => o.side === "BUY" && o.status === "filled")
                    .every((o) => o.entry_conditions.slice(0, 3).every((c) => c.passed)),
            );
            await page.getByRole("button", { name: "定位最近成交 ↗", exact: true }).click();
            await loaded();
            await page.waitForFunction(
                () => document.querySelector('#events .event-row[data-kind="fill"]'),
                {},
                { timeout: 120000 },
            );
            await page
                .locator('#events .event-row[data-kind="fill"]')
                .filter({ hasText: "B 买入成交" })
                .first()
                .click();
            assert.ok((await page.locator("#selection-info").textContent()).includes("整段锚点：L0"));
            await page.locator("#selection-info").screenshot({ path: path.join(output, "buy-evidence.png") });
        });
        await test("date context change removes stale comparisons and cancellation stops scheduling", async () => {
            await page.getByLabel("回测起点", { exact: true }).fill("2019-01-01");
            await page.getByLabel("回测起点", { exact: true }).dispatchEvent("change");
            assert.equal(await page.locator("#ratio-results tr").count(), 0);
            await page.route("**/api/tdx-backtest?**", (route) =>
                route.fulfill({
                    status: 503,
                    contentType: "application/json",
                    body: JSON.stringify({ error: "controlled test failure" }),
                }),
            );
            await page.getByRole("button", { name: "对比四组幅度", exact: true }).click();
            await page.waitForFunction(
                () => document.querySelector("#ratio-status").textContent.includes("四组对比结束"),
                {},
                { timeout: 30000 },
            );
            assert.ok((await page.locator("#ratio-status").textContent()).includes("失败 4 组"));
            assert.ok(!(await page.locator("#ratio-results").textContent()).includes("无平仓样本"));
            await page.unroute("**/api/tdx-backtest?**");
            // Leave the request pending until UI cancellation, without launching an engine job.
            let pending;
            await page.route("**/api/tdx-backtest?**", (route) => {
                pending = route;
            });
            await page.getByRole("button", { name: "对比四组幅度", exact: true }).click();
            await page.getByRole("button", { name: "取消对比", exact: true }).click();
            assert.ok((await page.locator("#ratio-status").textContent()).includes("已取消"));
            assert.ok(await page.getByRole("button", { name: "对比四组幅度", exact: true }).isEnabled());
            if (pending) await pending.abort().catch(() => {});
        });
        assert.deepEqual(errors, []);
        await context.tracing.stop();
    } catch (e) {
        results.push({ status: "FAIL", error: e.stack });
        console.error(e);
        process.exitCode = 1;
        await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
        await context.tracing.stop({ path: path.join(output, "trace.zip") });
    } finally {
        fs.writeFileSync(path.join(output, "report.json"), JSON.stringify(results, null, 2));
        console.log(output);
        await browser.close();
    }
})().catch((e) => {
    console.error(e);
    process.exitCode = 1;
});
