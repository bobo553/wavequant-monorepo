"use client";

import { type JSX, useEffect, useMemo, useRef } from "react";

import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
    Skeleton,
} from "@repo/design-system-web/components";
import { LineChart, PieChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { type EChartsCoreOption, init, use as registerECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

registerECharts([LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, CanvasRenderer]);

interface IChartDatum {
    label?: string;
    name?: string;
    value: number;
}

interface IDashboardChartProps {
    data: IChartDatum[];
    description: string;
    status?: "empty" | "error" | "loading" | "ready";
    title: string;
    type: "line" | "pie";
}

function buildLineOption(data: IChartDatum[]): EChartsCoreOption {
    return {
        aria: { enabled: true, decal: { show: true } },
        color: ["#625bf6"],
        grid: { top: 20, right: 12, bottom: 28, left: 42 },
        tooltip: { trigger: "axis", valueFormatter: (value: unknown) => `¥ ${String(value)}k` },
        xAxis: {
            type: "category",
            boundaryGap: false,
            data: data.map((item) => item.label),
            axisLine: { show: false },
            axisTick: { show: false },
        },
        yAxis: {
            type: "value",
            axisLabel: { formatter: "¥{value}k" },
            splitLine: { lineStyle: { color: "rgba(128, 128, 140, 0.16)" } },
        },
        series: [
            {
                type: "line",
                smooth: true,
                symbol: "circle",
                symbolSize: 7,
                data: data.map((item) => item.value),
                areaStyle: { color: "rgba(98, 91, 246, 0.12)" },
                lineStyle: { width: 3 },
            },
        ],
    };
}

function buildPieOption(data: IChartDatum[]): EChartsCoreOption {
    return {
        aria: { enabled: true, decal: { show: true } },
        color: ["#625bf6", "#16a394", "#f59e0b"],
        tooltip: { trigger: "item", valueFormatter: (value: unknown) => `${String(value)}%` },
        legend: { bottom: 0, icon: "circle", itemWidth: 8, itemHeight: 8 },
        series: [
            {
                type: "pie",
                radius: ["52%", "72%"],
                center: ["50%", "43%"],
                label: { formatter: "{d}%", fontWeight: 600, position: "inside", color: "#ffffff" },
                data: data.map((item) => ({ name: item.name, value: item.value })),
            },
        ],
    };
}

/** 渲染带加载、空和错误状态的经营图表。 */
export function DashboardChart({
    data,
    description,
    status = "ready",
    title,
    type,
}: IDashboardChartProps): JSX.Element {
    const chartRef = useRef<HTMLDivElement>(null);
    const option = useMemo(() => (type === "line" ? buildLineOption(data) : buildPieOption(data)), [data, type]);

    useEffect(() => {
        if (status !== "ready" || !chartRef.current) return;

        const chart = init(chartRef.current);
        chart.setOption(option);
        const resizeObserver = new ResizeObserver(() => chart.resize());
        resizeObserver.observe(chartRef.current);

        return () => {
            resizeObserver.disconnect();
            chart.dispose();
        };
    }, [option, status]);

    return (
        <Card>
            <CardHeader>
                <CardTitle>{title}</CardTitle>
                <CardDescription>{description}</CardDescription>
            </CardHeader>
            <CardContent>
                {status === "loading" ? <Skeleton className="h-72 w-full" /> : null}
                {status === "empty" ? (
                    <div className="border-surface-400 text-muted-foreground grid h-72 place-items-center rounded-lg border border-dashed text-sm">
                        当前筛选范围内暂无数据
                    </div>
                ) : null}
                {status === "error" ? (
                    <div
                        role="alert"
                        className="bg-destructive/5 text-destructive grid h-72 place-items-center rounded-lg px-6 text-center text-sm"
                    >
                        图表数据加载失败，请稍后重试
                    </div>
                ) : null}
                {status === "ready" ? (
                    <div ref={chartRef} role="img" aria-label={`${title}：${description}`} className="h-72 w-full" />
                ) : null}
            </CardContent>
        </Card>
    );
}
