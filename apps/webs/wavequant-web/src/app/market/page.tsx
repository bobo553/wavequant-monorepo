import type { JSX } from "react";

import { MarketDashboard } from "@/features/market-dashboard/market-dashboard";

/** WaveQuant 全景市场看盘页，不覆盖原项目的默认研究工作台。 */
export default function MarketPage(): JSX.Element {
    return <MarketDashboard />;
}
