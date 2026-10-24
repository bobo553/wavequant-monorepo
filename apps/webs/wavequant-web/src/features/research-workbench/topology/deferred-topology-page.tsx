"use client";
import { useEffect, useState, type JSX } from "react";
import { defineAsyncView } from "@repo/async-ui";
import { AsyncFeatureBoundary, AsyncFeatureFallback } from "@/shared/components/async-feature";
import { useResearchPage } from "@/shared/components/wavequant-shell/wavequant-shell-state";
const Topology = defineAsyncView({ id: "research-topology", source: "src/features/research-workbench/topology/strategy-topology-page.tsx",
    loader: () => import(/* webpackChunkName: "research-topology" */ "./strategy-topology-page").then((module) => ({ default: module.StrategyTopologyPage })), fallback: AsyncFeatureFallback });

/** 拓扑数据与编辑器只在首次访问时下载，之后保留编辑状态。 */
export function DeferredTopologyPage(): JSX.Element | null {
    const active = useResearchPage() === "topology";
    const [visited, setVisited] = useState(false);
    useEffect(() => { if (active) setVisited(true); }, [active]);
    return active || visited ? <div hidden={!active}><AsyncFeatureBoundary><Topology /></AsyncFeatureBoundary></div> : null;
}
