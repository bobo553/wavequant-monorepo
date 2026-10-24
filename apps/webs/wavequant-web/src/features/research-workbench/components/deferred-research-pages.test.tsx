import { act, render, waitFor } from "@testing-library/react";
import { DeferredResearchPages } from "./deferred-research-pages";

function navigate(page: string): void {
    act(() => {
        window.history.replaceState(null, "", `?page=${page}`);
        window.dispatchEvent(new Event("wavequant:research-page-change"));
    });
}

describe("DeferredResearchPages", () => {
    it("mounts only visited sections, notifies after DOM commit and retains nodes across navigation", async () => {
        window.history.replaceState(null, "", "/research?page=workspace");
        const mounted: string[] = [];
        const ready = (event: Event): void => {
            const page = (event as CustomEvent<{ page: string }>).detail.page;
            expect(document.getElementById(`page-${page}`)).not.toBeNull();
            mounted.push(page);
        };
        window.addEventListener("wavequant:research-section-ready", ready);
        try {
            render(<DeferredResearchPages />);
            expect(document.getElementById("equity-chart")).toBeNull();
            expect(document.getElementById("orders-body")).toBeNull();
            expect(document.getElementById("health-body")).toBeNull();
            navigate("performance");
            await waitFor(() => expect(document.getElementById("equity-chart")).not.toBeNull());
            const chart = document.getElementById("equity-chart");
            expect(mounted).toContain("performance");
            navigate("orders");
            await waitFor(() => expect(document.getElementById("orders-body")).not.toBeNull());
            expect(document.getElementById("page-performance")).toHaveAttribute("hidden");
            expect(document.getElementById("health-body")).toBeNull();
            navigate("performance");
            expect(document.getElementById("equity-chart")).toBe(chart);
            expect(document.getElementById("page-performance")).not.toHaveAttribute("hidden");
        } finally { window.removeEventListener("wavequant:research-section-ready", ready); }
    });
});
