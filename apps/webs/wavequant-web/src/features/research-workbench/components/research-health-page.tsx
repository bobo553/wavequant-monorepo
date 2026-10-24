"use client";
import type { JSX } from "react";
import { ResearchTable } from "./research-ledgers";
import { useResearchSection } from "../runtime/use-research-section";

export function HealthPage(): JSX.Element {
    const active = useResearchSection("health");
    return (
        <section id="page-health" className="page" hidden={!active}>
            <article className="panel">
                <div className="card-header">
                    <h2>运行与数据门禁</h2>
                    <span className="pill">真实订单关闭</span>
                </div>
                <div id="health-summary" className="note-card" />
                <ResearchTable headers={["验收项", "状态", "说明"]} bodyId="health-body" />
            </article>
            <article className="panel">
                <div className="card-header">
                    <h2>
                        本地告警 <small>只读展示；确认操作请使用 CLI</small>
                    </h2>
                </div>
                <div id="alerts" className="alerts" />
            </article>
        </section>
    );
}
