import type { JSX, KeyboardEvent } from "react";

import { Button } from "@repo/design-system-web/components";

import { marketTabs } from "@/features/market-dashboard/market-data";
import type { TMarketTabId } from "@/features/market-dashboard/market-types";

interface IMarketTabsProps {
    activeTab: TMarketTabId;
    onChange: (tab: TMarketTabId) => void;
}

/** 提供看盘子视图切换及键盘方向键导航。 */
export function MarketTabs({ activeTab, onChange }: IMarketTabsProps): JSX.Element {
    function handleKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const currentIndex = marketTabs.findIndex((tab) => tab.id === activeTab);
        const nextIndex =
            event.key === "Home"
                ? 0
                : event.key === "End"
                  ? marketTabs.length - 1
                  : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + marketTabs.length) % marketTabs.length;
        const nextTab = marketTabs[nextIndex];
        if (nextTab) onChange(nextTab.id);
    }

    return (
        <div
            role="tablist"
            aria-label="看盘视图"
            onKeyDown={handleKeyDown}
            className="border-border flex overflow-x-auto border-b"
        >
            {marketTabs.map((tab) => (
                <Button
                    key={tab.id}
                    role="tab"
                    aria-selected={tab.id === activeTab}
                    tabIndex={tab.id === activeTab ? 0 : -1}
                    variant="ghost"
                    onClick={() => onChange(tab.id)}
                    className={
                        tab.id === activeTab
                            ? "border-primary text-primary h-11 shrink-0 rounded-none border-b-2 px-4"
                            : "text-muted-foreground h-11 shrink-0 rounded-none px-4"
                    }
                >
                    {tab.label}
                    {tab.count ? (
                        <span className="bg-secondary rounded px-1.5 py-0.5 text-[10px]">{tab.count}</span>
                    ) : null}
                </Button>
            ))}
        </div>
    );
}
