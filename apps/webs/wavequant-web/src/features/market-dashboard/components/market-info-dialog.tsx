"use client";
import type { JSX } from "react";
import { useMarketWorkspace } from "../market-workspace-context";
export default function MarketInfoDialog(): JSX.Element | null {
    const { dialog } = useMarketWorkspace();
    return dialog?.kind === "info" ? <p className="text-muted-foreground p-5 text-sm leading-7 whitespace-pre-line">{dialog.body}</p> : null;
}
