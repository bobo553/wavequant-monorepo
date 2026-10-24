"use client";
import type { JSX } from "react";
import { useMarketWorkspace } from "../market-workspace-context";
import { MarketWorkspaceOverlay } from "./market-workspace-overlay";

/** toast保留同步轻量反馈，不因一条通知下载任何弹框正文。 */
export function DeferredMarketOverlay(): JSX.Element | null {
    const { dialog, toast } = useMarketWorkspace();
    return <>
        {dialog ? <MarketWorkspaceOverlay /> : null}
        {toast ? <div role="status" className="border-primary/30 bg-secondary fixed bottom-10 left-1/2 z-[60] -translate-x-1/2 rounded-lg border px-4 py-3 text-xs shadow-xl">{toast}</div> : null}
    </>;
}
