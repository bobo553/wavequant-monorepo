import type { JSX } from "react";

import "../../../public/styles.css";
import { ResearchChart } from "./components/research-chart";
import { ResearchControls } from "./components/research-controls";
import { ResearchEvidence } from "./components/research-evidence";
import { ResearchHeader } from "./components/research-header";
import { ResearchLedgers, StockBacktestResults } from "./components/research-ledgers";
import { ResearchMetrics } from "./components/research-metrics";
import { HealthPage, OrdersPage, PerformancePage } from "./components/research-secondary-pages";
import { ResearchSidebar } from "./components/research-sidebar";
import { StockBrowser } from "./components/stock-browser";
import { ResearchRuntime } from "./runtime/research-runtime";

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
                <ResearchSidebar />
                <main>
                    <ResearchHeader />
                    <ResearchControls />
                    <ResearchMetrics />
                    <section id="page-workspace" className="page">
                        <div id="backtest-details" className="panel note-card" hidden />
                        <div className="workspace-grid">
                            <ResearchChart />
                            <StockBrowser />
                            <ResearchEvidence />
                        </div>
                        <ResearchLedgers />
                    </section>
                    <StockBacktestResults />
                    <PerformancePage />
                    <OrdersPage />
                    <HealthPage />
                    <footer>
                        <span>
                            TradingView Lightweight Charts™ · Copyright (с) 2025{" "}
                            <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">
                                TradingView, Inc.
                            </a>
                        </span>
                        <span>本机数据 · 无外部行情请求 · 不提供买卖操作</span>
                    </footer>
                </main>
            </div>
            <ResearchRuntime />
        </>
    );
}
