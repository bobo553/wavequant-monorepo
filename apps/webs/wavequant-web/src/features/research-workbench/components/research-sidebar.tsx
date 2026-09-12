import type { JSX } from "react";

import Link from "next/link";

const navigation = [
    ["workspace", "⌁", "K 线复盘"],
    ["performance", "▥", "策略绩效"],
    ["orders", "≡", "订单与信号"],
    ["health", "◉", "系统状态"],
] as const;

/** 原研究工作台的主导航，保留 data-page 契约供客户端控制器切换视图。 */
export function ResearchSidebar(): JSX.Element {
    return (
        <aside className="sidebar">
            <Link className="brand" href="/">
                <span className="brand-icon">∿</span>
                <span>
                    WaveQuant<small>主控量化研究</small>
                </span>
            </Link>
            <div className="nav-label">RESEARCH WORKSPACE</div>
            <nav aria-label="工作台导航">
                {navigation.map(([page, icon, label], index) => (
                    <button className={`nav-item${index === 0 ? "active" : ""}`} data-page={page} key={page}>
                        <span>{icon}</span> {label}
                    </button>
                ))}
            </nav>
            <div className="sidebar-bottom">
                <span className="status-dot" /> 本地 · 只读模式
                <p>通达信日线 / 无实盘路由</p>
                <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">
                    Charts by TradingView ↗
                </a>
                <a href="/vendor/NOTICE" target="_blank">
                    开源署名与 NOTICE
                </a>
                <a href="/vendor/LICENSE" target="_blank">
                    Apache 2.0 License
                </a>
            </div>
        </aside>
    );
}
