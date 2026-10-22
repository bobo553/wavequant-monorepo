import assert from "node:assert/strict";
import test from "node:test";
import { tradeReasonItems } from "../public/trade-reasons.js";

test("one-p breakout rejection explains the dated three-session window", () => {
    const text = tradeReasonItems({
        reason: "one_p_before_last_fall_high_breakout_no_entry",
        n_date: "2022-05-11", one_p_reached_date: "2022-06-10", one_p: 6.76,
        key_date: "2022-04-13", key_price: 7.67, breakout_date: "2022-06-29", observation_day: 2,
    }).join("\n");
    assert.match(text, /2022-05-11.*一饱/);
    assert.match(text, /2022-06-29.*2022-04-13/);
    assert.match(text, /第 2 个交易日/);
    assert.match(text, /随后两个交易日禁止买入和加仓/);
    assert.doesNotMatch(text, /undefined|NaN/);
});
