import type { JSX } from "react";

/** 展示研究上下文与 SDK 标识；全局导航和刷新由共享应用壳承载。 */
export function ResearchHeader(): JSX.Element {
    return (
        <section className="heading research-heading">
            <div>
                <div className="eyebrow">STRATEGY RESEARCH / 主控波浪</div>
            </div>
            <div className="sdk-badge">
                TradingView<small>Lightweight Charts 5.2.1</small>
            </div>
        </section>
    );
}
