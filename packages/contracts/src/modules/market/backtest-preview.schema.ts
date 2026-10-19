import { z } from "zod";

import { MarketBarSchema } from "./market-bars.schema";

/** 同一已完成交易日的趋势、决策及模拟账户快照。 */
export const BacktestPreviewSchema = z
    .object({
        job: z.string().min(1),
        revision: z.number().int().nonnegative(),
        preview: z
            .object({
                symbol: z.string().min(1),
                asof: z.iso.date(),
                timeframe: z.literal("1d"),
                processed: z.number().int().positive(),
                total: z.number().int().positive(),
                phase: z.literal("daily"),
                bars: z.array(MarketBarSchema),
                // 行情底图与回测前缀分开，禁止把完整行情当作已完成回测。
                market_bars: z.array(MarketBarSchema).optional(),
                market_asof: z.iso.date().optional(),
                theory: z.record(z.string(), z.unknown()),
                markers: z.array(z.record(z.string(), z.unknown())),
                signals: z.array(z.record(z.string(), z.unknown())),
                orders: z.array(z.record(z.string(), z.unknown())),
                trades: z.array(z.record(z.string(), z.unknown())),
                audit: z.array(z.record(z.string(), z.unknown())),
                curve: z.array(z.record(z.string(), z.unknown())),
                metrics: z.record(z.string(), z.unknown()),
                backtest: z.record(z.string(), z.unknown()),
            })
            .nullable(),
    })
    .superRefine((value, context) => {
        const preview = value.preview;
        if (preview && (preview.market_bars !== undefined || preview.market_asof !== undefined)) {
            const market = preview.market_bars;
            const fields = ["time", "open", "high", "low", "close", "volume", "factor", "raw_close"] as const;
            if (
                !market ||
                market.length !== preview.total ||
                market.at(-1)?.time !== preview.market_asof ||
                market.some((bar, index) => index > 0 && bar.time <= market[index - 1]!.time) ||
                preview.bars.some((bar, index) => fields.some((key) => bar[key] !== market[index]?.[key]))
            ) {
                context.addIssue({
                    code: "custom",
                    path: ["preview", "market_bars"],
                    message: "Full market candles must contain the identical completed prefix",
                });
            }
        }
        if (
            preview &&
            (preview.processed !== value.revision ||
                preview.processed > preview.total ||
                preview.bars.length !== preview.processed ||
                preview.bars.at(-1)?.time !== preview.asof ||
                preview.theory.asof !== preview.asof ||
                preview.curve.length !== preview.processed ||
                typeof preview.curve.at(-1)?.timestamp !== "string" ||
                String(preview.curve.at(-1)?.timestamp).slice(0, 10) !== preview.asof)
        ) {
            context.addIssue({
                code: "custom",
                path: ["preview"],
                message: "Preview must match its completed market prefix",
            });
        }
        if (
            preview &&
            [...preview.signals, ...preview.orders, ...preview.audit, ...preview.curve].some(
                (row) => typeof row.timestamp !== "string" || row.timestamp.slice(0, 10) > preview.asof,
            )
        ) {
            context.addIssue({
                code: "custom",
                path: ["preview"],
                message: "Preview cannot contain future account records",
            });
        }
        if (
            preview &&
            (preview.markers.some((row) => typeof row.time !== "string" || row.time > preview.asof) ||
                preview.trades.some(
                    (row) => typeof row.exit_time !== "string" || row.exit_time.slice(0, 10) > preview.asof,
                ))
        ) {
            context.addIssue({
                code: "custom",
                path: ["preview"],
                message: "Preview cannot contain future markers or trades",
            });
        }
    });

export type TBacktestPreview = z.infer<typeof BacktestPreviewSchema>;
