import { z } from "zod";

const nullableNumber = z.number().finite().nullable();

export const LimitUpStockSchema = z.object({
    code: z.string().regex(/^\d{6}$/),
    name: z.string().min(1),
    streak: z.number().int().min(1).max(100),
    price: nullableNumber,
    change_pct: nullableNumber,
    amount: nullableNumber,
    turnover_pct: nullableNumber,
    seal_amount: nullableNumber,
    first_seal: z.string().nullable(),
    last_seal: z.string().nullable(),
    open_count: z.number().int().nonnegative().nullable(),
    industry: z.string(),
});

export const LimitUpLadderSchema = z.object({
    date: z.iso.date(),
    source: z.literal("akshare/eastmoney"),
    fetched_at: z.iso.datetime({ offset: true }),
    status: z.enum(["ok", "empty"]),
    notice: z.string(),
    stocks: z.array(LimitUpStockSchema),
});

export type TLimitUpStock = z.infer<typeof LimitUpStockSchema>;
export type TLimitUpLadder = z.infer<typeof LimitUpLadderSchema>;
