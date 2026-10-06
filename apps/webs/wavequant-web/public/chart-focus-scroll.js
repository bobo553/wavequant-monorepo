/** 定位时保留收益指标，并为应用壳的固定导航预留实际高度。 */
export function scrollChartWithMetrics(chart, { behavior = "instant" } = {}) {
    const doc = chart.ownerDocument;
    const metrics = doc.getElementById("metric-return")?.closest(".metric-grid");
    const target = metrics ?? chart.closest(".chart-card") ?? chart;
    const header = doc.querySelector('.wavequant-shell[data-shell-variant="research"] header');
    target.style.scrollMarginTop = `${(header?.getBoundingClientRect().height ?? 0) + 12}px`;
    target.scrollIntoView({ block: "start", inline: "nearest", behavior });
}
