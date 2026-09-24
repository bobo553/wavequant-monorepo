"use client";

import type { JSX } from "react";

import { IndexStrip } from "@/features/market-dashboard/components/index-strip";
import { LadderView } from "@/features/market-dashboard/components/ladder-view";
import { MarketTabs } from "@/features/market-dashboard/components/market-tabs";
import { MarketHeaderActions, MarketToolbar } from "@/features/market-dashboard/components/market-toolbar";
import { OverviewView } from "@/features/market-dashboard/components/overview-view";
import { SupportingView } from "@/features/market-dashboard/components/supporting-view";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

/** WaveQuant 全景市场看盘交互区，仅持有视图切换所需客户端状态。 */
export function MarketDashboard(): JSX.Element {
    const { activeTab, setActiveTab } = useMarketWorkspace();

    return (
        <main id="market-main" className="market-workbench min-w-0 px-3 py-4 sm:px-6 sm:py-5 xl:px-8">
            <header className="market-heading">
                <div>
                    <p className="market-eyebrow">MARKET INTELLIGENCE / 看盘工作台</p>
                    <h1>
                        市场看盘<span aria-hidden="true">.</span>
                    </h1>
                    <p className="market-heading-copy">大盘、主线、个股与验证共享同一观察时点。</p>
                </div>
                <div className="market-heading-aside">
                    <div className="market-heading-badge">
                        市场全景<small>大盘 → 主线 → 个股 → 验证</small>
                    </div>
                    <MarketHeaderActions />
                </div>
            </header>
            <div className="market-sections flex min-w-0 flex-col gap-3">
                {activeTab !== "ladder" && <MarketToolbar />}
                {activeTab !== "ladder" && <IndexStrip />}
                <div className="market-view-bar">
                    <span className="market-view-caption">看盘视图</span>
                    <MarketTabs activeTab={activeTab} onChange={setActiveTab} />
                </div>
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
