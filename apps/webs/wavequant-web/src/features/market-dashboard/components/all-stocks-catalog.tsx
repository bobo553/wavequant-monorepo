"use client";

import { type JSX, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@repo/design-system-web/components";

import { readMarketCatalog, type TCatalogSource, type TMarketCatalogView } from "../services/market-catalog";

/** 全市场目录独立于研究页；挂载和切换来源均不自动请求。 */
export function AllStocksCatalog(): JSX.Element {
    const [source, setSource] = useState<TCatalogSource>("akshare");
    const [catalog, setCatalog] = useState<TMarketCatalogView | null>(null);
    const [query, setQuery] = useState("");
    const [limit, setLimit] = useState(100);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const controller = useRef<AbortController | null>(null);
    useEffect(() => () => controller.current?.abort(), []);
    const matches = useMemo(() => {
        const tokens = query.normalize("NFKC").trim().toLowerCase().split(/\s+/).filter(Boolean);
        return (catalog?.stocks ?? []).filter((stock) =>
            tokens.every((token) => `${stock.symbol} ${stock.name ?? ""}`.toLowerCase().includes(token)));
    }, [catalog, query]);

    async function load(): Promise<void> {
        controller.current?.abort();
        const current = new AbortController();
        controller.current = current;
        setLoading(true);
        setError("");
        try {
            const result = await readMarketCatalog(source, current.signal, (fresh) => {
                if (!current.signal.aborted) setCatalog(fresh);
            });
            if (!current.signal.aborted) setCatalog(result);
        } catch (cause: unknown) {
            if (!current.signal.aborted) setError(cause instanceof Error ? cause.message : "目录加载失败，请重试");
        } finally {
            if (!current.signal.aborted) setLoading(false);
        }
    }

    return (
        <section className="border-border bg-card rounded-lg border p-4" aria-label="全市场股票目录">
            <div className="mb-4 flex flex-wrap items-end gap-3">
                <div className="mr-auto">
                    <h2 className="text-base font-semibold">全市场股票</h2>
                    <p className="text-muted-foreground mt-1 text-xs">按需加载沪深北股票目录，选择股票后进入行情与复盘。</p>
                </div>
                <label className="grid gap-1 text-xs">数据源
                    <select aria-label="目录数据源" value={source}
                        className="border-input bg-background h-9 rounded-md border px-2"
                        onChange={(event) => {
                            controller.current?.abort();
                            setSource(event.currentTarget.value === "tdx" ? "tdx" : "akshare");
                            setCatalog(null); setLoading(false); setError(""); setLimit(100);
                        }}>
                        <option value="akshare">AkShare · 在线目录</option>
                        <option value="tdx">通达信 · 本地目录</option>
                    </select>
                </label>
                <Button type="button" variant="outline" disabled={loading} onClick={() => void load()}>
                    {loading ? "正在加载…" : catalog ? "刷新目录" : "加载目录"}
                </Button>
            </div>
            <label className="mb-3 grid gap-1 text-xs">股票名称或代码
                <input type="search" aria-label="搜索全市场股票" value={query} disabled={!catalog}
                    className="border-input bg-background h-9 rounded-md border px-3"
                    placeholder="例如 海洋王 / 002724"
                    onChange={(event) => { setQuery(event.currentTarget.value); setLimit(100); }} />
            </label>
            <p role="status" className="text-muted-foreground mb-3 text-xs">
                {error || (catalog ? `${matches.length} / ${catalog.stocks.length} 只股票${catalog.catalog_cache === "offline" ? " · 使用缓存，服务器暂不可用" : catalog.catalog_cache === "stale-while-revalidate" ? " · 已显示缓存，后台校验中" : ""}`
                    : loading ? "正在读取股票目录…" : "尚未请求目录，点击加载目录后获取。")}
            </p>
            {catalog && (matches.length ? (
                <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-label="全市场股票列表">
                    {matches.slice(0, limit).map((stock) => (
                        <li key={stock.symbol} className="border-border rounded-md border p-3">
                            {stock.has_data === false ? (
                                <span className="text-muted-foreground">{stock.name || stock.symbol} · 暂无可用日线</span>
                            ) : (
                                <a className="text-primary flex items-center justify-between gap-2 underline-offset-4 hover:underline"
                                    href={`/research?${new URLSearchParams({ page: "workspace", symbol: stock.symbol, source })}`}>
                                    <strong className="truncate text-sm">{stock.name || stock.symbol}</strong>
                                    <span className="shrink-0 text-xs">{stock.symbol}</span>
                                </a>
                            )}
                        </li>
                    ))}
                </ul>
            ) : <p className="text-muted-foreground py-8 text-center text-sm">没有匹配的股票，请更换名称或代码。</p>)}
            {matches.length > limit && <Button className="mt-4" variant="outline" onClick={() => setLimit((count) => count + 100)}>
                加载更多（已显示 {Math.min(limit, matches.length)} / {matches.length}）
            </Button>}
        </section>
    );
}
