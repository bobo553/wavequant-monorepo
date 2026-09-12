"use client";

import { type JSX, useMemo, useState } from "react";

import { Badge, Button } from "@repo/design-system-web/components";
import { IconArrowsSort, IconStar, IconStarFilled } from "@tabler/icons-react";

import type { IStockQuote, TSortDirection, TStockSortKey } from "@/features/market-dashboard/market-types";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

interface IStockTableProps {
    rows: IStockQuote[];
}

function numericValue(stock: IStockQuote, key: TStockSortKey): number {
    const source = stock[key];
    const numeric = Number.parseFloat(source.replaceAll(",", ""));
    if (key !== "amount") return numeric;
    return source.includes("亿元") ? numeric * 10_000 : numeric;
}

/** 展示可三态排序、可查看个股且可维护自选的股票池。 */
export function StockTable({ rows }: IStockTableProps): JSX.Element {
    const workspace = useMarketWorkspace();
    const [sort, setSort] = useState<{ direction: TSortDirection; key: TStockSortKey | null }>({
        direction: "none",
        key: null,
    });
    const sortedRows = useMemo(() => {
        if (!sort.key || sort.direction === "none") return rows;
        return [...rows].sort(
            (left, right) =>
                (numericValue(left, sort.key as TStockSortKey) - numericValue(right, sort.key as TStockSortKey)) *
                (sort.direction === "asc" ? 1 : -1),
        );
    }, [rows, sort]);

    function cycleSort(key: TStockSortKey): void {
        setSort((current) =>
            current.key !== key
                ? { direction: "desc", key }
                : current.direction === "desc"
                  ? { direction: "asc", key }
                  : { direction: "none", key: null },
        );
    }

    return (
        <div className="overflow-x-auto">
            <table className="w-full min-w-[880px] text-left text-[11px]" aria-label="当前涨停股票池">
                <thead className="bg-background/35 text-muted-foreground text-[10px]">
                    <tr>
                        <th className="px-3 py-3 font-medium">名称 / 代码</th>
                        <SortableHead
                            label="现价"
                            active={sort.key === "price" ? sort.direction : "none"}
                            onClick={() => cycleSort("price")}
                        />
                        <SortableHead
                            label="涨跌幅"
                            active={sort.key === "change" ? sort.direction : "none"}
                            onClick={() => cycleSort("change")}
                        />
                        <SortableHead
                            label="成交额"
                            active={sort.key === "amount" ? sort.direction : "none"}
                            onClick={() => cycleSort("amount")}
                        />
                        <SortableHead
                            label="换手率"
                            active={sort.key === "turnover" ? sort.direction : "none"}
                            onClick={() => cycleSort("turnover")}
                        />
                        <th className="px-3 py-3 font-medium">涨停状态</th>
                        <th className="px-3 py-3 font-medium">首封 / 开板</th>
                        <th className="px-3 py-3 text-center font-medium">观察</th>
                    </tr>
                </thead>
                <tbody className="divide-border divide-y">
                    {sortedRows.length ? (
                        sortedRows.map((stock) => (
                            <tr key={stock.code} className="hover:bg-background/30">
                                <td className="px-3 py-2.5">
                                    <button
                                        type="button"
                                        className="hover:text-primary text-left"
                                        onClick={() => workspace.openStock(stock.code)}
                                    >
                                        <strong className="block font-medium">{stock.name}</strong>
                                        <span className="text-muted-foreground text-[9px]">
                                            {stock.code} · {stock.board}
                                        </span>
                                    </button>
                                </td>
                                <td className="px-3 py-2.5 font-medium text-rose-400 tabular-nums">{stock.price}</td>
                                <td className="px-3 py-2.5 font-semibold text-rose-400 tabular-nums">{stock.change}</td>
                                <td className="px-3 py-2.5 tabular-nums">{stock.amount}</td>
                                <td className="px-3 py-2.5 tabular-nums">{stock.turnover}</td>
                                <td className="px-3 py-2.5">
                                    <Badge className="rounded bg-rose-500/15 text-[9px] text-rose-300">
                                        {stock.status}
                                    </Badge>
                                </td>
                                <td className="px-3 py-2.5 tabular-nums">
                                    {stock.firstSeal} / {stock.openCount} 次
                                </td>
                                <td className="px-3 py-2.5 text-center">
                                    <Button
                                        variant="outline"
                                        size="icon-sm"
                                        onClick={() => workspace.toggleFavorite(stock.code)}
                                        aria-label={`${workspace.favorites.includes(stock.code) ? "取消关注" : "关注"}${stock.name}`}
                                    >
                                        {workspace.favorites.includes(stock.code) ? (
                                            <IconStarFilled className="text-primary" aria-hidden="true" />
                                        ) : (
                                            <IconStar aria-hidden="true" />
                                        )}
                                    </Button>
                                </td>
                            </tr>
                        ))
                    ) : (
                        <tr>
                            <td colSpan={8} className="text-muted-foreground px-4 py-12 text-center">
                                当前股票池没有匹配样本；这是筛选零结果，不是数据服务错误。
                            </td>
                        </tr>
                    )}
                </tbody>
            </table>
        </div>
    );
}

function SortableHead({
    active,
    label,
    onClick,
}: {
    active: TSortDirection;
    label: string;
    onClick: () => void;
}): JSX.Element {
    return (
        <th className="px-2 py-1 font-medium">
            <Button variant="ghost" size="sm" onClick={onClick}>
                {label}
                <IconArrowsSort data-icon="inline-end" aria-hidden="true" />
                <span className="sr-only">{active === "none" ? "原顺序" : active === "desc" ? "降序" : "升序"}</span>
            </Button>
        </th>
    );
}
