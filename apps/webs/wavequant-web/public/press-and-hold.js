/** 给原生按钮增加长按重复；单击仍由 click 负责，键盘与辅助技术保持原生激活方式。 */
export function bindPressAndHold(button, action, { scheduler = globalThis, delay = 400, interval = 150 } = {}) {
    const controller = new AbortController();
    const documentTarget = button.ownerDocument;
    let pointerId = null;
    let delayTimer = null;
    let repeatTimer = null;
    let suppressPointerClick = false;

    function clearTimers() {
        if (delayTimer !== null) scheduler.clearTimeout(delayTimer);
        if (repeatTimer !== null) scheduler.clearInterval(repeatTimer);
        delayTimer = null;
        repeatTimer = null;
    }

    function stop() {
        clearTimers();
        pointerId = null;
    }

    function cancel() {
        if (pointerId === null) return;
        suppressPointerClick = true;
        stop();
    }

    function repeat() {
        if (button.disabled) {
            stop();
            return;
        }
        suppressPointerClick = true;
        action();
        if (button.disabled) stop();
    }

    button.addEventListener(
        "pointerdown",
        (event) => {
            if (pointerId !== null || button.disabled || !event.isPrimary || event.button !== 0) return;
            suppressPointerClick = false;
            pointerId = event.pointerId;
            button.setPointerCapture(pointerId);
            delayTimer = scheduler.setTimeout(() => {
                delayTimer = null;
                if (pointerId === null) return;
                repeat();
                if (pointerId !== null && !button.disabled) repeatTimer = scheduler.setInterval(repeat, interval);
            }, delay);
        },
        { signal: controller.signal },
    );

    button.addEventListener(
        "pointermove",
        (event) => {
            if (event.pointerId !== pointerId) return;
            const bounds = button.getBoundingClientRect();
            const margin = 8;
            if (
                event.clientX >= bounds.left - margin &&
                event.clientX <= bounds.right + margin &&
                event.clientY >= bounds.top - margin &&
                event.clientY <= bounds.bottom + margin
            )
                return;
            const capturedId = pointerId;
            cancel();
            if (button.hasPointerCapture(capturedId)) button.releasePointerCapture(capturedId);
        },
        { signal: controller.signal },
    );

    button.addEventListener(
        "pointerup",
        (event) => {
            if (event.pointerId === pointerId) stop();
        },
        { signal: controller.signal },
    );
    button.addEventListener(
        "pointercancel",
        (event) => {
            if (event.pointerId === pointerId) cancel();
        },
        { signal: controller.signal },
    );
    button.addEventListener(
        "lostpointercapture",
        (event) => {
            if (event.pointerId === pointerId) cancel();
        },
        { signal: controller.signal },
    );
    button.addEventListener(
        "contextmenu",
        (event) => {
            if (pointerId !== null) event.preventDefault();
        },
        { signal: controller.signal },
    );
    button.addEventListener(
        "click",
        (event) => {
            if (suppressPointerClick && event.detail > 0) {
                suppressPointerClick = false;
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
            if (!button.disabled) action();
        },
        { signal: controller.signal },
    );

    documentTarget.defaultView.addEventListener("blur", cancel, { signal: controller.signal });
    documentTarget.addEventListener(
        "visibilitychange",
        () => {
            if (documentTarget.visibilityState === "hidden") cancel();
        },
        { signal: controller.signal },
    );

    return () => {
        stop();
        controller.abort();
    };
}
