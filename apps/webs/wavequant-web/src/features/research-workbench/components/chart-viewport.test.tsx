import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChartViewNavigation } from "./chart-view-navigation";
import { ChartViewport } from "./chart-viewport";

describe("chart keyboard entry and existing navigation controls", () => {
    it("exposes a keyboard focus target, help text and all existing runtime mounting points", () => {
        const host = document.createElement("div");
        host.innerHTML = renderToStaticMarkup(
            <>
                <ChartViewNavigation />
                <ChartViewport />
            </>,
        );
        const chart = host.querySelector<HTMLElement>("#price-chart");
        expect(chart?.tabIndex).toBe(0);
        expect(chart?.getAttribute("role")).toBe("region");
        expect(chart?.getAttribute("aria-keyshortcuts")).toBe("ArrowUp ArrowDown ArrowLeft ArrowRight");
        expect(chart?.getAttribute("aria-describedby")).toBe("chart-keyboard-help");
        expect(host.querySelector("#chart-keyboard-help")?.textContent).toContain("↑ 放大 · ↓ 缩小 · ← → 移动");
        for (const id of [
            "chart-pan-left",
            "chart-pan-right",
            "chart-zoom-in",
            "chart-zoom-out",
            "trade-playback-previous",
            "trade-playback-toggle",
            "trade-playback-next",
            "trade-playback-speed",
            "trade-playback-progress",
            "trade-playback-current",
            "trade-playback-announcement",
            "ohlc",
            "ohlc-text",
            "copy-candle",
            "candle-copy-feedback",
            "price-chart",
            "chart-position-slider",
            "chart-position-label",
            "chart-loading-overlay",
            "asof-label",
            "replay-mode",
            "previous",
            "replay-slider",
            "next",
            "latest",
        ]) {
            expect(host.querySelectorAll(`#${id}`), `${id} has one runtime owner`).toHaveLength(1);
        }
    });
});
