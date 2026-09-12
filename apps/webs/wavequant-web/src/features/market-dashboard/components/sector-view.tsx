"use client";

import { type JSX, useMemo, useState } from "react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@repo/design-system-web/components";
import { IconArrowsSort, IconChevronRight } from "@tabler/icons-react";

import { sectorHeat, stockQuotes } from "@/features/market-dashboard/market-data";
import type { TMarketTabId, TSortDirection } from "@/features/market-dashboard/market-types";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

interface ISectorViewProps {
    onNavigate: (tab: TMarketTabId) => void;
}

type TSectorSort = "current" | "amount" | "coverage" | "none";

/** 提供行业/概念切换、三日同刻对照、三态排序、成分钻取与范围联动。 */
export function SectorView({ onNavigate }: ISectorViewProps): JSX.Element {
    const workspace = useMarketWorkspace();
    const [category, setCategory] = useState<"industry" | "concept">("industry");
    const [days, setDays] = useState<1 | 3>(3);
    const [sort, setSort] = useState<{ direction: TSortDirection; key: TSectorSort }>({
        direction: "none",
        key: "none",
    });
    const rows = useMemo(
        () =>
            sectorHeat
                .map((sector, index) => ({
                    ...sector,
                    amount: 5.45 + index * 2.63,
                    coverage: 96 - index * 3,
                    current: Number.parseFloat(sector.change),
                    previous: Number.parseFloat(sector.change) - (index % 3) * 1.17 + 0.42,
                }))
                .sort((left, right) => {
                    if (sort.direction === "none" || sort.key === "none") return 0;
                    const delta = left[sort.key] - right[sort.key];
                    return sort.direction === "desc" ? -delta : delta;
                }),
        [sort],
    );

    function cycleSort(key: Exclude<TSectorSort, "none">): void {
        setSort((current) =>
            current.key !== key
                ? { direction: "desc", key }
                : current.direction === "desc"
                  ? { direction: "asc", key }
                  : { direction: "none", key: "none" },
        );
    }

    return (
        <div className="space-y-3">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h2 className="text-lg font-bold">板块轮动 · 同刻横向比较</h2>
                    <p className="text-muted-foreground mt-1 text-[10px]">
                        面积代表成交额，颜色代表当前等权涨幅；缺失不会被显示为平盘。
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button
                        variant={category === "industry" ? "secondary" : "outline"}
                        onClick={() => setCategory("industry")}
                    >
                        行业
                    </Button>
                    <Button
                        variant={category === "concept" ? "secondary" : "outline"}
                        onClick={() => setCategory("concept")}
                    >
                        概念题材
                    </Button>
                    <Button variant="outline" onClick={() => setDays(days === 3 ? 1 : 3)}>
                        {days} 日同刻
                    </Button>
                </div>
            </header>
            <section aria-label="板块热力图" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {rows.map((sector, index) => (
                    <button
                        key={sector.name}
                        type="button"
                        onClick={() => workspace.setSelectedSector(sector.name)}
                        className={`border-border rounded-lg border p-4 text-left hover:border-rose-400/45 ${workspace.selectedSector === sector.name ? "ring-primary ring-2" : ""} ${index < 4 ? "bg-rose-400/20" : "bg-rose-400/10"}`}
                    >
                        <span className="flex justify-between">
                            <strong>{category === "concept" && index % 2 ? `${sector.name}链` : sector.name}</strong>
                            <Badge variant="outline">覆盖 {sector.coverage}%</Badge>
                        </span>
                        <b className="mt-3 block text-2xl text-rose-400">{sector.change}</b>
                        <span className="text-muted-foreground mt-1 block text-[10px]">
                            {days === 3
                                ? `前两日 ${sector.previous.toFixed(2)}% / ${(sector.previous - 0.8).toFixed(2)}%`
                                : sector.detail}
                        </span>
                    </button>
                ))}
            </section>
            <Card className="rounded-lg">
                <CardHeader className="flex-row items-center justify-between">
                    <CardTitle>板块精确数据</CardTitle>
                    <Button
                        onClick={() => {
                            if (workspace.selectedSector === "全部题材")
                                workspace.setSelectedSector(rows[0]?.name ?? "全部题材");
                            onNavigate("overview");
                        }}
                    >
                        联动此板块到全景看盘
                    </Button>
                </CardHeader>
                <CardContent className="overflow-x-auto p-0">
                    <table className="w-full min-w-[760px] text-left text-xs">
                        <thead className="bg-background/40 text-muted-foreground">
                            <tr>
                                <th className="p-3">板块</th>
                                <SortHead label="涨跌幅" onClick={() => cycleSort("current")} />
                                <SortHead label="成交额" onClick={() => cycleSort("amount")} />
                                <SortHead label="有效覆盖" onClick={() => cycleSort("coverage")} />
                                <th className="p-3">封板 / 触板 / 炸板</th>
                                <th className="p-3">成分</th>
                            </tr>
                        </thead>
                        <tbody className="divide-border divide-y">
                            {rows.map((sector, index) => (
                                <tr key={sector.name}>
                                    <td className="p-3 font-medium">{sector.name}</td>
                                    <td className="p-3 text-rose-400">{sector.change}</td>
                                    <td className="p-3">{sector.amount.toFixed(2)}亿元</td>
                                    <td className="p-3">{sector.coverage}%</td>
                                    <td className="p-3">
                                        {Math.max(0, 3 - Math.floor(index / 3))} / {4 + (index % 3)} / {index % 2}
                                    </td>
                                    <td className="p-3">
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() =>
                                                workspace.openStock(
                                                    stockQuotes[index % stockQuotes.length]?.code ?? "SIM001",
                                                )
                                            }
                                        >
                                            查看领先成分
                                            <IconChevronRight data-icon="inline-end" aria-hidden="true" />
                                        </Button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </CardContent>
            </Card>
        </div>
    );
}

function SortHead({ label, onClick }: { label: string; onClick: () => void }): JSX.Element {
    return (
        <th className="p-3">
            <Button variant="ghost" size="sm" onClick={onClick}>
                {label}
                <IconArrowsSort data-icon="inline-end" aria-hidden="true" />
            </Button>
        </th>
    );
}
