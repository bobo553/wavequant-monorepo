import type { JSX } from "react";

import { MarketShell } from "@/shared/components/market-shell/market-shell";

import { MarketWorkspaceOverlay } from "@/features/market-dashboard/components/market-workspace-overlay";
import { MarketDashboard } from "@/features/market-dashboard/market-dashboard";
import { MarketWorkspaceProvider } from "@/features/market-dashboard/market-workspace-context";

/** WaveQuant 全景市场看盘页，不覆盖原项目的默认研究工作台。 */
export default function MarketPage(): JSX.Element {
    return (
        <MarketWorkspaceProvider>
            <MarketShell>
                <MarketDashboard />
            </MarketShell>
            <MarketWorkspaceOverlay />
        </MarketWorkspaceProvider>
    );
}
