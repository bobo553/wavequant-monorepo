"use client";
import { useEffect, useId, useRef, type JSX } from "react";
import { AsyncFeatureBoundary } from "@/shared/components/async-feature";
import { useMarketWorkspace } from "../market-workspace-context";
import { marketDialogViews } from "./async-market-dialogs";

/** 唯一受控弹窗外壳即时可关闭；各正文通过async-ui独立加载。 */
export function MarketWorkspaceOverlay(): JSX.Element | null {
    const { dialog, closeDialog } = useMarketWorkspace();
    const panel = useRef<HTMLElement>(null);
    const titleId = useId();
    const kind = dialog?.kind;
    useEffect(() => {
        if (!kind) return;
        const previous = document.activeElement;
        const overflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        if (!panel.current?.contains(document.activeElement)) panel.current?.focus();
        return () => {
            document.body.style.overflow = overflow;
            if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
        };
    }, [kind]);
    if (!dialog) return null;
    const View = marketDialogViews[dialog.kind];
    const title = dialog.kind === "info" ? dialog.title : dialog.kind === "stock" ? `个股核验 · ${dialog.code}` :
        { search: "找股票 / 题材", settings: "界面与工作区设置", notifications: "提醒中心" }[dialog.kind];
    return <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/75 p-3 backdrop-blur-sm" role="presentation"
        onClick={(event) => { if (event.target === event.currentTarget) closeDialog(); }}>
        <section ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId}
            className="border-border bg-card max-h-[92vh] w-full max-w-2xl overflow-auto rounded-xl border shadow-2xl"
            onKeyDown={(event) => {
                if (event.key === "Escape") { event.stopPropagation(); closeDialog(); }
                if (event.key !== "Tab") return;
                const items = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex="0"]')].filter((node) => node.getClientRects().length);
                const first = items[0], last = items.at(-1);
                if (!first) { event.preventDefault(); return; }
                if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) { event.preventDefault(); last?.focus(); }
                else if (!event.shiftKey && (document.activeElement === last || document.activeElement === event.currentTarget)) { event.preventDefault(); first.focus(); }
            }}>
            <header className="border-border sticky top-0 z-10 flex items-center justify-between border-b bg-inherit px-5 py-4">
                <h2 id={titleId} className="text-base font-semibold">{title}</h2>
                <button type="button" onClick={closeDialog} aria-label="关闭对话框">关闭</button>
            </header>
            <AsyncFeatureBoundary key={dialog.kind}><View /></AsyncFeatureBoundary>
        </section>
    </div>;
}
