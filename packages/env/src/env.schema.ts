import { z } from "zod";

export const globalSchema = z.object({
    NODE_ENV: z.enum(["development", "production", "staging", "test"]),
});

export const databaseSchema = z.object({
    DB_HOST: z
        .string({
            error: "You forgot to set the DB_HOST variable",
        })
        .describe("Database host address"),
    DB_PORT: z.coerce
        .number({
            error: "You forgot to set the DB_PORT variable",
        })
        .describe("Database port"),
    DB_DATABASE: z
        .string({
            error: "You forgot to set the DB_DATABASE variable",
        })
        .describe("Database name"),
    DB_USERNAME: z
        .string({
            error: "You forgot to set the DB_USERNAME variable",
        })
        .describe("Database user"),
    DB_PASSWORD: z
        .string({
            error: "You forgot to set the DB_PASSWORD variable",
        })
        .describe("Database password"),
});

export const apiSchema = z.object({
    API_PORT: z.coerce
        .number({
            error: "You forgot to set the API_PORT variable",
        })
        .describe("Port for the API application"),
    ALLOWED_ORIGINS: z
        .string()
        .default("http://localhost:3000")
        .describe("Comma-separated list of allowed CORS origins"),
});

export const h5Schema = z.object({
    H5_PORT: z.coerce
        .number({
            error: "You forgot to set the H5_PORT variable",
        })
        .describe("Port for the H5 application"),
    NEXT_PUBLIC_API_URL: z
        .string({
            error: "You forgot to set the NEXT_PUBLIC_API_URL variable",
        })
        .describe("URL for the API application"),
});

export const mobileSchema = z.object({
    EXPO_PUBLIC_API_URL: z
        .string({
            error: "You forgot to set the EXPO_PUBLIC_API_URL variable",
        })
        .describe("URL for the API application"),
});

export const wavequantDataSchema = z.object({
    WAVEQUANT_REDIS_URL: z.url().refine((url) => url.startsWith("redis://") || url.startsWith("rediss://")).optional(),
    WAVEQUANT_REDIS_PREFIX: z.string().regex(/^[A-Za-z0-9:_-]{1,64}$/).default("wavequant"),
    WAVEQUANT_KLINE_MAX_ENTRIES: z.coerce.number().int().min(1).max(10000).default(2048),
    WAVEQUANT_KLINE_MAX_MIB: z.coerce.number().int().min(32).max(4096).default(512),
    WAVEQUANT_KLINE_MAX_ITEM_MIB: z.coerce.number().int().min(1).max(32).default(16),
    WAVEQUANT_KLINE_INTRADAY_SECONDS: z.coerce.number().int().min(30).max(900).default(60),
    WAVEQUANT_KLINE_REFRESH_BATCH: z.coerce.number().int().min(1).max(64).default(16),
    WAVEQUANT_AKSHARE_PYTHON: z.string().min(1).optional().describe("Absolute Python executable path in the AKShare virtual environment"),
});
