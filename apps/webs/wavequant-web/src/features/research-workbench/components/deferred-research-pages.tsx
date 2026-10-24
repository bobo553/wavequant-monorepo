"use client";
import { useEffect, useState, type ReactNode, type JSX } from "react";
import { defineAsyncView } from "@repo/async-ui";
import { AsyncFeatureBoundary, AsyncFeatureFallback } from "@/shared/components/async-feature";
import { useResearchPage } from "@/shared/components/wavequant-shell/wavequant-shell-state";
const Performance = defineAsyncView({ id: "research-performance", source: "src/features/research-workbench/components/research-performance-page.tsx",
    loader: () => import(/* webpackChunkName: "research-performance" */ "./research-performance-page").then((module) => ({ default: module.PerformancePage })), fallback: AsyncFeatureFallback });
const Orders = defineAsyncView({ id: "research-orders", source: "src/features/research-workbench/components/research-orders-page.tsx",
    loader: () => import(/* webpackChunkName: "research-orders" */ "./research-orders-page").then((module) => ({ default: module.OrdersPage })), fallback: AsyncFeatureFallback });
const Health = defineAsyncView({ id: "research-health", source: "src/features/research-workbench/components/research-health-page.tsx",
    loader: () => import(/* webpackChunkName: "research-health" */ "./research-health-page").then((module) => ({ default: module.HealthPage })), fallback: AsyncFeatureFallback });

/** 首次选择前不挂载；旧运行时持有DOM引用，首次进入后保留节点和表单状态。 */
function VisitedSection({ page, children }: { page: string; children: ReactNode }): JSX.Element | null {
    const active = useResearchPage() === page;
    const [visited, setVisited] = useState(false);
    useEffect(() => { if (active) setVisited(true); }, [active]);
    return active || visited ? <div hidden={!active}><AsyncFeatureBoundary>{children}</AsyncFeatureBoundary></div> : null;
}
export function DeferredResearchPages(): JSX.Element {
    return <>
        <VisitedSection page="performance"><Performance /></VisitedSection>
        <VisitedSection page="orders"><Orders /></VisitedSection>
        <VisitedSection page="health"><Health /></VisitedSection>
    </>;
}
