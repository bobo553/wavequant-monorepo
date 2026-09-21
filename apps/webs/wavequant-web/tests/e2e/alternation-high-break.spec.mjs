import { expect, test } from "@playwright/test";

test("Xidian high breakout confirms a solid tertiary segment on August 20", async ({ page }) => {
    test.setTimeout(240_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const catalog = {
        available: true,
        latest: "2026-09-21",
        with_daily: 1,
        stocks: [{ symbol: "sz.301130", name: "西点药业", has_data: true, last: "2026-09-21", source: "akshare" }],
    };
    await page.route("**/api/akshare-catalog", (route) => route.fulfill({ json: catalog }));
    await page.route("**/api/tdx-catalog", (route) => route.fulfill({ json: catalog }));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#selected-stock-summary")).toContainText("未回测", { timeout: 60_000 });
    await page.locator("#backtest-start").fill("2018-01-02");
    const response = page.waitForResponse((r) => r.url().includes("/api/akshare-backtest?"), { timeout: 180_000 });
    await page.getByRole("button", { name: "运行当前股票回测" }).click();
    const result = await response;
    expect(result.ok()).toBe(true);
    const view = await result.json();
    const segment = view.theory.tertiary_trends.strokes.find(
        (s) =>
            s.points[0]?.time === "2025-08-11" && s.points[1]?.time === "2026-07-21" && s.available_at === "2026-08-20",
    );
    expect(segment.kind).toBe("tertiary");
    expect(segment.points.every((p) => p.state === "confirmed" && p.available_at === "2026-08-20")).toBe(true);
    await expect(page.locator("#loading")).toBeHidden({ timeout: 120_000 });
    // Exercise the production renderer and point explanation with the real API segment.
    const rendered = await page.evaluate(async (stroke) => {
        const { LectureOverlay } = await import("/lecture-overlay.js");
        const overlay = new LectureOverlay(globalThis.document.createElement("div"));
        overlay.strokes = [stroke];
        overlay.projected = [
            { stroke, points: stroke.points.map((point, i) => ({ point, x: 20 + i * 100, y: 20 + i * 40 })) },
        ];
        const canvas = globalThis.document.createElement("canvas");
        const ctx = canvas.getContext("2d");
        const observed = [];
        const original = ctx.stroke.bind(ctx);
        ctx.stroke = () => {
            observed.push({ dash: ctx.getLineDash(), width: ctx.lineWidth });
            original();
        };
        const drawingTarget = {};
        drawingTarget["useMediaCoordinateSpace"] = (fn) => fn({ context: ctx });
        overlay.draw(drawingTarget);
        return { observed, explanation: overlay.annotation(`drawing:${stroke.id}:1`).description };
    }, segment);
    expect(rendered.observed).toEqual([{ dash: [], width: 3.5 }]);
    expect(rendered.explanation).toContain("2026-08-20");
    expect(rendered.explanation).toContain("正式三级趋势线");
    expect(rendered.explanation).toContain("不要求收盘越过旧高点");
    expect(errors).toEqual([]);
});
