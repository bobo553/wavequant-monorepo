"use client";

import { useEffect, useId, useMemo, useRef, useState, type JSX } from "react";
import { Input } from "@repo/design-system-web/components";
import { IconSearch } from "@tabler/icons-react";
import { readMarketCatalog, type TMarketCatalogView } from "../services/market-catalog";
import { searchSecurities, securitySearchItems } from "../services/security-search";

/** 打开时按需读取真实目录；输入只筛选本地索引，不逐键请求行情。 */
export default function MarketSearchDialog(): JSX.Element {
    const [query, setQuery] = useState("");
    const [active, setActive] = useState(0);
    const [catalog, setCatalog] = useState<TMarketCatalogView | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [attempt, setAttempt] = useState(0);
    const listId = useId();
    const list = useRef<HTMLDivElement>(null);
    useEffect(() => {
        const controller = new AbortController();
        void readMarketCatalog("akshare", controller.signal, fresh => {
            if (!controller.signal.aborted) { setCatalog(fresh); setActive(0); }
        }).then(value => {
            if (!controller.signal.aborted) { setCatalog(value); setError(""); }
        }).catch((cause: unknown) => {
            if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "股票目录读取失败");
        }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [attempt]);
    const rows = useMemo(() => securitySearchItems(catalog), [catalog]);
    const matches = useMemo(() => searchSecurities(rows, query), [rows, query]);
    const results = matches.slice(0, query.trim() ? 50 : 12);
    const selected = Math.min(active, Math.max(0, results.length - 1));
    useEffect(() => {
        list.current?.querySelector<HTMLElement>(`[data-result-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
    }, [selected]);

    return <div>
        <label className="border-border relative block border-b p-4">
            <span className="sr-only">搜索股票或指数名称、代码</span>
            <IconSearch className="text-muted-foreground absolute top-1/2 left-7 -translate-y-1/2" aria-hidden="true" />
            <Input autoFocus value={query} role="combobox" aria-expanded={results.length > 0}
                aria-autocomplete="list" aria-controls={listId} aria-describedby={`${listId}-status`}
                aria-activedescendant={results.length ? `${listId}-${selected}` : undefined}
                onChange={event => { setQuery(event.target.value); setActive(0); }}
                onKeyDown={event => {
                    if (event.nativeEvent.isComposing) return;
                    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                        event.preventDefault();
                        setActive(index => Math.max(0, Math.min(results.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))));
                    }
                    if (event.key === "Enter") {
                        event.preventDefault();
                        list.current?.querySelector<HTMLAnchorElement>(`[data-result-index="${selected}"]`)?.click();
                    }
                }} className="h-11 pl-10" placeholder="输入名称或代码，例如 上证 / 海洋王 / 600519" />
        </label>
        <div className="flex flex-wrap gap-2 px-4 pt-3" aria-label="搜索示例">
            {["上证指数", "深证成指", "海洋王", "600519"].map(value => <button key={value} type="button"
                className="border-border text-muted-foreground hover:text-primary rounded border px-2 py-1 text-xs"
                onClick={() => { setQuery(value); setActive(0); }}>{value}</button>)}
        </div>
        <p id={`${listId}-status`} role="status" aria-live="polite" className="text-muted-foreground px-4 pt-3 text-xs">
            {error ? `股票目录暂不可用：${error}；已知指数仍可搜索。` : loading && !catalog ? "正在读取股票目录，指数可先搜索…" : `${matches.length} 个匹配 · ${rows.length} 只证券${catalog?.catalog_cache === "offline" ? " · 使用本地缓存" : ""}`}
            {error && <button type="button" className="text-primary ml-2 underline" onClick={() => {
                setLoading(true); setError(""); setAttempt(value => value + 1);
            }}>重试</button>}
        </p>
        <div ref={list} id={listId} role="listbox" aria-label="股票与指数联想结果" className="max-h-80 min-h-36 overflow-auto p-2">
            {results.map((stock, index) => <a id={`${listId}-${index}`} key={stock.symbol} href={stock.href}
                role="option" aria-selected={index === selected} data-result-index={index} tabIndex={-1}
                onMouseEnter={() => setActive(index)}
                className={`flex w-full items-center justify-between rounded-lg px-4 py-3 text-left ${index === selected ? "bg-primary/12 ring-primary/30 ring-1" : "hover:bg-secondary/60"}`}>
                <span><strong className="block text-sm">{stock.name}</strong><small className="text-muted-foreground">{stock.symbol}</small></span>
                <span className="text-muted-foreground text-xs">{stock.kind === "index" ? "指数 · 行情与趋势" : "股票 · 行情与复盘"}</span>
            </a>)}
            {!results.length && <div className="text-muted-foreground py-8 text-center text-sm">
                {loading ? "正在读取完整股票目录…" : "没有匹配的证券，请换用名称、六位代码或交易所代码。"}
            </div>}
        </div>
        <p className="border-border text-muted-foreground border-t px-5 py-3 text-[10px]">
            ↑↓ 选择 · Enter 打开 · Escape 关闭 · 股票与指数均进入研究页
            {matches.length > results.length && ` · 已显示前 ${results.length} 项，请补充名称或代码`}
        </p>
    </div>;
}
