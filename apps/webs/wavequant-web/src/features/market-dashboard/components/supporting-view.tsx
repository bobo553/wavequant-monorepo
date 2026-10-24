"use client";

import type { JSX } from "react";

import { AsyncSector, AsyncTheme, AsyncLeader, AsyncRadar, AsyncMulti, AsyncReview } from "../async-market-views";
import type { TMarketTabId } from "@/features/market-dashboard/market-types";

interface ISupportingViewProps {
    onNavigate: (tab: TMarketTabId) => void;
    tab: Exclude<TMarketTabId, "overview" | "ladder" | "catalog">;
}

/** 将六个辅助页签分派到各自完整业务视图。 */
export function SupportingView({ onNavigate, tab }: ISupportingViewProps): JSX.Element {
    if (tab === "sectors") return <AsyncSector onNavigate={onNavigate} />;
    if (tab === "themes") return <AsyncTheme onNavigate={onNavigate} />;
    if (tab === "leaders") return <AsyncLeader />;
    if (tab === "radar") return <AsyncRadar />;
    if (tab === "multi") return <AsyncMulti />;
    return <AsyncReview />;
}
