"use client";

import { type JSX, useMemo, useState } from "react";

import { Button, Card, CardContent, Input } from "@repo/design-system-web/components";
import { IconBell, IconCheck, IconSearch, IconStar, IconX } from "@tabler/icons-react";

import { MarketSettingsPanel } from "@/features/market-dashboard/components/market-settings-panel";
import { sectorHeat, stockQuotes } from "@/features/market-dashboard/market-data";
import type { TWatchGroup } from "@/features/market-dashboard/market-types";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const groups: Array<{ id: TWatchGroup; label: string }> = [
    { id: "core", label: "核心关注" },
    { id: "verify", label: "待验证" },
    { id: "risk", label: "风险观察" },
];

/** 提供搜索、个股核验、通知、口径和设置等低频浮层。 */
export function MarketWorkspaceOverlay(): JSX.Element | null {
    const workspace = useMarketWorkspace();
    const [query, setQuery] = useState("");
    const [activeResult, setActiveResult] = useState(0);
    const results = useMemo(() => {
        const keyword = query.trim().toLowerCase();
        if (!keyword) return stockQuotes.slice(0, 8);
        return stockQuotes.filter((stock, index) => {
            const sector = sectorHeat[index % sectorHeat.length]?.name ?? "综合题材";
            return [stock.name, stock.code, stock.board, sector].some((value) => value.toLowerCase().includes(keyword));
        });
    }, [query]);

    if (!workspace.dialog && !workspace.toast) return null;

    const stockDialog = workspace.dialog?.kind === "stock" ? workspace.dialog : null;
    const selectedStock = stockDialog ? stockQuotes.find((stock) => stock.code === stockDialog.code) : undefined;

    function selectResult(): void {
        const stock = results[activeResult];
        if (stock) workspace.openStock(stock.code);
    }

    return (
        <>
            {workspace.dialog ? (
                <div
                    className="fixed inset-0 z-50 grid place-items-center bg-slate-950/75 p-3 backdrop-blur-sm"
                    role="presentation"
                >
                    <section
                        role="dialog"
                        aria-modal="true"
                        aria-label={workspace.dialog.kind === "search" ? "搜索股票或题材" : "工作区对话框"}
                        className="border-border bg-card max-h-[92vh] w-full max-w-2xl overflow-auto rounded-xl border shadow-2xl"
                    >
                        <header className="border-border sticky top-0 z-10 flex items-center justify-between border-b bg-inherit px-5 py-4">
                            <h2 className="text-base font-semibold">
                                {workspace.dialog.kind === "search" ? "找股票 / 题材" : null}
                                {workspace.dialog.kind === "settings" ? "界面与工作区设置" : null}
                                {workspace.dialog.kind === "notifications" ? "提醒中心" : null}
                                {workspace.dialog.kind === "info" ? workspace.dialog.title : null}
                                {selectedStock ? `${selectedStock.name} · ${selectedStock.code}` : null}
                            </h2>
                            <Button
                                variant="ghost"
                                size="icon-sm"
                                onClick={workspace.closeDialog}
                                aria-label="关闭对话框"
                            >
                                <IconX aria-hidden="true" />
                            </Button>
                        </header>

                        {workspace.dialog.kind === "search" ? (
                            <div>
                                <label className="border-border relative block border-b p-4">
                                    <span className="sr-only">名称、SIM 代码、板块或题材</span>
                                    <IconSearch
                                        className="text-muted-foreground absolute top-1/2 left-7 -translate-y-1/2"
                                        aria-hidden="true"
                                    />
                                    <Input
                                        autoFocus
                                        value={query}
                                        onChange={(event) => {
                                            setQuery(event.target.value);
                                            setActiveResult(0);
                                        }}
                                        onKeyDown={(event) => {
                                            if (event.nativeEvent.isComposing) return;
                                            if (event.key === "ArrowDown") {
                                                event.preventDefault();
                                                setActiveResult((index) => Math.min(results.length - 1, index + 1));
                                            }
                                            if (event.key === "ArrowUp") {
                                                event.preventDefault();
                                                setActiveResult((index) => Math.max(0, index - 1));
                                            }
                                            if (event.key === "Enter") {
                                                event.preventDefault();
                                                selectResult();
                                            }
                                        }}
                                        className="h-11 pl-10"
                                        placeholder="中文 / SIM 代码 / 板块 / 题材"
                                    />
                                </label>
                                <div className="max-h-96 overflow-auto p-2">
                                    {results.length ? (
                                        results.map((stock, index) => (
                                            <button
                                                type="button"
                                                key={stock.code}
                                                onMouseEnter={() => setActiveResult(index)}
                                                onClick={() => workspace.openStock(stock.code)}
                                                className={`flex w-full items-center justify-between rounded-lg px-4 py-3 text-left ${index === activeResult ? "bg-primary/12 ring-primary/30 ring-1" : "hover:bg-secondary/60"}`}
                                            >
                                                <span>
                                                    <strong className="block text-sm">{stock.name}</strong>
                                                    <small className="text-muted-foreground">
                                                        {stock.code} · {stock.board}
                                                    </small>
                                                </span>
                                                <span className="text-right">
                                                    <b className="block text-rose-400">{stock.change}</b>
                                                    <small className="text-muted-foreground">{stock.status}</small>
                                                </span>
                                            </button>
                                        ))
                                    ) : (
                                        <div className="py-12 text-center">
                                            <IconSearch className="text-muted-foreground mx-auto" />
                                            <p className="mt-3 text-sm">没有匹配的股票或题材</p>
                                            <p className="text-muted-foreground mt-1 text-xs">
                                                试试 SIM001、人形机器人或主板。
                                            </p>
                                        </div>
                                    )}
                                </div>
                                <p className="border-border text-muted-foreground border-t px-5 py-3 text-[10px]">
                                    ↑↓ 选择 · Enter 打开 · Escape 关闭 · 搜索集合仅为 48 只虚构证券
                                </p>
                            </div>
                        ) : null}

                        {selectedStock ? (
                            <div className="space-y-4 p-5">
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
                            </div>
                        ) : null}

                        {workspace.dialog.kind === "info" ? (
                            <p className="text-muted-foreground p-5 text-sm leading-7 whitespace-pre-line">
                                {workspace.dialog.body}
                            </p>
                        ) : null}

                        {workspace.dialog.kind === "notifications" ? (
                            <div className="divide-border divide-y p-2">
                                {[
                                    "云启算力达到 7 连板暂定状态",
                                    "新澜医药 13:27 完成回封",
                                    "当前范围出现 4 只炸板未回封",
                                ].map((item, index) => (
                                    <div key={item} className="flex gap-3 px-3 py-4">
                                        <IconBell
                                            className={index ? "text-muted-foreground" : "text-primary"}
                                            aria-hidden="true"
                                        />
                                        <div>
                                            <p className="text-sm">{item}</p>
                                            <small className="text-muted-foreground">
                                                仅页面内演示提醒 · 不发送系统通知
                                            </small>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : null}

                        {workspace.dialog.kind === "settings" ? <MarketSettingsPanel /> : null}
                    </section>
                </div>
            ) : null}
            {workspace.toast ? (
                <div
                    role="status"
                    className="border-primary/30 bg-secondary fixed bottom-10 left-1/2 z-[60] -translate-x-1/2 rounded-lg border px-4 py-3 text-xs shadow-xl"
                >
                    {workspace.toast}
                </div>
            ) : null}
        </>
    );
}

function Stat({ label, tone = "", value }: { label: string; tone?: string; value: string }): JSX.Element {
    return (
        <div className="border-border bg-background/35 rounded-lg border p-3">
            <span className="text-muted-foreground text-[10px]">{label}</span>
            <strong className={`mt-1 block text-lg ${tone}`}>{value}</strong>
        </div>
    );
}
