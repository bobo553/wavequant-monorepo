/** JS缓存边界保持unknown，由消费方校验其所需字段。 */
export function loadStockCatalog(source: string, path: string, options?: {
    fetcher?: typeof fetch;
    staleWhileRevalidate?: boolean;
    onRefresh?: (catalog: unknown) => void;
}): Promise<unknown>;

export function marketTimeframeCacheKey(source: string, symbol: string, requestedAsOf: string, timeframe: string,
    nTargetTrendConfirmationEnabled?: boolean, ordinaryTrendCompletionEnabled?: boolean): string;
export interface MarketTimeframeCacheEntry {
    key: string;
    etag: string;
    bundle: unknown;
    cachedAt?: string;
}
export const marketTimeframeStorage: {
    get(key: string): Promise<MarketTimeframeCacheEntry | null>;
    put(entry: MarketTimeframeCacheEntry): Promise<void>;
};
