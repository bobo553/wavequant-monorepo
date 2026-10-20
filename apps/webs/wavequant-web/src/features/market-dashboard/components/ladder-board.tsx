"use client";

import type { JSX } from "react";

import type { TLimitUpStock } from "@repo/contracts";
import { IconStar, IconStarFilled } from "@tabler/icons-react";

import { stockResearchHref } from "@/features/market-dashboard/research-link";
import { useMarketWatchlist } from "@/features/market-dashboard/use-market-watchlist";

/** 按连板数分组展示股票，名称跳转行情，独立星标维护服务器自选。 */
export function LadderBoard({
    stocks,
    date,
    compact = false,
}: {
    stocks: TLimitUpStock[];
    date: string;
    compact?: boolean;
}): JSX.Element {
    const favorites = useMarketWatchlist();
    const levels = [...new Set(stocks.map((stock) => stock.streak))].sort((a, b) => b - a);
    return (
        <div className="flex flex-col" aria-label="连板天梯">
            {favorites.message && (
                <p role="status" className="text-muted-foreground py-2 text-xs">
                    {favorites.message}
                </p>
            )}
            {(compact ? levels.slice(0, 4) : levels).map((level) => {
                const rows = stocks.filter((stock) => stock.streak === level);
                return (
                    <section
                        key={level}
                        aria-label={`${level}板梯队`}
                        className="border-border grid min-h-16 grid-cols-[60px_minmax(0,1fr)] items-center border-b last:border-0"
                    >
                        <div className="text-center">
                            <strong className="text-primary block text-lg">
                                {level === 1 ? "首板" : `${level}板`}
                            </strong>
                            <span className="text-muted-foreground text-xs">{rows.length} 只</span>
                        </div>
                        <div className="flex flex-wrap gap-2 py-3">
                            {(compact ? rows.slice(0, 6) : rows).map((stock) => (
                                <div
                                    key={stock.code}
                                    className="border-border bg-background/45 relative rounded-md border"
                                >
                                    <button
                                        type="button"
                                        aria-label={`${favorites.isFavorite(stock.code) ? "取消收藏" : "收藏"} ${stock.name}`}
                                        aria-pressed={favorites.isFavorite(stock.code)}
                                        title={favorites.isFavorite(stock.code) ? "从我的自选移除" : "收藏到我的自选"}
                                        disabled={favorites.pending}
                                        onClick={() => void favorites.toggle(stock.code, stock.name)}
                                        className="text-primary focus-visible:ring-primary absolute top-1 right-1 grid size-8 place-items-center rounded outline-none focus-visible:ring-2 disabled:opacity-50"
                                    >
                                        {favorites.isFavorite(stock.code) ? (
                                            <IconStarFilled size={16} aria-hidden="true" />
                                        ) : (
                                            <IconStar size={16} aria-hidden="true" />
                                        )}
                                    </button>
                                    <a
                                        href={stockResearchHref(stock.code, date)}
                                        aria-label={`查看 ${stock.name} ${stock.code} 行情与复盘`}
                                        title={`打开 ${date} 的行情与复盘`}
                                        className="hover:text-primary focus-visible:ring-primary block rounded-md py-2 pr-10 pl-3 text-xs outline-none focus-visible:ring-2"
                                    >
                                        <div className="flex flex-wrap gap-2">
                                            <strong>{stock.name}</strong>
                                            <span className="text-muted-foreground">{stock.code}</span>
                                        </div>
                                        <div className="text-muted-foreground mt-1">
                                            {stock.industry} · 首封 {stock.first_seal ?? "—"}
                                        </div>
                                        <div className="mt-1 text-rose-400">
                                            {stock.change_pct === null ? "—" : `${stock.change_pct.toFixed(2)}%`}
                                        </div>
                                    </a>
                                </div>
                            ))}
                            {compact && rows.length > 6 && (
                                <span className="text-muted-foreground self-center text-xs">
                                    另 {rows.length - 6} 只，展开查看
                                </span>
                            )}
                        </div>
                    </section>
                );
            })}
        </div>
    );
}
