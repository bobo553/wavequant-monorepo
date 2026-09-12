import type { JSX } from "react";

/** 研究工作台业务标题区；全局上下文与刷新操作由共享应用壳承载。 */
export function ResearchHeader(): JSX.Element {
    return (
        <section className="heading">
            <div>
                <div className="eyebrow">STRATEGY RESEARCH / 主控波浪</div>
                <h1>
                    让每一个信号，都有据可循<span className="accent">.</span>
                </h1>
                <p>N 字结构、趋势交替与轧空信号，在同一张图上复核。</p>
            </div>
            <div className="sdk-badge">
                TradingView<small>Lightweight Charts 5.2.1</small>
            </div>
        </section>
    );
}
