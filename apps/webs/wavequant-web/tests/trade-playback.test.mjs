import assert from "node:assert/strict";
import test from "node:test";

import { TradePlayback, filledTradePlaybackEvents } from "../public/trade-playback.js";

const buy = { id: "buy", kind: "fill", side: "BUY", time: "2024-01-03", price: 10 };
const sell = { id: "sell", kind: "fill", side: "SELL", time: "2024-01-05", price: 11 };
const add = { id: "add", kind: "fill", side: "BUY", time: "2024-01-03", price: 10.5 };
const view = { result_scope: "stock", backtest: { status: "completed" }, markers: [sell, buy, add] };

function playback() {
    const selected = [];
    const pending = new Map();
    let nextId = 0;
    const subject = new TradePlayback({
        onSelect: (marker) => selected.push(marker.id),
        onChange() {},
        schedule: (callback, delay) => {
            const id = ++nextId;
            pending.set(id, { callback, delay });
            return id;
        },
        cancel: (id) => pending.delete(id),
    });
    const tick = () => {
        const [id, timer] = pending.entries().next().value;
        pending.delete(id);
        timer.callback();
    };
    return { subject, selected, pending, tick };
}

test("playback uses actual fills in chronological order and ignores signals", () => {
    assert.deepEqual(
        filledTradePlaybackEvents(view).map(({ id }) => id),
        ["buy", "add", "sell"],
    );
    assert.deepEqual(filledTradePlaybackEvents({ ...view, markers: [{ id: "signal", kind: "signal" }] }), []);
    assert.deepEqual(filledTradePlaybackEvents({ ...view, result_scope: "akshare" }), []);
    assert.deepEqual(
        filledTradePlaybackEvents({
            ...view,
            markers: [{ ...buy, timestamp: "2024-01-03T15:00:00" }, { ...add, timestamp: "2024-01-03T14:00:00" }, sell],
        }).map(({ id }) => id),
        ["add", "buy", "sell"],
    );
});

test("pause retains the current fill and resume continues with the next fill", () => {
    const { subject, selected, pending, tick } = playback();
    subject.setView(view);
    assert.equal(subject.play(), true);
    assert.deepEqual(selected, ["buy"]);
    assert.equal(subject.snapshot().playing, true);
    assert.equal(pending.size, 1);

    subject.pause();
    assert.equal(subject.snapshot().index, 0);
    assert.equal(pending.size, 0);
    subject.play();
    assert.deepEqual(selected, ["buy"]);
    tick();
    assert.deepEqual(selected, ["buy", "add"]);
    tick();
    assert.deepEqual(selected, ["buy", "add", "sell"]);
    assert.equal(subject.snapshot().finished, true);
    assert.equal(pending.size, 0);

    subject.play();
    assert.deepEqual(selected, ["buy", "add", "sell", "buy"]);
});

test("manual stepping, speed changes and view changes cancel stale playback", () => {
    const { subject, selected, pending, tick } = playback();
    subject.setView(view);
    subject.play();
    assert.equal(subject.setSpeed(800), true);
    assert.equal(pending.values().next().value.delay, 800);
    assert.equal(subject.setSpeed(0), false);
    assert.equal(subject.step(1), true);
    assert.equal(subject.snapshot().playing, false);
    assert.equal(pending.size, 0);
    assert.deepEqual(selected, ["buy", "add"]);
    subject.step(-1);
    assert.equal(subject.snapshot().current.id, "buy");
    subject.step(99);
    assert.equal(subject.snapshot().current.id, "sell");
    assert.equal(subject.snapshot().finished, false);
    subject.step(-1);

    subject.play();
    tick();
    subject.setView({ ...view, markers: [] });
    assert.equal(subject.snapshot().total, 0);
    assert.equal(subject.snapshot().index, -1);
    assert.equal(pending.size, 0);
    assert.equal(subject.play(), false);
});

test("a selection callback can pause playback before another timer is scheduled", () => {
    const pending = new Map();
    let subject;
    subject = new TradePlayback({
        onSelect: () => subject.pause(),
        onChange() {},
        schedule: (callback) => {
            pending.set(1, callback);
            return 1;
        },
        cancel: (id) => pending.delete(id),
    });
    subject.setView(view);
    subject.play();
    assert.equal(subject.snapshot().index, 0);
    assert.equal(subject.snapshot().playing, false);
    assert.equal(pending.size, 0);
});
