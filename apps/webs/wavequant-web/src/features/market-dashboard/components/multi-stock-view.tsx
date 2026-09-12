"use client";

import { type JSX, useState } from "react";

import { Button, Card, CardContent, CardHeader, CardTitle } from "@repo/design-system-web/components";

import { stockQuotes } from "@/features/market-dashboard/market-data";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const gridClasses = { 4: "lg:grid-cols-2", 6: "lg:grid-cols-3", 9: "lg:grid-cols-3" } as const;
const slotNames = [
    "slot-one",
    "slot-two",
    "slot-three",
    "slot-four",
    "slot-five",
    "slot-six",
    "slot-seven",
    "slot-eight",
    "slot-nine",
] as const;

/** 提供 4/6/9 宫格、单屏换股、公共百分比轴和联动观察游标。 */
export function MultiStockView(): JSX.Element {
    const workspace = useMarketWorkspace();
    const [gridSize, setGridSize] = useState<4 | 6 | 9>(4);
    const [cursor, setCursor] = useState(8);
    const visibleStocks = Array.from({ length: gridSize }, (_, index) => ({
        code: workspace.multiCodes[index] ?? stockQuotes[index % stockQuotes.length]?.code ?? "SIM001",
        slot: slotNames[index] ?? `slot-${index + 1}`,
    }));
    const points = [2, 5, 4, 8, 7, 11, 13, 12, 17, 19, 21, 24];
    return (
        <div className="space-y-3">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h2 className="text-lg font-bold">多股同屏 · 统一比例刻度</h2>
                    <p className="text-muted-foreground mt-1 text-[10px]">
                        所有图共用 {workspace.date} {workspace.time} 观察时点和 ±36% 百分比轴。
                    </p>
                </div>
                <div className="flex gap-2">
                    {([4, 6, 9] as const).map((size) => (
                        <Button
                            key={size}
                            variant={gridSize === size ? "secondary" : "outline"}
                            onClick={() => setGridSize(size)}
                        >
                            {size} 图
                        </Button>
                    ))}
                </div>
            </header>
            <label className="border-border bg-card flex items-center gap-3 rounded-lg border px-4 py-3 text-xs">
                <span className="text-muted-foreground">同步悬停时刻</span>
                <input
                    aria-label="多股同屏联动时刻"
                    type="range"
                    min="0"
                    max={points.length - 1}
                    value={cursor}
                    onChange={(event) => setCursor(Number(event.target.value))}
                    className="market-range flex-1"
                />
                <strong>
                    {9 + Math.floor(cursor / 2)}:{cursor % 2 ? "30" : "00"}
                </strong>
            </label>
            <div className={`grid gap-3 sm:grid-cols-2 ${gridClasses[gridSize]}`}>
                {visibleStocks.map(({ code, slot }, index) => {
                    const stock = stockQuotes.find((item) => item.code === code) ?? stockQuotes[0];
                    if (!stock) return null;
                    const shifted = points.map((point, pointIndex) =>
                        Math.min(34, point + ((index * 3 + pointIndex) % 7) - 3),
                    );
                    const polyline = shifted
                        .map((point, pointIndex) => `${(pointIndex / (shifted.length - 1)) * 100},${42 - point}`)
                        .join(" ");
                    return (
                        <Card key={slot} className="rounded-lg">
                            <CardHeader className="flex-row items-center justify-between">
                                <div>
                                    <CardTitle>{stock.name}</CardTitle>
                                    <p className="text-muted-foreground text-[10px]">
                                        {stock.code} · {stock.board}
                                    </p>
                                </div>
                                <select
                                    aria-label={`更换第 ${index + 1} 屏股票`}
                                    value={stock.code}
                                    onChange={(event) => workspace.replaceMultiStock(index, event.target.value)}
                                    className="border-border bg-background h-8 max-w-28 rounded-md border px-2 text-[10px]"
                                >
                                    {stockQuotes.map((item) => (
                                        <option key={item.code} value={item.code}>
                                            {item.name}
                                        </option>
                                    ))}
                                </select>
                            </CardHeader>
                            <CardContent>
                                <div className="flex items-baseline justify-between">
                                    <strong className="text-xl text-rose-400">{stock.change}</strong>
                                    <span className="text-muted-foreground text-[10px]">公共轴 -36% ～ +36%</span>
                                </div>
                                <svg
                                    viewBox="0 0 100 48"
                                    className="mt-3 h-28 w-full"
                                    role="img"
                                    aria-label={`${stock.name}分时百分比走势`}
                                >
                                    <line
                                        x1="0"
                                        y1="24"
                                        x2="100"
                                        y2="24"
                                        stroke="currentColor"
                                        className="text-border"
                                        strokeDasharray="2 2"
                                    />
                                    <polyline points={polyline} fill="none" stroke="var(--primary)" strokeWidth="1.4" />
                                    <line
                                        x1={(cursor / (points.length - 1)) * 100}
                                        y1="0"
                                        x2={(cursor / (points.length - 1)) * 100}
                                        y2="48"
                                        stroke="var(--foreground)"
                                        strokeWidth="0.5"
                                        opacity="0.65"
                                    />
                                </svg>
                                <Button variant="ghost" size="sm" onClick={() => workspace.openStock(stock.code)}>
                                    打开个股证据
                                </Button>
                            </CardContent>
                        </Card>
                    );
                })}
            </div>
        </div>
    );
}
