/** 指数与个股使用完整交易所身份；仅在线行情支持这些指数代码范围。 */
export function isIndexSymbol(symbol) {
    return /^(sh\.000|sz\.(399|980)|bj\.899)\d{3}$/.test(symbol ?? "");
}
/** @type {ReadonlyArray<readonly [string, string, string]>} */
export const marketIndices = [
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
];
export function indexName(symbol) { return marketIndices.find(row => row[0] === symbol)?.[1]; }
export function priceUnit(symbol) { return isIndexSymbol(symbol) ? "点" : "元"; }
