"use client";

import type { JSX } from "react";

import { Card, CardContent, CardHeader } from "@repo/design-system-web/components";

import { indexQuotes } from "@/features/market-dashboard/market-data";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

function Sparkline({ points }: { points: number[] }): JSX.Element {
    const path = points
        .map((point, index) => `${index === 0 ? "M" : "L"}${(index / (points.length - 1)) * 100},${18 - point * 1.5}`)
        .join(" ");
    return (
        <svg viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true" className="text-primary h-5 w-full">
            <path d={path} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
    );
}

/** 展示固定参考范围的六组样本指数。 */
export function IndexStrip(): JSX.Element {
    const workspace = useMarketWorkspace();
    return (
        <section aria-label="样本指数" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
            {indexQuotes.map((quote) => (
                <Card
                    key={quote.label}
                    role="button"
                    tabIndex={0}
                    onClick={() =>
                        workspace.openInfo(
                            `${quote.label} · 示意指数`,
                            `基准值 1000，当前 ${quote.value}，涨跌 ${quote.change}。该参考指数使用固定虚构成分，不随市场或题材筛选变化。`,
                        )
                    }
                    onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            workspace.openInfo(
                                `${quote.label} · 示意指数`,
                                "该参考指数使用固定虚构成分，不随市场或题材筛选变化。",
                            );
                        }
                    }}
                    className="hover:border-primary/45 min-w-0 rounded-lg"
                >
                    <CardHeader className="flex-row items-center justify-between px-3 pt-3 pb-1">
                        <span className="text-xs font-medium">{quote.label}</span>
                        <span className="text-muted-foreground text-[9px]">示意指数</span>
                    </CardHeader>
                    <CardContent className="px-3 pb-2">
                        <div className="flex items-end justify-between gap-2">
                            <strong className="text-xl font-bold tracking-tight text-rose-400 tabular-nums">
                                {quote.value}
                            </strong>
                            <span className="pb-1 text-[10px] font-semibold text-rose-400">{quote.change}</span>
                        </div>
                        <Sparkline points={quote.points} />
                    </CardContent>
                </Card>
            ))}
        </section>
    );
}
