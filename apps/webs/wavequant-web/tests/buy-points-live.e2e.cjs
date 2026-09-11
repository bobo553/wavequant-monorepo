/* Controlled slow-scan transport + real browser component. No fabricated market result is saved as research. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.DASHBOARD_URL || "http://127.0.0.1:8765";
const output = path.resolve("results/buy_points_live", new Date().toISOString().replaceAll(/[:.]/g, "-"));
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
    });
    const page = await browser.newPage({ viewport: { width: 800, height: 900 } });
    const errors = [],
        checks = [];
    page.on("pageerror", (e) => errors.push(e.message));
    let job,
        pending = [],
        requestCount = 0;
    const respond = (route) => route.fulfill({ json: structuredClone(job) });
    async function publish(fields) {
        Object.assign(job, fields, { revision: job.revision + 1 });
        const waiting = pending;
        pending = [];
        await Promise.all(waiting.map(respond));
    }
    const match = (symbol, priority) => ({
        symbol,
        name: "测试股票 " + symbol,
        signal_date: "2026-01-06",
        status: "awaiting_next_open",
        buy_point_type: priority === 2 ? "mature_shallow_squeeze" : "transition_squeeze",
        priority,
        trend_level: 1,
        regime: "轧空",
        raw_reference_price: 20,
        rvol: 2,
        gross_reward_risk: 2,
    });
    try {
        await page.route(base + "/scan-live-test", (route) =>
            route.fulfill({
                contentType: "text/html",
                body: `
      <meta charset="utf-8"><link rel="stylesheet" href="/styles.css"><main style="width:400px;padding:24px">
      <button id="all-stocks-tab">全部股票</button><button id="buy-points-tab">符合买点</button>
      <div id="stock-list"></div><div id="stock-search-controls"></div><section id="buy-points-panel">
      <select id="scan-lookback"><option value="1">当日</option></select><button id="scan-start">扫描买点</button>
      <button id="scan-cancel">取消扫描</button><p id="scan-status" role="status"></p><div id="buy-points-list"></div></section>
      <script type="module">import {BuyPoints} from '/buy-points.js';
      const params={run:'controlled-test',variant:'lecture_v2',scenario:'base',source:'tdx',asof:'2026-01-06',start:'2020-01-01'};
      window.liveTest=new BuyPoints({getContext:()=>params,onSelect:r=>{window.selected=r.symbol},
        api:async(path,p,signal,method='GET')=>{const r=await fetch(path+(method==='GET'?'?'+new URLSearchParams(p):''),
          {method,signal,headers:{'Content-Type':'application/json'},...(method==='POST'?{body:JSON.stringify(p)}:{})});if(!r.ok)throw Error('test HTTP '+r.status);return r.json()}});
      window.ready=true;</script></main>`,
            }),
        );
        await page.route(base + "/api/buy-scan**", async (route) => {
            requestCount++;
            const req = route.request();
            if (req.method() === "POST" && !req.url().includes("/cancel")) {
                job = {
                    id: "controlled-scan",
                    revision: 0,
                    params: JSON.parse(req.postData()),
                    status: "running",
                    total: 3,
                    processed: 0,
                    failed: 0,
                    stale: 0,
                    skipped: 0,
                    results: [],
                    errors: [],
                    error: null,
                    current: "sh.600000",
                    funnel: { stocks: {}, rejections: {} },
                    performance: { cache_hits: 0, recomputed: 0, elapsed_seconds: 0 },
                };
                return respond(route);
            }
            if (req.url().includes("/cancel")) {
                await publish({ status: "cancelled", current: null });
                return respond(route);
            }
            const after = Number(new URL(req.url()).searchParams.get("after"));
            if (job.revision > after || !["running", "cancelling"].includes(job.status)) return respond(route);
            pending.push(route);
        });
        await page.goto(base + "/scan-live-test");
        await page.waitForFunction(() => window.ready);
        await page.getByRole("button", { name: "扫描买点", exact: true }).click();
        await page.waitForFunction(() => document.querySelector("#scan-status").textContent.includes("扫描中"));
        await publish({ processed: 1, current: "sh.600001", results: [match("sh.600000", 1)] });
        await page.locator(".buy-point-item").waitFor();
        assert.match(await page.locator("#scan-status").textContent(), /扫描中.*1 \/ 3/);
        await page.evaluate(() => {
            window.firstNode = document.querySelector(".buy-point-item");
            window.firstNode.focus();
        });
        await page.locator(".buy-point-item").click();
        assert.equal(await page.evaluate(() => window.selected), "sh.600000");
        checks.push("first match visible and clickable at 1/3 while later stocks remain unfinished");
        await publish({
            processed: 2,
            current: "sh.600002",
            results: [match("sh.600000", 1), match("sh.600001", 2)],
            performance: { cache_hits: 1, recomputed: 1, elapsed_seconds: 4.2 },
        });
        await page.waitForFunction(() => document.querySelectorAll(".buy-point-item").length === 2);
        assert.equal(await page.locator(".buy-point-item").first().getAttribute("data-symbol"), "sh.600001");
        assert.ok(await page.evaluate(() => window.firstNode === document.querySelector('[data-symbol="sh.600000"]')));
        assert.ok(await page.evaluate(() => document.activeElement === window.firstNode));
        assert.match(await page.locator("#scan-status").textContent(), /缓存复用 1 只 · 新算 1 只 · 用时 4.2 秒/);
        checks.push("cache reuse, recomputation and elapsed time rendered without hiding live matches");
        checks.push("second match inserted with V2 priority; existing node and focus preserved");
        await page.locator("#buy-points-panel").screenshot({ path: path.join(output, "partial-2-of-3.png") });
        await page.getByRole("button", { name: "取消扫描", exact: true }).click();
        await page.waitForFunction(() => document.querySelector("#scan-status").textContent.includes("已取消"));
        assert.equal(await page.locator(".buy-point-item").count(), 2);
        checks.push("cancellation retains clearly labelled partial matches");
        await page.getByRole("button", { name: "扫描买点", exact: true }).click();
        await page.waitForFunction(() => document.querySelector("#scan-status").textContent.includes("扫描中"));
        await publish({ processed: 1, results: [match("sh.600000", 1)] });
        await page.locator(".buy-point-item").waitFor();
        await publish({ status: "failed", error: "controlled source-integrity failure", results: [] });
        await page.waitForFunction(() => document.querySelector("#scan-status").textContent.includes("结果作废"));
        assert.equal(await page.locator(".buy-point-item").count(), 0);
        checks.push("integrity failure revokes previously visible results");
        assert.deepEqual(errors, []);
        assert.ok(requestCount < 20);
        checks.push("no runtime errors or busy-poll loop");
        console.log(checks.map((s) => "PASS " + s).join("\n"));
    } catch (error) {
        await page.screenshot({ path: path.join(output, "failure.png") });
        throw error;
    } finally {
        fs.writeFileSync(path.join(output, "report.json"), JSON.stringify({ checks, errors, requestCount }, null, 2));
        await browser.close();
        console.log("Evidence: " + output);
    }
})().catch((e) => {
    console.error(e);
    process.exitCode = 1;
});
