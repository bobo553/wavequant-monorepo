import { z } from "zod";

export const BuyIntervalLevelSchema = z.enum(["1", "2", "3"]).default("2");
export type TBuyIntervalLevel = z.infer<typeof BuyIntervalLevelSchema>;

export const WatchlistSnapshotSchema = z.object({
    schemaVersion: z.literal(1),
    groups: z
        .array(
            z.object({
                id: z.string().min(1).max(64),
                name: z.string().min(1).max(24),
                position: z.number().int().nonnegative(),
                protected: z.boolean(),
            }),
        )
        .min(1)
        .max(20),
    memberships: z.array(
        z.object({
            groupId: z.string().min(1).max(64),
            symbol: z.string().regex(/^(?:sh|sz|bj)\.\d{6}$/),
            name: z.string().max(80),
            position: z.number().int().nonnegative(),
        }),
    ),
});

export const WatchlistSettingsSchema = z.object({
    enabled: z.boolean(),
    context: z.object({
        run: z.string().min(1).max(128),
        variant: z.string().min(1).max(128),
        scenario: z.string().min(1).max(128),
        source: z.enum(["akshare", "tdx"]),
        start: z.iso.date(),
        volume_filter: z.enum(["true", "false"]),
        net_reward_risk_filter: z.enum(["true", "false"]),
        shallow_base_breakout_enabled: z.enum(["true", "false"]),
        n_target_trend_confirmation_enabled: z.enum(["true", "false"]).default("false"),
        ordinary_trend_completion_enabled: z.enum(["true", "false"]).default("false"),
        buy_interval_level: BuyIntervalLevelSchema,
        trading_enabled: z.literal("false").default("false"),
        enable_secondary_reversal: z.string().default("[false,false,true,false]").refine(raw=>{
            try { const values:unknown=JSON.parse(raw); return Array.isArray(values)&&values.length===4&&values.every(value=>typeof value==="boolean"); }
            catch { return false; }
        }),
        initial_capital: z
            .string()
            .refine((value) => Number.isFinite(Number(value)) && Number(value) > 0 && Number(value) <= 1e9),
        max_position_weight: z
            .string()
            .refine((value) => Number.isFinite(Number(value)) && Number(value) > 0 && Number(value) <= 1),
    }),
});

export const WatchlistDocumentSchema = z.object({
    revision: z.number().int().nonnegative(),
    snapshot: WatchlistSnapshotSchema,
    settings: WatchlistSettingsSchema.nullable(),
});

export type TWatchlistSnapshot = z.infer<typeof WatchlistSnapshotSchema>;
export type TWatchlistSettings = z.infer<typeof WatchlistSettingsSchema>;
export type TWatchlistDocument = z.infer<typeof WatchlistDocumentSchema>;

export const WatchlistMembersMutationSchema = z.object({
    action: z.enum(["add", "remove"]),
    groupId: z.string().min(1).max(64),
    stocks: z
        .array(
            z.object({
                symbol: z.string().regex(/^(?:sh|sz|bj)\.\d{6}$/),
                name: z.string().max(80).optional(),
            }),
        )
        .min(1),
});
export type TWatchlistMembersMutation = z.infer<typeof WatchlistMembersMutationSchema>;

/** Rust趋势计算执行开关；不改变策略规则，关闭时从零重算趋势状态机。 */
export const TrendExecutionOptionsSchema = z.object({
    trend_offset_enabled: z.enum(["true", "false"]).default("true"),
});
export type TTrendExecutionOptions = z.infer<typeof TrendExecutionOptionsSchema>;
