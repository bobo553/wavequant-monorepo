import { z } from "zod";

import { loadStockCatalog } from "../../../../public/catalog-cache.js";

// 仅投影目录视图需要的字段；不把行情或演示报价混入真实股票目录。
const catalogViewSchema = z.object({
    stocks: z.array(z.object({
        symbol: z.string().regex(/^(sh|sz|bj)\.\d{6}$/),
        name: z.string().optional(),
        has_data: z.boolean().optional(),
    })),
    catalog_cache: z.string().optional(),
});
export type TMarketCatalogView = z.infer<typeof catalogViewSchema>;
export type TCatalogSource = "akshare" | "tdx";

/** 只由用户加载动作调用，沿用来源隔离的IndexedDB目录及ETag校验。 */
export async function readMarketCatalog(
    source: TCatalogSource,
    signal: AbortSignal,
    onRefresh: (catalog: TMarketCatalogView) => void,
): Promise<TMarketCatalogView> {
    const raw: unknown = await loadStockCatalog(source, `/api/${source}-catalog`, {
        staleWhileRevalidate: true,
        fetcher: (input, options) => fetch(input, {
            ...options,
            signal: AbortSignal.any([signal, ...(options?.signal ? [options.signal] : [])]),
        }),
        onRefresh: (value: unknown) => {
            if (!signal.aborted) onRefresh(catalogViewSchema.parse(value));
        },
    });
    return catalogViewSchema.parse(raw);
}
