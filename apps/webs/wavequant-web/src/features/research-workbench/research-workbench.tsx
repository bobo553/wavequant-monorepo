import type { JSX } from "react";

import { ResearchChart } from "./components/research-chart";
import { ResearchControls } from "./components/research-controls";
import { ResearchEvidence } from "./components/research-evidence";
import { ResearchHeader } from "./components/research-header";
import { ResearchLedgers, StockBacktestResults } from "./components/research-ledgers";
import { ResearchMetrics } from "./components/research-metrics";
import { DeferredResearchPages } from "./components/deferred-research-pages";
import { StockBrowser } from "./components/stock-browser";
import { WatchlistRail } from "./components/watchlist-rail";
import { ResearchRuntime } from "./runtime/research-runtime";
import { DeferredTopologyPage } from "./topology/deferred-topology-page";

/**
 * 完整的 React 研究工作台页面。
 *
 * 页面结构由 React 组件负责；Lightweight Charts 与已有、已验收的研究交互通过
 * Client Component 运行时适配器挂载，避免在 Server Component 中访问浏览器 API。
 */
export function ResearchWorkbench(): JSX.Element {
    return (
        <>
            <link rel="modulepreload" href="/watchlist-bootstrap.js" />
            <div data-wavequant-react-workbench="true" style={{ display: "contents" }}>
                <main id="research-main" className="research-content" tabIndex={-1}>
                    <ResearchHeader />
                    <ResearchMetrics />
                    <section id="page-workspace" className="page">
                        <div className="workspace-grid">
                            <WatchlistRail />
                            <ResearchChart />
                            <StockBrowser />
                            <ResearchEvidence />
                        </div>
                        <div className="backtest-details-slot">
                            <div id="backtest-details" className="panel note-card" hidden />
                        </div>
                        <ResearchLedgers />
                    </section>
                    <ResearchControls />
                    <StockBacktestResults />
                    <DeferredResearchPages />
                    <DeferredTopologyPage />
                </main>
            </div>
            <ResearchRuntime />
        </>
    );
}
