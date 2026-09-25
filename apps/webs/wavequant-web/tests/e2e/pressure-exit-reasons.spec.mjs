import { expect, test } from "@playwright/test";

test("pressure exits render distinct reduction and clear evidence in the browser", async ({ page }) => {
    await page.goto("/research", { waitUntil: "domcontentloaded" });
    const details = await page.evaluate(async () => {
        const { appendTradeEvidence } = await import("/trade-review.js");
        const { formatFilledTradeCopy } = await import("/filled-trade-copy.js");
        const base = {
            kind: "fill",
            side: "SELL",
            status: "filled",
            time: "2022-06-27",
            price: 7.12,
            raw_price: 6.92,
            adjustment_factor: 7.12 / 6.92,
            execution_model: "same_day_close",
            pressure_date: "2022-04-13",
            pressure_low: 7.2992,
            pressure_high: 7.6698,
            pressure_volume_multiple: 3.21,
            pressure_n_date: "2022-06-24",
            pressure_adverse_patterns: ["long_upper_shadow"],
        };
        const cases = [
            {
                ...base,
                reason: "pressure_gap_adverse_reduce",
                observed_low: 7.0007,
                previous_high: 6.9801,
                observed_close: 7.1242,
                previous_close: 6.9801,
            },
            {
                ...base,
                reason: "pressure_reduced_lower_close_clear",
                pressure_warning_date: "2022-06-27",
                pressure_adverse_patterns: ["close_below_previous"],
                observed_close: 7.4228,
                previous_close: 7.6184,
            },
            {
                ...base,
                reason: "pressure_breakout_adverse_clear",
                pressure_n_date: undefined,
                pressure_breakout_date: "2022-06-29",
                pressure_rally_low_date: "2022-04-27",
                pressure_rally_low: 5.22,
                pressure_rally_fraction: 0.507,
                pressure_adverse_patterns: ["bearish_body"],
            },
            {
                ...base,
                pressure_date: undefined,
                reason: "record_high_resistance_reduce",
                record_high_date: "2021-09-10",
                record_high: 7.0007,
                record_high_age: 97,
                record_breakout_date: "2022-02-11",
                record_upper_shadow_fraction: 0.796,
                record_adverse_patterns: ["close_below_previous", "long_upper_shadow"],
            },
            {
                ...base,
                pressure_date: undefined,
                reason: "record_high_lower_close_clear",
                record_high_date: "2021-09-10",
                record_high: 7.0007,
                record_high_age: 97,
                record_breakout_date: "2022-02-11",
                record_resistance_dates: ["2022-02-11", "2022-02-14"],
                observed_close: 6.5683,
                previous_close: 7.1036,
            },
        ];
        return cases.map((marker) => {
            const panel = globalThis.document.createElement("section");
            globalThis.document.body.append(panel);
            appendTradeEvidence(panel, marker);
            const detail = panel.textContent;
            const copy = formatFilledTradeCopy(
                {
                    symbol: "sz.000978",
                    variant: "lecture_v3",
                    backtest: { start: "2018-01-02" },
                    asof: "2022-07-01",
                    bars: [],
                },
                marker,
                "V3",
                "5%",
                null,
            );
            panel.remove();
            return { detail, copy };
        });
    });
    expect(details[0].detail).toContain("减仓 50%");
    expect(details[0].detail).toContain("减仓依据");
    expect(details[0].copy).toContain("减仓依据");
    expect(details[1].detail).toContain("2022-06-27 减仓后首次收跌");
    expect(details[1].detail).toContain("清仓依据");
    expect(details[1].copy).toContain("跳空减仓：2022-06-27");
    expect(details[2].detail).toContain("2022-06-29");
    expect(details[2].detail).toContain("2022-04-27");
    expect(details[2].copy).toContain("空头抵抗突破：2022-06-29");
    expect(details[3].detail).toContain("2021-09-10 阳线最高");
    expect(details[3].copy).toContain("目标减仓 50%，按整手执行");
    expect(details[4].detail).toContain("2022-02-11、2022-02-14");
    expect(details[4].copy).toContain("清空余仓");
});
