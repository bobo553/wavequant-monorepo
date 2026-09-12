"use client";

import type { JSX } from "react";

import { breadthBuckets } from "@/features/market-dashboard/market-data";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

/** 以分桶柱状图展示当前样本涨跌分布。 */
export function BreadthBars(): JSX.Element {
    const workspace = useMarketWorkspace();
    return (
        <div className="border-border xl:border-l xl:pl-4">
            <div className="mb-3 flex justify-between text-[11px]">
                <strong className="text-rose-400">上涨 31</strong>
                <span className="text-muted-foreground">平盘 0</span>
                <strong className="text-emerald-400">下跌 14</strong>
            </div>
            <div className="mb-2 flex h-2 overflow-hidden rounded-full">
                <span className="w-[68%] bg-rose-400" />
                <span className="flex-1 bg-emerald-400" />
            </div>
            <div className="grid h-36 grid-cols-7 items-end gap-2">
                {breadthBuckets.map((bucket) => (
                    <button
                        key={bucket.label}
                        type="button"
                        onClick={() =>
                            workspace.openInfo(
                                `${bucket.label} 涨跌分布`,
                                `当前范围共有 ${bucket.value} 只证券位于 ${bucket.label} 区间。点击个股名称可继续核验；停牌、缺失与无成交样本不计入分桶。`,
                            )
                        }
                        className="hover:bg-secondary/40 flex h-full flex-col items-center justify-end gap-2 rounded"
                    >
                        <span className="text-[10px] tabular-nums">{bucket.value}</span>
                        <span
                            style={{ height: `${Math.max(10, bucket.value * 3.2)}px` }}
                            className={
                                bucket.tone === "down"
                                    ? "w-full max-w-8 rounded-t bg-emerald-400/80"
                                    : bucket.tone === "up"
                                      ? "w-full max-w-8 rounded-t bg-rose-400/80"
                                      : "bg-muted-foreground w-full max-w-8 rounded-t"
                            }
                        />
                        <span className="text-muted-foreground text-center text-[8px] leading-3">{bucket.label}</span>
                    </button>
                ))}
            </div>
            <p className="text-muted-foreground mt-2 text-[10px]">未计入：2 只停牌 / 缺失 / 无成交。</p>
        </div>
    );
}
