import assert from "node:assert/strict";
import test from "node:test";

import { tradeReasonItems } from "../public/trade-reasons.js";

test("double-volume next lower close explains both dated volume comparisons without requiring another double", () => {
    const text = tradeReasonItems({
        reason: "volume_after_double_lower_close_clear", side: "SELL",
        previous_date: "2021-12-01", double_volume_reference_date: "2021-11-30",
        previous_volume: 3498200, double_volume_reference: 1419085,
        previous_volume_multiple: 3498200 / 1419085,
        observed_volume: 4680071, response_volume_multiple: 4680071 / 3498200,
        previous_close: 24.36864898843038, observed_close: 23.486930697815957,
    }).join("\n");
    assert.match(text, /2021-12-01.*2021-11-30.*2\.47/);
    assert.match(text, /1\.34.*23\.4869 < 前收 24\.3686/);
    assert.match(text, /清空剩余可卖仓位/);
    assert.doesNotMatch(text, /undefined|NaN|减仓.*70%/);
});
