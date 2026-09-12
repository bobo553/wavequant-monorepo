"use client";

import type { JSX } from "react";

import { marketTabs } from "@/features/market-dashboard/market-data";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

/** 在壳层同步展示当前页签、日期、时点与筛选范围。 */
export function MarketContextStatus({ kind }: { kind: "breadcrumb" | "clock" | "footer" }): JSX.Element {
    const workspace = useMarketWorkspace();
    const tab = marketTabs.find((item) => item.id === workspace.activeTab)?.label ?? "市场总览";
    if (kind === "breadcrumb") return <strong className="text-foreground font-medium">市场看盘 / {tab}</strong>;
    if (kind === "clock")
        return (
            <span className="ml-auto">
                {workspace.date} {workspace.time} · {workspace.scope}
            </span>
        );
    return (
        <span>
            <b className="text-primary">● 看盘 v2 · 虚构样本</b> {workspace.date} {workspace.time}:00 +08:00
        </span>
    );
}
