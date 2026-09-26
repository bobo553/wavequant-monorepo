/** Validate navigation before any chart request is issued. */
export function parseResearchLink(search) {
    const params = new URLSearchParams(search);
    if (!params.has("symbol")) return null;
    for (const name of ["symbol", "asof", "source"]) {
        if (params.getAll(name).length > 1) throw new Error("行情链接包含重复参数");
    }
    const symbol = params.get("symbol");
    if (!/^(sh\.(60|68)\d{4}|sz\.(00|30)\d{4}|bj\.(43|82|83|87|88|92)\d{4})$/.test(symbol || "")) {
        throw new Error("行情链接股票代码无效");
    }
    const asof = params.get("asof");
    if (
        asof !== null &&
        (!/^\d{4}-\d{2}-\d{2}$/.test(asof) ||
            !Number.isFinite(Date.parse(asof)) ||
            new Date(asof).toISOString().slice(0, 10) !== asof)
    ) {
        throw new Error("行情链接日期无效");
    }
    const source = params.get("source") || "akshare";
    if (!["akshare", "tdx"].includes(source)) throw new Error("行情链接数据源无效");
    return { symbol, asof, source };
}

export function resolveResearchLink(link, catalogs) {
    if (!["akshare", "tdx"].includes(link.source)) throw new Error("行情链接数据源无效");
    const catalog = catalogs[link.source];
    if (!catalog?.with_daily || !catalog.stocks?.some((stock) => stock.symbol === link.symbol && stock.has_data !== false))
        throw new Error(`${link.symbol} 在所选${link.source === "akshare" ? "AkShare" : "通达信"}目录中暂无可用行情，请更新股票目录后重试。`);
    return { ...link, fallback: false };
}
