"use client";
import { useMemo, useState, type JSX } from "react";
import { Input } from "@repo/design-system-web/components";
import { IconSearch } from "@tabler/icons-react";
import { stockQuotes, sectorHeat } from "../market-data";
import { useMarketWorkspace } from "../market-workspace-context";

/** 搜索数据和键盘结果选择仅在搜索框打开时下载。 */
export default function MarketSearchDialog(): JSX.Element {
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

    function selectResult(): void {
        const stock = results[activeResult];
        if (stock) workspace.openStock(stock.code);
    }

    return (<div>
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
                            </div>);
}
