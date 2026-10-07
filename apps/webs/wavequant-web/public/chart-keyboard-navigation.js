export function bindChartKeyboardNavigation(container, chart, onNavigate = () => {}) {
    const originalTabIndex = container.getAttribute("tabindex");
    if (originalTabIndex === null) container.tabIndex = 0;
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
    const onKeyDown = (event) => {
        if (
            event.target !== container ||
            event.defaultPrevented ||
            event.isComposing ||
            event.altKey ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey
        )
            return;
        const navigate = commands.get(event.key);
        if (!navigate || !chart.navigationState()) return;
        event.preventDefault();
        onNavigate();
        navigate();
    };
    container.addEventListener("pointerup", focusCanvas);
    container.addEventListener("click", focusCanvas);
    container.addEventListener("keydown", onKeyDown);
    return () => {
        container.removeEventListener("pointerup", focusCanvas);
        container.removeEventListener("click", focusCanvas);
        container.removeEventListener("keydown", onKeyDown);
        if (originalTabIndex === null) container.removeAttribute("tabindex");
    };
}
