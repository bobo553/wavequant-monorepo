import type { JSX } from "react";

import { ResearchChart } from "./components/research-chart";
import { ResearchControls } from "./components/research-controls";
import { ResearchEvidence } from "./components/research-evidence";
import { ResearchHeader } from "./components/research-header";
import { ResearchLedgers, StockBacktestResults } from "./components/research-ledgers";
import { ResearchMetrics } from "./components/research-metrics";
import { HealthPage, OrdersPage, PerformancePage } from "./components/research-secondary-pages";
import { StockBrowser } from "./components/stock-browser";
import { WatchlistRail } from "./components/watchlist-rail";
import { ResearchRuntime } from "./runtime/research-runtime";
import { StrategyTopologyPage } from "./topology/strategy-topology-page";

/**
 * 完整的 React 研究工作台页面。
 *
 * 页面结构由 React 组件负责；Lightweight Charts 与已有、已验收的研究交互通过
 * Client Component 运行时适配器挂载，避免在 Server Component 中访问浏览器 API。
 */
export function ResearchWorkbench(): JSX.Element {
    return (
        <>
            <div data-wavequant-react-workbench="true" style={{ display: "contents" }}>
                <main id="research-main" className="research-content" tabIndex={-1}>
                    <ResearchHeader />
                    <ResearchControls />
                    <ResearchMetrics />
                    <section id="page-workspace" className="page">
                        <div className="workspace-grid">
                            <WatchlistRail />
                            <ResearchChart />
                            <StockBrowser />
                            <ResearchEvidence />
                        </div>
                        <div id="backtest-details" className="panel note-card" hidden />
                        <ResearchLedgers />
                    </section>
                    <StockBacktestResults />
                    <PerformancePage />
                    <StrategyTopologyPage />
                    <OrdersPage />
                    <HealthPage />
                </main>
            </div>
            <ResearchRuntime />
        </>
    );
}
