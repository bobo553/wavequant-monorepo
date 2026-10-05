const MIN_CHART_HEIGHT = 200;
const VIEWPORT_BOTTOM_GAP = 16;
const CHART_HEIGHT_INCREASE = 100;

export function chartViewportHeight(viewportHeight, chartTop, footerHeight) {
    return (
        Math.max(MIN_CHART_HEIGHT, Math.floor(viewportHeight - chartTop - footerHeight - VIEWPORT_BOTTOM_GAP)) +
        CHART_HEIGHT_INCREASE
    );
}

/** 使用文档起点预留进度区，滚动页面不会使图表重新膨胀。 */
export function bindChartViewportHeight(chart, footers, layout) {
    let pendingFrame = 0;
    const update = () => {
        pendingFrame = 0;
        const bounds = chart.getBoundingClientRect();
        if (!bounds.width) return;
        const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
        const height = chartViewportHeight(
            viewportHeight,
            Math.max(0, bounds.top + window.scrollY),
            footers.reduce((total, footer) => total + footer.getBoundingClientRect().height, 0),
        );
        const value = `${height}px`;
        if (chart.style.getPropertyValue("--chart-viewport-height") !== value)
            chart.style.setProperty("--chart-viewport-height", value);
    };
    const schedule = () => {
        if (!pendingFrame) pendingFrame = window.requestAnimationFrame(update);
    };
    const observer = new ResizeObserver(schedule);
    for (const element of [layout, chart, ...footers]) observer.observe(element);
    window.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("resize", schedule);
    schedule();
    return () => {
        observer.disconnect();
        window.cancelAnimationFrame(pendingFrame);
        window.removeEventListener("resize", schedule);
        window.visualViewport?.removeEventListener("resize", schedule);
    };
}
