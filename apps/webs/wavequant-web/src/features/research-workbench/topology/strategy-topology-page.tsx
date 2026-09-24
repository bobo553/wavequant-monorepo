"use client";

import { useState } from "react";
import type { JSX, KeyboardEvent } from "react";

import dynamic from "next/dynamic";

import { useResearchPage } from "@/shared/components/wavequant-shell/wavequant-shell-state";

import { topologyFlows, topologyProfileNotes } from "./topology-data";

const TopologyCanvas = dynamic(() => import("./topology-canvas").then((module) => module.TopologyCanvas), {
    ssr: false,
    loading: () => <div className="panel topology-canvas-loading">正在准备策略画布…</div>,
});

export function StrategyTopologyPage(): JSX.Element {
    const activePage = useResearchPage();
    const [flowId, setFlowId] = useState(topologyFlows[0].id);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const flow = topologyFlows.find((item) => item.id === flowId) ?? topologyFlows[0];
    const flowIndex = topologyFlows.findIndex((item) => item.id === flow.id);
    const selectedGate = flow.gates.find((gate) => gate.id === selectedId) ?? flow.gates[0];

    const handleStageKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
        const nextIndex =
            event.key === "ArrowRight"
                ? (index + 1) % topologyFlows.length
                : event.key === "ArrowLeft"
                  ? (index - 1 + topologyFlows.length) % topologyFlows.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? topologyFlows.length - 1
                      : -1;
        if (nextIndex < 0) return;
        event.preventDefault();
        const nextFlow = topologyFlows[nextIndex];
        if (!nextFlow) return;
        setFlowId(nextFlow.id);
        setSelectedId(null);
        document.getElementById(`topology-tab-${nextFlow.id}`)?.focus();
    };

    return (
        <section id="page-topology" className="page" hidden={activePage !== "topology"} aria-label="策略拓扑">
            {activePage === "topology" ? (
                <div className="topology-page">
                    <header className="heading topology-heading">
                        <div>
                            <div className="eyebrow">STRATEGY RESEARCH / 决策拓扑</div>
                            <h1>
                                让每一条规则，都有去向<span className="accent">.</span>
                            </h1>
                            <p>沿真实策略路径复核结构、买点、模拟执行与退出。</p>
                        </div>
                        <div className="topology-heading-badge">
                            V3 全局策略<small>因果判断 · 只读画布</small>
                        </div>
                    </header>

                    <div className="panel topology-stage-bar">
                        <div className="topology-stage-caption">
                            <span>流程阶段</span>
                            <strong>
                                {String(flowIndex + 1).padStart(2, "0")} /{" "}
                                {String(topologyFlows.length).padStart(2, "0")}
                            </strong>
                        </div>
                        <div role="tablist" aria-label="拓扑阶段" className="topology-stage-tabs">
                            {topologyFlows.map((item, index) => (
                                <button
                                    id={`topology-tab-${item.id}`}
                                    key={item.id}
                                    type="button"
                                    role="tab"
                                    aria-controls="topology-stage-panel"
                                    aria-selected={item.id === flow.id}
                                    tabIndex={item.id === flow.id ? 0 : -1}
                                    onClick={() => {
                                        setFlowId(item.id);
                                        setSelectedId(null);
                                    }}
                                    onKeyDown={(event) => handleStageKeyDown(event, index)}
                                >
                                    {item.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div id="topology-stage-panel" role="tabpanel" aria-labelledby={`topology-tab-${flow.id}`}>
                        <p className="topology-stage-description">{flow.description}</p>
                        <div className="topology-workspace">
                            <TopologyCanvas key={flow.id} flow={flow} selectedId={selectedId} />
                            <aside className="panel topology-inspector" aria-label="判断与源码依据">
                                <div className="topology-panel-header">
                                    <div>
                                        <span className="topology-panel-kicker">规则索引</span>
                                        <h2>逐关复核</h2>
                                    </div>
                                    <span className="topology-count">{flow.gates.length} 项</span>
                                </div>
                                <div className="topology-rule-list">
                                    {flow.gates.map((gate, index) => (
                                        <button
                                            key={gate.id}
                                            type="button"
                                            aria-pressed={selectedGate?.id === gate.id}
                                            className="topology-rule"
                                            onClick={() => setSelectedId(gate.id)}
                                        >
                                            <span className="topology-rule__number">
                                                {String(index + 1).padStart(2, "0")}
                                            </span>
                                            <span className="topology-rule__body">
                                                <strong>{gate.question}</strong>
                                                <span className="topology-rule__route">
                                                    <em>是</em>
                                                    {gate.yes}
                                                </span>
                                                <span className="topology-rule__route topology-rule__route--no">
                                                    <em>否</em>
                                                    {gate.no}
                                                </span>
                                            </span>
                                        </button>
                                    ))}
                                </div>
                                {selectedGate ? (
                                    <div className="topology-detail" role="status">
                                        <span className="topology-panel-kicker">当前判断 · 源码依据</span>
                                        <h3>{selectedGate.question}</h3>
                                        <p>{selectedGate.detail}</p>
                                        <code>{selectedGate.source}</code>
                                    </div>
                                ) : null}
                            </aside>
                        </div>
                    </div>

                    <details className="panel topology-notes">
                        <summary>
                            方案口径与维护说明 <span>展开查看</span>
                        </summary>
                        <ul>
                            {topologyProfileNotes.map((note) => (
                                <li key={note}>{note}</li>
                            ))}
                        </ul>
                    </details>
                </div>
            ) : null}
        </section>
    );
}
