import { z } from "zod";

const marketDate = z.iso.date();
const marketDataVersion = z.string().regex(/^[a-f0-9]{64}$/);
const marketYear = z.number().int().min(1900).max(9999);
const marketTimeframe = z.enum(["1d", "1w", "1mo", "3mo", "1y"]);
const marketSymbol = z.string().regex(/^(sh|sz|bj)\.\d{6}$/);

export const MarketBarSchema = z
    .object({
        time: marketDate,
        open: z.number().finite().positive(),
        high: z.number().finite().positive(),
        low: z.number().finite().positive(),
        close: z.number().finite().positive(),
        raw_close: z.number().finite().positive(),
        volume: z.number().finite().nonnegative(),
        factor: z.number().finite().positive().optional(),
    })
    .passthrough();

export const MarketBarsViewSchema = z
    .object({
        symbol: marketSymbol,
        asof: marketDate,
        bars: z.array(MarketBarSchema),
        sessions: z.array(marketDate),
        price_basis: z.string().min(1),
        data_version: marketDataVersion,
        timeframe: marketTimeframe.optional(),
    })
    .passthrough();

/** Annual pages share one complete-history identity; only OHLC and sessions are sliced. */
export const MarketBarsPageSchema = z
    .object({
        schema_version: z.literal(1),
        source: z.enum(["tdx", "akshare"]),
        symbol: marketSymbol,
        requested_asof: marketDate,
        resolved_asof: marketDate,
        timeframe: marketTimeframe,
        data_version: marketDataVersion,
        year: marketYear,
        next_year: marketYear.nullable(),
        has_more: z.boolean(),
        available_years: z.array(marketYear).min(1),
        history_start: marketDate,
        history_end: marketDate,
        total_bars: z.number().int().positive(),
        view: MarketBarsViewSchema,
    })
    .superRefine((page, context) => {
        const reject = (path: (string | number)[], message: string): void => {
            context.addIssue({ code: "custom", path, message });
        };
        if (page.view.symbol !== page.symbol || page.view.asof !== page.resolved_asof) {
            reject(["view"], "Page and view must identify the same market selection");
        }
        if (page.view.data_version !== page.data_version) {
            reject(["view", "data_version"], "Market-data versions must match");
        }
        if (page.view.timeframe !== undefined && page.view.timeframe !== page.timeframe) {
            reject(["view", "timeframe"], "Market timeframes must match");
        }
        if (page.history_start > page.history_end || page.history_end > page.requested_asof) {
            reject(["history_end"], "History bounds must lie within the requested cutoff");
        }
        if (page.resolved_asof !== page.history_end) {
            reject(["resolved_asof"], "Resolved cutoff must identify the last available bar");
        }
        if (page.available_years.some((year, index, years) => index > 0 && year >= years[index - 1]!)) {
            reject(["available_years"], "Available years must be unique and descending");
        }
        const nextYear = page.available_years.find((year) => year < page.year) ?? null;
        if (page.next_year !== nextYear || page.has_more !== (nextYear !== null)) {
            reject(["next_year"], "Pagination must advance to the next available older year");
        }
        if (page.view.bars.length > page.total_bars) {
            reject(["total_bars"], "A page cannot exceed the complete history");
        }
        for (const [index, bar] of page.view.bars.entries()) {
            if (
                Number(bar.time.slice(0, 4)) !== page.year ||
                bar.time < page.history_start ||
                bar.time > page.history_end
            ) {
                reject(["view", "bars", index, "time"], "Bar must belong to this annual page and history");
            }
            if (index > 0 && bar.time <= page.view.bars[index - 1]!.time) {
                reject(["view", "bars", index, "time"], "Bars must be unique and chronological");
            }
        }
        for (const [index, session] of page.view.sessions.entries()) {
            if (Number(session.slice(0, 4)) !== page.year) {
                reject(["view", "sessions", index], "Session must belong to this annual page");
            }
            if (index > 0 && session <= page.view.sessions[index - 1]!) {
                reject(["view", "sessions", index], "Sessions must be unique and chronological");
            }
        }
    });

export type TMarketBar = z.infer<typeof MarketBarSchema>;
export type TMarketBarsView = z.infer<typeof MarketBarsViewSchema>;
export type TMarketBarsPage = z.infer<typeof MarketBarsPageSchema>;
