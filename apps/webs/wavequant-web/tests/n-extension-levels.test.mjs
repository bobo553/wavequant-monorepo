import assert from "node:assert/strict";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";

test("N extension levels only appear from their own availability date without changing the N date", () => {
    const view = {
        asof: "2026-08-07",
        bars: [{ time: "2026-07-27" }, { time: "2026-08-07" }],
        markers: [],
    };
    const event = {
        id: "n-july-27",
        event: "n_completed",
        direction: "up",
        time: "2026-07-27",
        available_at: "2026-07-27",
        price: 10,
        levels: [
            { name: "2T 投影", price: 12 },
            { name: "五顶投影", price: 15, available_at: "2026-08-07" },
            { name: "十满投影", price: 20, available_at: "2026-08-07" },
        ],
    };
    const theory = { asof: "2026-07-27", events: [event] };
    assert.deepEqual(
        buildAnnotations(view, theory)[0].levels.map((level) => level.name),
        ["2T 投影"],
    );
    const current = buildAnnotations(view, { ...theory, asof: "2026-08-07" })[0];
    assert.equal(current.time, "2026-07-27");
    assert.equal(current.levels.length, 3);
    assert.equal(event.levels.length, 3);
    assert.equal(
        buildAnnotations({ ...view, asof: "2026-07-27" }, { ...theory, asof: "2026-08-07" })[0].levels.length,
        1,
    );
});
