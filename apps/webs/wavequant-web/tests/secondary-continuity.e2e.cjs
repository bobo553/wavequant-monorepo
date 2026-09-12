/* Real TDX API + TradingView canvas verification; never mutates theory evidence. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.DASHBOARD_URL || "http://127.0.0.1:8765";
const output = path.resolve("results/secondary_continuity", new Date().toISOString().replaceAll(/[:.]/g, "-"));
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
    });
    const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
    const errors = [],
        checks = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
        await page.goto(base + "/research");
        await page.waitForFunction(
            () => document.querySelector("#loading").hidden && document.querySelector("#error").hidden,
        );
        const result = await page.evaluate(async () => {
            const { secondaryConnections, reversalConnections } = await import("/lecture-overlay.js");
            const results = [];
            for (const symbol of ["sh.600009", "sh.600104", "sh.600519"]) {
                const response = await fetch(`/api/tdx-theory?symbol=${symbol}&asof=2026-09-07`);
                if (!response.ok) throw Error("TDX theory failed");
                const t = await response.json(),
                    before = JSON.stringify(t);
                const first = reversalConnections(t.reversal_trends.strokes, t.lecture_drawing.strokes);
                const links = secondaryConnections(t.secondary_trends.strokes, t.reversal_trends.strokes);
                results.push({
                    symbol,
                    paths: t.secondary_trends.strokes.length,
                    connections: links.length,
                    unchanged:
                        before === JSON.stringify(t) &&
                        JSON.stringify(first) ===
                            JSON.stringify(reversalConnections(t.reversal_trends.strokes, t.lecture_drawing.strokes)),
                    alternating: links.every((l) => l.points.slice(1).every((p, i) => p.kind !== l.points[i].kind)),
                    sourceOnly: links
                        .filter((l) => l.points.length === 3)
                        .every((l) =>
                            t.reversal_trends.strokes.some((s) =>
                                s.points.some(
                                    (p) =>
                                        p.index === l.points[1].index &&
                                        p.ordinal === l.points[1].ordinal &&
                                        p.kind === l.points[1].kind &&
                                        p.value === l.points[1].value,
                                ),
                            ),
                        ),
                });
                if (symbol === "sh.600009") window.airportTheory = t;
            }
            return results;
        });
        for (const r of result) {
            assert.equal(r.connections, r.paths - 1);
            assert.ok(r.unchanged && r.alternating && r.sourceOnly);
        }
        checks.push({
            name: "three real stocks: all secondary source gaps joined using level-one points only",
            result,
        });
        const target = await page.evaluate(async () => {
            const { PriceChart } = await import("/charts.js");
            const view = await (await fetch("/api/tdx-view?symbol=sh.600009&asof=2026-09-07")).json();
            const host = document.createElement("div");
            host.id = "secondary-live-chart";
            Object.assign(host.style, {
                position: "fixed",
                left: "10px",
                top: "10px",
                width: "1400px",
                height: "800px",
                zIndex: 99999,
                background: "#111c28",
            });
            document.body.append(host);
            const chart = new PriceChart(
                host,
                () => {},
                (items) => {
                    window.secondaryClick = items[0];
                },
            );
            chart.setData(view);
            chart.setTheory(window.airportTheory);
            chart.setTertiaryTrendVisible(false);
            chart.chart.timeScale().setVisibleRange({ from: "2022-05-01", to: "2022-10-20" });
            const refresh = async () => {
                await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
                chart.lectureOverlay.updateAllViews();
            };
            await refresh();
            const find = () =>
                chart.lectureOverlay.projected.find(
                    (s) => s.stroke.kind === "secondary-connection" && s.stroke.points[0].time === "2022-05-10",
                );
            const link = find();
            if (!link) throw Error("expected Shanghai Airport secondary gap not drawn");
            const signature = JSON.stringify(link.points.map((p) => [p.x, p.y]));
            chart.setTrendVisible(false);
            await refresh();
            const independent = !!find() && signature === JSON.stringify(find().points.map((p) => [p.x, p.y]));
            const hitPoints = [];
            for (let i = 1; i < link.points.length; i++) {
                const a = link.points[i - 1],
                    b = link.points[i];
                for (const f of [0.35, 0.5, 0.65]) {
                    const x = a.x + (b.x - a.x) * f,
                        y = a.y + (b.y - a.y) * f,
                        hit = chart.lectureOverlay.hitTest(x, y);
                    if (hit?.externalId.startsWith("drawing:" + link.stroke.id + ":")) {
                        hitPoints.push({ x: x + 10, y: y + 10 });
                        break;
                    }
                }
            }
            window.secondaryTest = { chart, host, refresh, find };
            return {
                independent,
                hitPoints,
                points: link.stroke.points.map((p) => [p.time, p.kind, p.value]),
                known: link.stroke.available_at,
            };
        });
        assert.deepEqual(target.points, [
            ["2022-05-10", "L", 45.75],
            ["2022-08-26", "H", 60.13],
            ["2022-09-05", "L", 54.16],
        ]);
        assert.ok(target.independent);
        assert.equal(target.hitPoints.length, 2);
        for (const p of target.hitPoints) {
            await page.mouse.click(p.x, p.y);
            await page.waitForFunction(() => window.secondaryClick?.raw?.trend_level === 2);
            const info = await page.evaluate(() => window.secondaryClick);
            assert.equal(info.raw.scope, "display_only_connection");
            assert.equal(info.time, target.known);
            assert.match(info.description, /经一级趋势线/);
        }
        checks.push({
            name: "real purple canvas L-H-L connector, both legs clickable, independent of level-one visibility",
            target,
        });
        await page
            .locator("#secondary-live-chart")
            .screenshot({ path: path.join(output, "airport-secondary-continuous.png") });
        const offscreen = await page.evaluate(async () => {
            const { chart, refresh, find } = window.secondaryTest;
            chart.chart.timeScale().setVisibleRange({ from: "2022-06-01", to: "2022-07-01" });
            await refresh();
            const link = find();
            const crossing = !!link && link.points.every((p) => p.x !== null && p.y !== null);
            chart.setSecondaryTrendVisible(false);
            await refresh();
            const hidden = Number(window.secondaryTest.host.dataset.secondaryConnections) === 0;
            return { crossing, hidden };
        });
        assert.ok(offscreen.crossing && offscreen.hidden);
        checks.push({ name: "offscreen anchors still connect through viewport; secondary toggle hides connections" });
        assert.deepEqual(errors, []);
        console.log(checks.map((c) => "PASS " + c.name).join("\n"));
    } catch (error) {
        await page.screenshot({ path: path.join(output, "failure.png") });
        throw error;
    } finally {
        fs.writeFileSync(path.join(output, "report.json"), JSON.stringify({ checks, errors }, null, 2));
        await browser.close();
        console.log("Evidence: " + output);
    }
})().catch((e) => {
    console.error(e);
    process.exitCode = 1;
});
