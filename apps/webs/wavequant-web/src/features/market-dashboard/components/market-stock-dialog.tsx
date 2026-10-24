"use client";
import type { JSX } from "react";
import { Button, Card, CardContent } from "@repo/design-system-web/components";
import { IconCheck, IconStar } from "@tabler/icons-react";
import type { TWatchGroup } from "../market-types";
import { stockQuotes } from "../market-data";
import { useMarketWorkspace } from "../market-workspace-context";
const groups: Array<{ id: TWatchGroup; label: string }> = [
    { id: "core", label: "核心关注" },
    { id: "verify", label: "待验证" },
    { id: "risk", label: "风险观察" },
];

export default function MarketStockDialog(): JSX.Element {
    const workspace = useMarketWorkspace();
    const dialog = workspace.dialog;
    const selectedStock = dialog?.kind === "stock" ? stockQuotes.find((stock) => stock.code === dialog.code) : undefined;
    if (!selectedStock) return <p className="p-5">当前股票不在演示集合中。</p>;
    return (<div className="space-y-4 p-5">
        <h3 className="text-sm font-semibold">{selectedStock.name} · {selectedStock.code}</h3>
                                <div className="grid gap-3 sm:grid-cols-4">
                                    <Stat label="现价" value={selectedStock.price} tone="text-rose-400" />
                                    <Stat label="涨跌幅" value={selectedStock.change} tone="text-rose-400" />
                                    <Stat label="成交额" value={selectedStock.amount} />
                                    <Stat label="换手率" value={selectedStock.turnover} />
                                </div>
                                <Card className="rounded-lg">
                                    <CardContent className="grid gap-4 p-4 sm:grid-cols-2">
                                        <div>
                                            <h3 className="text-sm font-semibold">封板与规则证据</h3>
                                            <p className="text-muted-foreground mt-2 text-xs leading-6">
                                                {selectedStock.status}；首次封板 {selectedStock.firstSeal}，开板{" "}
                                                {selectedStock.openCount}{" "}
                                                次。涨停状态依据对应板块的虚构价格规则和同一观察时点判断。
                                            </p>
                                        </div>
                                        <div>
                                            <h3 className="text-sm font-semibold">合成五档说明</h3>
                                            <p className="text-muted-foreground mt-2 text-xs leading-6">
                                                盘口为演示队列，仅用于说明封板确认。队列额不是净流入、排队位置或成交承诺。
                                            </p>
                                        </div>
                                    </CardContent>
                                </Card>
                                <div>
                                    <h3 className="mb-2 text-sm font-semibold">加入观察组</h3>
                                    <div className="flex flex-wrap gap-2">
                                        {groups.map((group) => {
                                            const selected =
                                                workspace.watchGroups[selectedStock.code]?.includes(group.id) ?? false;
                                            return (
                                                <Button
                                                    key={group.id}
                                                    variant={selected ? "secondary" : "outline"}
                                                    onClick={() =>
                                                        workspace.toggleWatchGroup(selectedStock.code, group.id)
                                                    }
                                                >
                                                    {selected ? (
                                                        <IconCheck data-icon="inline-start" aria-hidden="true" />
                                                    ) : null}
                                                    {group.label}
                                                </Button>
                                            );
                                        })}
                                    </div>
                                </div>
                                <div className="flex flex-wrap justify-end gap-2">
                                    <Button
                                        variant="outline"
                                        onClick={() => workspace.toggleFavorite(selectedStock.code)}
                                    >
                                        <IconStar data-icon="inline-start" aria-hidden="true" />
                                        {workspace.favorites.includes(selectedStock.code) ? "移出自选" : "加入自选"}
                                    </Button>
                                    <Button
                                        onClick={() => {
                                            workspace.setMultiCodes(
                                                Array.from(
                                                    new Set([...workspace.multiCodes, selectedStock.code]),
                                                ).slice(0, 9),
                                            );
                                            workspace.setActiveTab("multi");
                                            workspace.closeDialog();
                                        }}
                                    >
                                        加入多股同屏
                                    </Button>
                                </div>
                            </div>);
}

function Stat({ label, tone = "", value }: { label: string; tone?: string; value: string }): JSX.Element {
    return (
        <div className="border-border bg-background/35 rounded-lg border p-3">
            <span className="text-muted-foreground text-[10px]">{label}</span>
            <strong className={`mt-1 block text-lg ${tone}`}>{value}</strong>
        </div>
    );
}
