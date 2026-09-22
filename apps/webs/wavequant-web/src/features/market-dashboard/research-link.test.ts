import { stockResearchHref } from "./research-link";

describe("stockResearchHref", () => {
    it("keeps leading zeros and routes Shanghai, Shenzhen and Beijing stocks", () => {
        for (const [code, symbol] of [
            ["001216", "sz.001216"],
            ["600519", "sh.600519"],
            ["688001", "sh.688001"],
            ["300154", "sz.300154"],
            ["920001", "bj.920001"],
        ]) {
            const url = new URL(stockResearchHref(code!, "2026-09-18"), "http://localhost");
            expect(url.pathname).toBe("/research");
            expect(url.searchParams.get("symbol")).toBe(symbol);
            expect(url.searchParams.get("asof")).toBe("2026-09-18");
        }
    });

    it("rejects invalid symbols instead of linking to another security", () => {
        expect(() => stockResearchHref("bad", "2026-09-18")).toThrow();
    });
});
