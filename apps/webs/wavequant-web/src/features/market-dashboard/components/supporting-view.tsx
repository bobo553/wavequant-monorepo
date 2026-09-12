"use client";

import type { JSX } from "react";

import { LeaderView } from "@/features/market-dashboard/components/leader-view";
import { MultiStockView } from "@/features/market-dashboard/components/multi-stock-view";
import { RadarView } from "@/features/market-dashboard/components/radar-view";
import { ReviewView } from "@/features/market-dashboard/components/review-view";
import { SectorView } from "@/features/market-dashboard/components/sector-view";
import { ThemeView } from "@/features/market-dashboard/components/theme-view";
import type { TMarketTabId } from "@/features/market-dashboard/market-types";

interface ISupportingViewProps {
    onNavigate: (tab: TMarketTabId) => void;
    tab: Exclude<TMarketTabId, "overview" | "ladder">;
}

/** 将六个辅助页签分派到各自完整业务视图。 */
export function SupportingView({ onNavigate, tab }: ISupportingViewProps): JSX.Element {
    if (tab === "sectors") return <SectorView onNavigate={onNavigate} />;
    if (tab === "themes") return <ThemeView onNavigate={onNavigate} />;
    if (tab === "leaders") return <LeaderView />;
    if (tab === "radar") return <RadarView />;
    if (tab === "multi") return <MultiStockView />;
    return <ReviewView />;
}
