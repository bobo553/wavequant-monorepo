import { z } from "zod";

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
    memberships: z
        .array(
            z.object({
                groupId: z.string().min(1).max(64),
                symbol: z.string().regex(/^(?:sh|sz|bj)\.\d{6}$/),
                name: z.string().max(80),
                position: z.number().int().nonnegative(),
            }),
        )
        .max(20_000),
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
