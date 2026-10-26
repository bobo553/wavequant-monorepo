import { indexName } from "./market-indices.js";
/** 研究页只保留自选与显式证券，不读取全市场目录；实际行情可用性由单证券接口确认。 */
export function researchSymbols(memberships, explicitSymbol = null, previous = []) {
    const stocks = new Map();
    for (const member of memberships) {
        if (!/^(sh|sz|bj)\.\d{6}$/.test(member.symbol)) continue;
        if (!stocks.has(member.symbol)) stocks.set(member.symbol, {
            symbol: member.symbol, name: member.name || member.symbol,
            has_data: true, status: "available_on_demand",
        });
    }
    if (explicitSymbol && !stocks.has(explicitSymbol)) {
        const known = previous.find((stock) => stock.symbol === explicitSymbol);
        stocks.set(explicitSymbol, known || { symbol: explicitSymbol, name: indexName(explicitSymbol) || explicitSymbol,
            has_data: true, status: "available_on_demand" });
    }
    return [...stocks.values()];
}

/** 无全目录末日元数据时，请求中国当前日期，由服务端解析最近可用交易日。 */
export function marketRequestDate(now = new Date()) {
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(now);
    const value = (type) => parts.find((part) => part.type === type).value;
    return `${value("year")}-${value("month")}-${value("day")}`;
}
