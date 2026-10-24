import { z } from "zod";

/** 研究初始化元数据；不包含行情、股票目录或交易日列表。 */
export const ResearchConfigurationSchema = z.object({
    runs: z.array(z.object({
        id: z.string().min(1),
        start: z.iso.date(),
        end: z.iso.date(),
    })).min(1),
    variants: z.record(z.string(), z.string()),
});
export type TResearchConfiguration = z.infer<typeof ResearchConfigurationSchema>;
