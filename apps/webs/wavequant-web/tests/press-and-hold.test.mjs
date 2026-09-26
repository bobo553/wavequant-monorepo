import assert from "node:assert/strict";
import test from "node:test";

import { bindPressAndHold } from "../public/press-and-hold.js";

class Clock {
    now = 0;
    nextId = 1;
    tasks = new Map();

    setTimeout = (callback, delay) => this.add(callback, delay, null);
    setInterval = (callback, delay) => this.add(callback, delay, delay);
    clearTimeout = (id) => this.tasks.delete(id);
    clearInterval = (id) => this.tasks.delete(id);

    add(callback, delay, interval) {
        const id = this.nextId++;
        this.tasks.set(id, { callback, due: this.now + delay, interval });
        return id;
    }

    advance(duration) {
        const until = this.now + duration;
        while (true) {
            const next = [...this.tasks].sort((a, b) => a[1].due - b[1].due)[0];
            if (!next || next[1].due > until) break;
            const [id, task] = next;
            this.now = task.due;
            if (task.interval === null) this.tasks.delete(id);
            else task.due += task.interval;
            task.callback();
        }
        this.now = until;
    }
}

function pointerEvent(type, { pointerId = 1, clientX = 10, clientY = 10 } = {}) {
    return Object.assign(new Event(type, { cancelable: true }), {
        pointerId,
        clientX,
        clientY,
        button: 0,
        isPrimary: true,
    });
}

function click(button, detail = 1) {
    const event = Object.assign(new Event("click", { cancelable: true }), { detail });
    button.dispatchEvent(event);
    return event;
}

class Button extends EventTarget {
    disabled = false;
    capturedId = null;
    ownerDocument = Object.assign(new EventTarget(), {
        defaultView: new EventTarget(),
        visibilityState: "visible",
    });

    setPointerCapture(id) {
        this.capturedId = id;
    }

    hasPointerCapture(id) {
        return this.capturedId === id;
    }

    releasePointerCapture(id) {
        this.capturedId = null;
        this.dispatchEvent(pointerEvent("lostpointercapture", { pointerId: id }));
    }

    getBoundingClientRect() {
        return { left: 0, right: 34, top: 0, bottom: 32 };
    }
}

test("a quick pointer click acts once without a delayed repeat", () => {
    const button = new Button();
    const clock = new Clock();
    let actions = 0;
    bindPressAndHold(button, () => actions++, { scheduler: clock });

    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(200);
    button.dispatchEvent(pointerEvent("pointerup"));
    click(button);
    clock.advance(1000);

    assert.equal(actions, 1);
});

test("holding repeats after the delay and release does not add a click", () => {
    const button = new Button();
    const clock = new Clock();
    let actions = 0;
    bindPressAndHold(button, () => actions++, { scheduler: clock });

    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(399);
    assert.equal(actions, 0);
    clock.advance(1);
    clock.advance(300);
    assert.equal(actions, 3);
    button.dispatchEvent(pointerEvent("pointerup"));
    assert.equal(click(button).defaultPrevented, true);
    clock.advance(1000);
    assert.equal(actions, 3);
});

test("moving out, losing focus, and reaching a disabled boundary stop repetition", () => {
    const button = new Button();
    const clock = new Clock();
    let actions = 0;
    bindPressAndHold(button, () => actions++, { scheduler: clock });

    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(400);
    button.dispatchEvent(pointerEvent("pointermove", { clientX: 50 }));
    clock.advance(500);
    click(button);
    assert.equal(actions, 1);
    assert.equal(button.capturedId, null);

    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(400);
    button.ownerDocument.defaultView.dispatchEvent(new Event("blur"));
    clock.advance(500);
    assert.equal(actions, 2);

    button.dispatchEvent(pointerEvent("pointerdown"));
    button.disabled = true;
    clock.advance(500);
    assert.equal(actions, 2);
});

test("keyboard activation remains a single native click", () => {
    const button = new Button();
    let actions = 0;
    const unbind = bindPressAndHold(button, () => actions++);
    click(button, 0);
    assert.equal(actions, 1);
    unbind();
    click(button, 0);
    assert.equal(actions, 1);
});

test("reaching the chart boundary during a hold allows a later press", () => {
    const button = new Button();
    const clock = new Clock();
    let actions = 0;
    bindPressAndHold(
        button,
        () => {
            actions++;
            button.disabled = true;
        },
        { scheduler: clock },
    );

    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(900);
    assert.equal(actions, 1);
    button.disabled = false;
    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(400);
    assert.equal(actions, 2);
});

test("pointer cancellation and hidden page stop a hold", () => {
    const button = new Button();
    const clock = new Clock();
    let actions = 0;
    bindPressAndHold(button, () => actions++, { scheduler: clock });

    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(400);
    button.dispatchEvent(pointerEvent("pointercancel"));
    clock.advance(500);
    assert.equal(actions, 1);

    button.dispatchEvent(pointerEvent("pointerdown"));
    clock.advance(400);
    button.ownerDocument.visibilityState = "hidden";
    button.ownerDocument.dispatchEvent(new Event("visibilitychange"));
    clock.advance(500);
    assert.equal(actions, 2);
});
