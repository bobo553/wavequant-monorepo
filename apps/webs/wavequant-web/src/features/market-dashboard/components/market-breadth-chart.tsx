"use client";

import { type JSX, useEffect, useMemo, useRef } from "react";

import { LineChart } from "echarts/charts";
import { AriaComponent, GridComponent, TooltipComponent } from "echarts/components";
import { type EChartsCoreOption, init, use as registerECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

import { chartSeries } from "@/features/market-dashboard/market-data";

registerECharts([LineChart, GridComponent, TooltipComponent, AriaComponent, CanvasRenderer]);

function buildMarketBreadthOption(): EChartsCoreOption {
    return {
        aria: { enabled: true },
        animation: false,
        color: ["#48d6c4"],
        grid: { top: 14, right: 16, bottom: 28, left: 52 },
        tooltip: { trigger: "axis", valueFormatter: (value: unknown) => `${String(value)}%` },
        xAxis: {
            type: "category",
            boundaryGap: false,
            data: [
                "09:30",
                "09:45",
                "10:00",
                "10:30",
                "11:00",
                "11:30",
                "13:00",
                "13:30",
                "14:00",
                "14:15",
                "14:30",
                "14:45",
                "15:00",
            ],
            axisLabel: { color: "#8291aa", interval: 2 },
            axisLine: { show: false },
            axisTick: { show: false },
        },
        yAxis: {
            type: "value",
            min: -6.6,
            max: 6.6,
            axisLabel: {
                color: "#8291aa",
                formatter: (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(1)}%`,
            },
            splitLine: { lineStyle: { color: "rgba(48, 67, 91, .45)" } },
        },
        series: [
            {
                type: "line",
                data: chartSeries,
                smooth: 0.25,
                showSymbol: false,
                lineStyle: { width: 2 },
                areaStyle: { color: "rgba(72, 214, 196, .08)" },
            },
        ],
    };
}

/** 绘制大盘等权涨幅时间序列，并随容器尺寸变化重排。 */
export function MarketBreadthChart(): JSX.Element {
    const chartRef = useRef<HTMLDivElement>(null);
    const option = useMemo(() => buildMarketBreadthOption(), []);

    useEffect(() => {
        if (!chartRef.current) return;
        const chart = init(chartRef.current);
        chart.setOption(option);
        const resizeObserver = new ResizeObserver(() => chart.resize());
        resizeObserver.observe(chartRef.current);
        return () => {
            resizeObserver.disconnect();
            chart.dispose();
        };
    }, [option]);

    return (
        <div
            ref={chartRef}
            role="img"
            aria-label="2026-09-07 盘中样本等权涨幅趋势，当前为正 6.04%"
            className="h-48 w-full min-w-0"
        />
    );
}
