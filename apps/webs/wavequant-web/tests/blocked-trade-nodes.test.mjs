import assert from "node:assert/strict";
import test from "node:test";

import { blockedTradeNodes, formatBlockedTradeCopy, groupBlockedTradeNodes } from "../public/blocked-trade-nodes.js";

test("blocked nodes share one date card without losing screening or execution evidence", () => {
    const view = {
        markers: [
            {
                id: "order-1",
                kind: "order",
                status: "cancelled",
                time: "2026-09-17",
                price: 10.2,
                reason: "risk_budget_below_one_lot",
            },
            { id: "fill-1", kind: "fill", status: "filled", time: "2026-09-17", price: 10.2 },
        ],
        theory: {
            events: [
                {
                    id: "rule-1",
                    event: "entry_rejected",
                    time: "2026-09-17",
                    available_at: "2026-09-17",
                    price: 10,
                    reason: "not_squeeze_regime",
                },
                {
                    id: "rule-2",
                    event: "entry_preflight_rejected",
                    time: "2026-09-17",
                    available_at: "2026-09-17T14:30:00+08:00",
                    price: 10,
                    reason: "insufficient_close_gross_reward_risk",
                },
                {
                    id: "rule-3",
                    event: "entry_rejected",
                    time: "2026-09-16",
                    available_at: "2026-09-16",
                    price: 9.8,
                    reason: "not_squeeze_regime",
                },
                { id: "unrelated", event: "n_completed", time: "2026-09-17", available_at: "2026-09-17" },
            ],
        },
    };
    const nodes = blockedTradeNodes(view);
    const groups = groupBlockedTradeNodes(nodes);

    assert.equal(nodes.length, 4);
    assert.deepEqual(
        groups.map((group) => group.time),
        ["2026-09-17", "2026-09-16"],
    );
    assert.deepEqual(
        groups[0].nodes.map((node) => node.id),
        ["order-1", "rule-1", "rule-2"],
    );
    assert.equal(groups[0].primary.id, "order-1");
    assert.equal(groups[1].primary.id, "rule-3");
    assert.equal(view.theory.events.length, 4);
});

test("empty blocked history produces no date cards", () => {
    assert.deepEqual(groupBlockedTradeNodes(blockedTradeNodes({ markers: [], theory: { events: [] } })), []);
});

test("copy text retains each same-day rejection with stock, strategy, prices and reasons", () => {
    const view = {
        symbol: "sh.600519",
        variant: "lecture_v3",
        backtest: { start: "2026-01-01" },
        asof: "2026-09-17",
        bars: [{ time: "2026-09-16" }],
        markers: [
            {
                id: "order-1",
                kind: "order",
                status: "cancelled",
                time: "2026-09-17",
                signal_time: "2026-09-16",
                price: 10.2,
                raw_price: 10.1,
                reason: "risk_budget_below_one_lot",
                risk_budget: 100,
                one_lot_price_risk: 120,
            },
        ],
        theory: {
            events: [
                {
                    id: "rule-1",
                    event: "entry_rejected",
                    time: "2026-09-16",
                    available_at: "2026-09-17",
                    price: 10,
                    reason: "not_squeeze_regime",
                    attack: 0,
                },
                {
                    id: "rule-2",
                    event: "entry_preflight_rejected",
                    time: "2026-09-16",
                    available_at: "2026-09-17",
                    price: 9.9,
                    reason: "insufficient_close_gross_reward_risk",
                    gross_reward_risk: 1.2,
                    required_reward_risk: 2,
                },
            ],
        },
    };
    const groups = groupBlockedTradeNodes(blockedTradeNodes(view));
    const text = formatBlockedTradeCopy(view, groups, "第三类策略");

    assert.match(text, /贵州茅台（sh\.600519）/);
    assert.match(text, /策略：第三类策略/);
    assert.match(text, /被拦截：1 日 · 3 次/);
    assert.match(text, /执行委托未成交 · 拟价 10\.20 元/);
    assert.match(text, /单笔风险预算不足以买入一手/);
    assert.match(text, /策略候选未下单 · 收盘参考价 10\.00 元/);
    assert.match(text, /不是轧空／强轧空盘/);
    assert.match(text, /收盘收益风险比 1\.20，要求至少 2\.00/);
    assert.deepEqual([...text.matchAll(/事件 ID：/g)].length, 3);
    assert.equal(formatBlockedTradeCopy(view, [groups[0]], "第三类策略"), text);
});
