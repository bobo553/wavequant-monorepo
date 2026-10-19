import { z } from "zod";

export const BacktestPipelineStepSchema = z.object({
    id: z.number().int().min(1).max(24),
    title: z.string().min(1),
    status: z.enum(["pending", "running", "completed", "failed", "not_recorded"]),
    cache: z.enum(["none", "buffering", "stored", "available", "partial", "failed"]),
    rows: z.number().int().nonnegative(),
    elapsed_seconds: z.number().nonnegative().optional(),
    encoding_seconds: z.number().nonnegative().optional(),
    write_seconds: z.number().nonnegative().optional(),
    encoded_bytes: z.number().int().nonnegative().optional(),
    reused_bytes: z.number().int().nonnegative().optional(),
    error: z.string().optional(),
    cache_error: z.string().optional(),
});

export const BacktestPipelineSchema = z.object({
    schema_version: z.literal(1),
    steps: z
        .array(BacktestPipelineStepSchema)
        .length(24)
        .refine(
            (steps) => steps.every((step, index) => step.id === index + 1),
            "Calculation steps must be ordered and unique",
        ),
    note: z.string(),
});

export type TBacktestPipeline = z.infer<typeof BacktestPipelineSchema>;
