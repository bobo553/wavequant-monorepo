import { performanceChartRuntime } from "./charts.js";
const { base, themePalette, withOpacity, themedSeries, L } = performanceChartRuntime;

export class PerformanceCharts {
    constructor(containers) {
        this.instances = containers.map((c, i) => {
            const chart = base(c);
            // Daily portfolio histories must fit even in a narrow half-width panel.
            chart.applyOptions({ timeScale: { minBarSpacing: 0.01 } });
            const palette = themePalette();
            const series = chart.addSeries(L.AreaSeries, {
                lineColor: i === 1 ? "#ec7c8a" : i === 2 ? "#829ddd" : palette.accent,
                topColor: i === 1 ? "#ec7c8a30" : i === 2 ? "#829ddd30" : withOpacity(palette.accent, 0.19),
                bottomColor: "#00000000",
                lineWidth: 2,
                priceFormat: { type: "custom", formatter: (p) => (i ? `${p.toFixed(2)}%` : p.toFixed(4)) },
            });
            themedSeries.add({ index: i, series });
            chart.timeScale().subscribeSizeChange(() => chart.timeScale().fitContent());
            chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
                if (range) {
                    c.dataset.visibleFromIndex = range.from;
                    c.dataset.visibleToIndex = range.to;
                }
            });
            return { chart, series };
        });
    }
    update(curve, holdingCurve = []) {
        ["value", "drawdown", "exposure"].forEach((key, i) => {
            this.instances[i].series.setData(
                i === 1 ? holdingCurve : curve.map((r) => ({ time: r.time, value: r[key] })),
            );
            this.instances[i].chart.timeScale().fitContent();
        });
    }
    resize() {
        this.instances.forEach(({ chart }) => chart.timeScale().fitContent());
    }
}
