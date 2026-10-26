import type { TMarketCatalogView } from "./market-catalog";
import { marketIndices } from "../../../../public/market-indices.js";

export interface ISecuritySearchItem {
    symbol: string;
    name: string;
    kind: "stock" | "index";
    aliases: string[];
    href: string;
}

export function securitySearchItems(catalog: TMarketCatalogView | null): ISecuritySearchItem[] {
    const rows = marketIndices.map<ISecuritySearchItem>(([symbol, name, aliases]) => {
        if (!symbol || !name || aliases === undefined) throw new Error("指数目录缺少必要身份字段");
        return { symbol, name, aliases: aliases.split(" "), kind: "index",
            href: `/research?${new URLSearchParams({ page: "workspace", symbol, source: "akshare" })}` };
    });
    const seen = new Set(rows.map(row => row.symbol));
    for (const stock of catalog?.stocks ?? []) {
        if (seen.has(stock.symbol)) continue;
        seen.add(stock.symbol);
        rows.push({ symbol: stock.symbol, name: stock.name || stock.symbol, kind: "stock", aliases: [],
            href: `/research?${new URLSearchParams({ page: "workspace", symbol: stock.symbol, source: "akshare" })}` });
    }
    return rows;
}

/** 名称/完整代码优先；仅截取显示结果，不截断可搜索目录。 */
export function searchSecurities(rows: ISecuritySearchItem[], query: string): ISecuritySearchItem[] {
    const normalized = query.normalize("NFKC").trim().toLowerCase();
    const tokens = normalized.split(/\s+/).filter(Boolean);
    const score = (row: ISecuritySearchItem): number => {
        const code = row.symbol.slice(3);
        if ([row.name, row.symbol, code].some(value => value.toLowerCase() === normalized)) return 0;
        if (row.name.toLowerCase().startsWith(normalized) || code.startsWith(normalized)) return 1;
        return 2;
    };
    return rows.filter(row => {
        const text = `${row.symbol} ${row.symbol.replace(".", "")} ${row.name} ${row.aliases.join(" ")}`.toLowerCase();
        return tokens.every(token => text.includes(token));
    }).sort((a, b) => score(a) - score(b));
}
