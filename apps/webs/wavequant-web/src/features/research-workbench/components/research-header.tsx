import type { JSX } from "react";

import { Button } from "@repo/design-system-web/components";

/** 研究工作台标题区；刷新按钮复用共享 shadcn Button 原语。 */
export function ResearchHeader(): JSX.Element {
    return (
        <>
            <header className="topbar">
                <div className="breadcrumb">
                    研究中心 <span>/</span> <b id="page-title">K 线复盘</b>
                </div>
                <div className="top-actions">
                    <span className="pill">● 离线历史数据</span>
                    <Button id="reload" className="icon-button" variant="outline" size="sm" title="刷新当前视图">
                        ↻ 刷新
                    </Button>
                </div>
            </header>
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
        </>
    );
}
