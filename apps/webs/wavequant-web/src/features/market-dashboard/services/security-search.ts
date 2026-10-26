import type { TMarketCatalogView } from "./market-catalog";

export interface ISecuritySearchItem {
    symbol: string;
    name: string;
    kind: "stock" | "index";
    aliases: string[];
    href: string;
}

// 保留交易所身份，sh.000001上证指数与sz.000001平安银行分别显示。
const indices = [
    ["sh.000001", "上证指数", "沪指 上证综指 上证综合指数 szzs sh000001"],
    ["sz.399001", "深证成指", "深成指 深证成份指数 深证指数 sz399001"],
    ["sz.399006", "创业板指", "创业板指数 cyb sz399006"],
    ["sh.000300", "沪深300", "沪深300指数 hs300 sh000300"],
    ["sh.000016", "上证50", "上证50指数 sz50 sh000016"],
    ["sh.000905", "中证500", "中证500指数 zz500 sh000905"],
    ["sh.000852", "中证1000", "中证1000指数 zz1000 sh000852"],
    ["sh.000688", "科创50", "科创50指数 科创板指数 kc50 sh000688"],
    ["sz.399005", "中小100", "中小板指 中小板指数 sz399005"],
    ["bj.899050", "北证50", "北证50指数 北交所指数 bz50 bj899050"],
] as const;

export function securitySearchItems(catalog: TMarketCatalogView | null): ISecuritySearchItem[] {
    const rows: ISecuritySearchItem[] = indices.map(([symbol, name, aliases]) => ({
        symbol, name, aliases: aliases.split(" "), kind: "index",
        href: `https://quote.eastmoney.com/zs${symbol.slice(3)}.html`,
    }));
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
