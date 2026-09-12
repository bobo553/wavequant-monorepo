"use client";

import type { JSX } from "react";

import { ladderLevels, stockQuotes } from "@/features/market-dashboard/market-data";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

interface ILadderBoardProps {
    compact?: boolean;
}

/** 按连板高度展示当前封板样本，空梯队保留明确状态。 */
export function LadderBoard({ compact = false }: ILadderBoardProps): JSX.Element {
    const workspace = useMarketWorkspace();
    const levels = compact ? ladderLevels.slice(0, 4) : ladderLevels;
    return (
        <div className="flex flex-col">
            {levels.map((level) => (
                <div
                    key={level.level}
                    className="border-border grid min-h-14 grid-cols-[56px_minmax(0,1fr)] items-center border-b last:border-0"
                >
                    <div className="text-center">
                        <strong className="block text-lg text-amber-300">{level.label}</strong>
                        <span className="text-muted-foreground text-[9px]">板 · {level.stocks.length} 只</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 py-2">
                        {level.stocks.length ? (
                            level.stocks.map((stock) => (
                                <button
                                    key={stock.name}
                                    type="button"
                                    onClick={() =>
                                        workspace.openStock(
                                            stockQuotes.find((item) => item.name === stock.name)?.code ?? "SIM001",
                                        )
                                    }
                                    className="border-border bg-background/45 hover:border-primary/50 rounded-md border px-2 py-1.5 text-left"
                                >
                                    <span className="flex gap-2 text-[11px]">
                                        <strong>{stock.name}</strong>
                                        <b className="text-rose-400">{stock.change}</b>
                                    </span>
                                    <span className="text-muted-foreground block text-[8px]">
                                        {stock.board} · {stock.theme} · {stock.streak}板暂定
                                    </span>
                                </button>
                            ))
                        ) : (
                            <span className="text-muted-foreground text-[10px]">此梯队暂无样本</span>
                        )}
                    </div>
                </div>
            ))}
        </div>
    );
}
