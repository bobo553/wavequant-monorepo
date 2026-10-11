import { MarketBarsPageSchema, type TMarketBarsPage } from "@repo/contracts";
import { describe, expect, it } from "vitest";

function annualPage(): TMarketBarsPage {
    return {
        schema_version: 1,
        source: "akshare",
        symbol: "sz.000678",
        requested_asof: "2026-10-09",
        resolved_asof: "2026-10-09",
        timeframe: "1d",
        data_version: "a".repeat(64),
        year: 2026,
        next_year: 2024,
        has_more: true,
        available_years: [2026, 2024],
        history_start: "2024-01-02",
        history_end: "2026-10-09",
        total_bars: 2,
        view: {
            symbol: "sz.000678",
            asof: "2026-10-09",
            bars: [{ time: "2026-10-09", open: 7, high: 8, low: 6, close: 7.5, raw_close: 7.5, volume: 100 }],
            sessions: ["2026-10-09"],
            price_basis: "raw",
            data_version: "a".repeat(64),
            timeframe: "1d",
            metrics: null,
        },
    };
}

describe("annual market bars contract", () => {
    it("preserves view metadata and allows missing calendar years", () => {
        const parsed = MarketBarsPageSchema.parse(annualPage());
        expect(parsed.next_year).toBe(2024);
        expect(parsed.view.metrics).toBeNull();
    });

    it("rejects a different symbol, cutoff, version or timeframe", () => {
        for (const field of ["symbol", "asof", "data_version", "timeframe"] as const) {
            const page = annualPage();
            const replacements = {
                symbol: "sh.600000",
                asof: "2025-12-31",
                data_version: "b".repeat(64),
                timeframe: "1w",
            };
            Object.assign(page.view, { [field]: replacements[field] });
            expect(MarketBarsPageSchema.safeParse(page).success).toBe(false);
        }
    });

    it("rejects mixed years, duplicate dates and nonfinite prices", () => {
        const mixed = annualPage();
        mixed.view.bars[0]!.time = "2024-01-02";
        expect(MarketBarsPageSchema.safeParse(mixed).success).toBe(false);
        const duplicate = annualPage();
        duplicate.view.bars.push({ ...duplicate.view.bars[0]! });
        expect(MarketBarsPageSchema.safeParse(duplicate).success).toBe(false);
        const invalid = annualPage();
        invalid.view.bars[0]!.close = Number.NaN;
        expect(MarketBarsPageSchema.safeParse(invalid).success).toBe(false);
    });

    it("rejects skipped, repeated or inconsistent page cursors", () => {
        for (const update of [
            { next_year: 2025 },
            { next_year: 2026 },
            { has_more: false },
            { available_years: [2024, 2026] },
            { available_years: [2026, 2024, 2024] },
        ]) {
            expect(MarketBarsPageSchema.safeParse({ ...annualPage(), ...update }).success).toBe(false);
        }
    });

    it("accepts the oldest page and rejects a future history cutoff", () => {
        const oldest = annualPage();
        oldest.year = 2024;
        oldest.next_year = null;
        oldest.has_more = false;
        oldest.view.bars[0]!.time = "2024-01-02";
        oldest.view.sessions = ["2024-01-02"];
        expect(MarketBarsPageSchema.safeParse(oldest).success).toBe(true);
        oldest.history_end = "2026-10-12";
        expect(MarketBarsPageSchema.safeParse(oldest).success).toBe(false);
    });
});
