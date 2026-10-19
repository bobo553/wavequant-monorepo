import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChartViewNavigation } from "./chart-view-navigation";
import { ChartViewport } from "./chart-viewport";

describe("chart keyboard entry and existing navigation controls", () => {
    it("exposes a keyboard focus target and existing runtime mounting points without the help paragraph", () => {
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
        expect(chart?.hasAttribute("aria-describedby")).toBe(false);
        expect(host.querySelector("#chart-keyboard-help")).toBeNull();
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
            "stock-data-load-status",
            "stock-data-load-message",
            "stock-data-load-retry",
            "asof-label",
            "replay-mode",
            "previous",
            "replay-slider",
            "next",
            "latest",
        ]) {
            expect(host.querySelectorAll(`#${id}`), `${id} has one runtime owner`).toHaveLength(1);
        }
        expect(host.querySelector("#stock-data-load-status")?.getAttribute("aria-live")).toBe("polite");
        expect(host.querySelector("#stock-data-load-retry")?.hasAttribute("hidden")).toBe(true);
    });
});
