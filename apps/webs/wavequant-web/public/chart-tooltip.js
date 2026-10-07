/** 关闭提示框时同步退出浏览器顶层，保留原有 hidden 状态契约。 */
export function hideChartTooltip(tooltip) {
    if (typeof tooltip.hidePopover === "function" && tooltip.matches(":popover-open")) tooltip.hidePopover();
    tooltip.hidden = true;
}

/** 提示框以页面视口为边界；图表的容器查询和层叠隔离不能限制浮层。 */
export function bindChartTooltipLayer(container, tooltip, onHide) {
    const document = container.ownerDocument;
    const view = document.defaultView;
    const nativePopover = typeof tooltip.showPopover === "function" && typeof tooltip.hidePopover === "function";
    if (nativePopover) tooltip.setAttribute("popover", "manual");
    else {
        // 旧浏览器将卡片挂到工作台根部，避开 chart-card 的布局包含块并保留主题。
        const host = container.closest("[data-wavequant-react-workbench]") || document.body;
        host.append(tooltip);
    }

    const hideOnLayoutChange = (event) => {
        if (event.target instanceof view.Node && tooltip.contains(event.target)) return;
        onHide();
    };
    const hideOnEscape = (event) => {
        if (event.key === "Escape") onHide();
    };
    view.addEventListener("scroll", hideOnLayoutChange, true);
    view.addEventListener("resize", hideOnLayoutChange);
    view.addEventListener("keydown", hideOnEscape);
    view.visualViewport?.addEventListener("resize", hideOnLayoutChange);
    view.visualViewport?.addEventListener("scroll", hideOnLayoutChange);
    const resizeObserver = typeof view.ResizeObserver === "function" ? new view.ResizeObserver(onHide) : null;
    resizeObserver?.observe(container);

    return {
        show() {
            tooltip.hidden = false;
            if (nativePopover && !tooltip.matches(":popover-open")) tooltip.showPopover();
        },
        position(point) {
            const bounds = container.getBoundingClientRect();
            const viewport = view.visualViewport;
            const viewportLeft = viewport?.offsetLeft ?? 0;
            const viewportTop = viewport?.offsetTop ?? 0;
            const viewportWidth = viewport?.width ?? (document.documentElement.clientWidth || view.innerWidth);
            const viewportHeight = viewport?.height ?? (document.documentElement.clientHeight || view.innerHeight);
            const margin = 8;
            tooltip.style.setProperty("--chart-tooltip-max-width", `${Math.max(0, viewportWidth - margin * 2)}px`);
            tooltip.style.setProperty("--chart-tooltip-max-height", `${Math.max(0, viewportHeight - margin * 2)}px`);
            const width = tooltip.offsetWidth;
            const height = tooltip.offsetHeight;
            const anchorX = bounds.left + point.x;
            const anchorY = bounds.top + point.y;
            const maxLeft = viewportLeft + viewportWidth - width - margin;
            const maxTop = viewportTop + viewportHeight - height - margin;
            let left = anchorX + 16;
            if (left > maxLeft) left = anchorX - width - 16;
            tooltip.style.left = `${Math.max(viewportLeft + margin, Math.min(left, maxLeft))}px`;
            tooltip.style.top = `${Math.max(viewportTop + margin, Math.min(anchorY + 12, maxTop))}px`;
        },
        destroy() {
            resizeObserver?.disconnect();
            view.removeEventListener("scroll", hideOnLayoutChange, true);
            view.removeEventListener("resize", hideOnLayoutChange);
            view.removeEventListener("keydown", hideOnEscape);
            view.visualViewport?.removeEventListener("resize", hideOnLayoutChange);
            view.visualViewport?.removeEventListener("scroll", hideOnLayoutChange);
            hideChartTooltip(tooltip);
            tooltip.remove();
        },
    };
}
