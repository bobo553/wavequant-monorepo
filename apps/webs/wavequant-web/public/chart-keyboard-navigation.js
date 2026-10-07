export function bindChartKeyboardNavigation(container, chart, onNavigate = () => {}) {
    const originalTabIndex = container.getAttribute("tabindex");
    const doc = container.ownerDocument;
    let pointerInside = false;
    if (originalTabIndex === null) container.tabIndex = 0;
    const controls =
        'input, textarea, select, button, a, summary, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="slider"], [role="listbox"], [role="menu"], [role="menuitem"], [role="tab"], [role="button"], [role="checkbox"], [role="radio"], [role="spinbutton"]';
    const isControl = (element) => Boolean(element?.closest?.(controls));
    const commands = new Map([
        ["ArrowUp", () => chart.zoom("in")],
        ["ArrowDown", () => chart.zoom("out")],
        ["ArrowLeft", () => chart.pan(-1)],
        ["ArrowRight", () => chart.pan(1)],
    ]);
    // SDK 会在 mousedown/touchstart 移除焦点，须在交互结束后恢复键盘入口。
    const focusCanvas = (event) => {
        if (event.button === 0 && event.target?.tagName === "CANVAS") container.focus({ preventScroll: true });
    };
    const enter = () => {
        pointerInside = true;
    };
    const leave = () => {
        pointerInside = false;
    };
    const onKeyDown = (event) => {
        if (
            !container.isConnected ||
            container.closest("[hidden], [inert]") ||
            event.defaultPrevented ||
            event.isComposing ||
            event.altKey ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey
        )
            return;
        const active = doc.activeElement;
        const ownsFocus = container.contains(active) || container.contains(event.target);
        const pageFocus = active === doc.body || active === doc.documentElement || active === null;
        if (!ownsFocus && !(pointerInside && pageFocus)) return;
        if (isControl(active) || event.composedPath().some(isControl)) return;
        const navigate = commands.get(event.key);
        if (!navigate || !chart.navigationState()) return;
        event.preventDefault();
        onNavigate();
        navigate();
    };
    container.addEventListener("pointerup", focusCanvas);
    container.addEventListener("click", focusCanvas);
    container.addEventListener("pointerenter", enter);
    container.addEventListener("pointermove", enter);
    container.addEventListener("pointerleave", leave);
    // 画布内部可能截断冒泡，按区域焦点在捕获阶段处理四向键。
    doc.addEventListener("keydown", onKeyDown, true);
    return () => {
        container.removeEventListener("pointerup", focusCanvas);
        container.removeEventListener("click", focusCanvas);
        container.removeEventListener("pointerenter", enter);
        container.removeEventListener("pointermove", enter);
        container.removeEventListener("pointerleave", leave);
        doc.removeEventListener("keydown", onKeyDown, true);
        if (originalTabIndex === null) container.removeAttribute("tabindex");
    };
}
