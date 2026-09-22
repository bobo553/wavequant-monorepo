"use client";

import type { JSX } from "react";

import { IndexStrip } from "@/features/market-dashboard/components/index-strip";
import { LadderView } from "@/features/market-dashboard/components/ladder-view";
import { MarketTabs } from "@/features/market-dashboard/components/market-tabs";
import { MarketToolbar } from "@/features/market-dashboard/components/market-toolbar";
import { OverviewView } from "@/features/market-dashboard/components/overview-view";
import { SupportingView } from "@/features/market-dashboard/components/supporting-view";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

/** WaveQuant 全景市场看盘交互区，仅持有视图切换所需客户端状态。 */
export function MarketDashboard(): JSX.Element {
    const { activeTab, setActiveTab } = useMarketWorkspace();

    return (
        <main id="market-main" className="min-w-0 px-3 py-4 sm:px-6 sm:py-5 xl:px-8">
            <header className="mb-4 pr-0 xl:pr-[360px]">
                <p className="text-primary text-[10px] font-semibold tracking-[0.18em] uppercase">
                    Market Intelligence / 看盘工作台
                </p>
                <div className="mt-1 flex items-baseline gap-4">
                    <h1 className="text-2xl font-bold tracking-tight">市场看盘</h1>
                    <span className="text-muted-foreground text-[10px]">大盘 → 主线 → 个股 → 验证</span>
                </div>
            </header>
            <div className="flex flex-col gap-3">
                {activeTab !== "ladder" && <MarketToolbar />}
                {activeTab !== "ladder" && <IndexStrip />}
                <MarketTabs activeTab={activeTab} onChange={setActiveTab} />
                <div
                    role="tabpanel"
                    aria-label={
                        activeTab === "overview" ? "市场总览" : activeTab === "ladder" ? "涨停阶梯" : "市场辅助视图"
                    }
                >
                    {activeTab === "overview" ? (
                        <OverviewView onNavigate={setActiveTab} />
                    ) : activeTab === "ladder" ? (
                        <LadderView />
                    ) : (
                        <SupportingView tab={activeTab} onNavigate={setActiveTab} />
                    )}
                </div>
            </div>
        </main>
    );
}
