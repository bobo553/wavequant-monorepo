/** 达到一饱至二吐前为普通 A；达到二吐（含相等）为强势 A。 */
export function classifyAAttack(high, oneP, twoT) {
    if (![high, oneP, twoT].every((price) => Number.isFinite(price) && price > 0) || twoT <= oneP || high < oneP)
        return null;
    return high >= twoT ? "strong" : "ordinary";
}

export function isAOriginBroken(bar, origin) {
    return Number.isFinite(bar?.low) && Number.isFinite(origin) && bar.low < origin;
}
