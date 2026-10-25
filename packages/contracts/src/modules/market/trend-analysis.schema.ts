import { z } from "zod";
const index=z.number().int().nonnegative().max(200_000);
const four=<T extends z.ZodType>(item:T)=>z.tuple([item,item,item,item]);
const trendState=z.enum(["空翻多","空多交替","多头","多翻空","多空交替","空头"]);
/** 可选path仅表达结构中断；referenceOnly端点只供上层引用，不参与本级绘图。 */
const PolylinePointSchema=z.object({barIndex:index,kind:z.enum(["H","L"]),ordinal:index,knownIndex:index,confirmed:z.boolean(),path:index.optional(),referenceOnly:z.boolean().optional()}).strict();
const TrendPointSchema=z.object({sourceIndex:index,knownIndex:index,confirmed:z.boolean(),path:index.optional(),referenceOnly:z.boolean().optional()}).strict();
/** 索引属于独立行情数组；[startIndex,endIndex) 为实际资格，null 表示尚未结束。 */
const QualificationIntervalSchema=z.object({path:index,startIndex:index,lowIndex:index,endIndex:index.nullable(),
    confirmationRule:z.string().min(1),endReason:z.string().min(1).nullable()}).strict();
/** 前端专用DTO，不包含行情、内部检查点、trendData或绘图对象。 */
export const TrendAnalysisSchema=z.object({
    schemaVersion:z.literal(1),symbol:z.string().min(1),timeframe:z.enum(["1d","1w","1mo","3mo","1y"]),
    dataVersion:z.string().regex(/^[a-f0-9]{64}$/),complete:z.boolean(),unavailableRules:z.array(z.string()),
    state:z.object({qualification:z.object({enabled:four(z.boolean()),active:four(z.boolean()),intervals:four(z.array(QualificationIntervalSchema).max(200_000))}).strict().optional(),version:index,currentIndex:index,dynamicDataIndex:four(index),enabled:four(z.boolean()),trendState:four(trendState.nullable()),
        data:z.object({polyline:z.array(PolylinePointSchema).max(200_000),level1:z.array(TrendPointSchema).max(200_000),level2:z.array(TrendPointSchema).max(200_000),level3:z.array(TrendPointSchema).max(200_000)}).strict(),
    }).strict(),
    timing:z.object({dataRequestMs:z.number().nonnegative(),analysisMs:z.number().nonnegative(),totalMs:z.number().nonnegative()}).strict(),
}).strict().superRefine((value,ctx)=>{
    const {state}=value;const levels=[state.data.polyline,state.data.level1,state.data.level2,state.data.level3];
    state.qualification?.intervals.forEach((intervals,level)=>{
        if(!state.qualification?.enabled[level]&&intervals.length)ctx.addIssue({code:"custom",message:"未开启买点的级别不能有资格区间"});
        if(state.qualification?.active[level]!==intervals.some(interval=>interval.endIndex===null))ctx.addIssue({code:"custom",message:"当前资格与区间状态不一致"});
        for(const interval of intervals){
            if(interval.lowIndex>interval.startIndex||interval.startIndex>=state.currentIndex
                ||interval.endIndex!==null&&(interval.endIndex<interval.startIndex||interval.endIndex>=state.currentIndex))
                ctx.addIssue({code:"custom",message:"资格区间超过已知行情或时间倒置"});
            if((interval.endIndex===null)!==(interval.endReason===null))ctx.addIssue({code:"custom",message:"资格结束日期与原因必须同时存在"});
        }
    });
    levels.forEach((points,level)=>{
        if(points.length&&(state.dynamicDataIndex[level]??0)>=points.length)ctx.addIssue({code:"custom",message:"动态偏移超出本级点数组"});
        if(!points.length&&state.dynamicDataIndex[level]!==0)ctx.addIssue({code:"custom",message:"空层级偏移必须为0"});
        if(!state.enabled[level]&&points.length)ctx.addIssue({code:"custom",message:"未启用层级不能包含点"});
        for(const point of points){
            if(point.knownIndex>=state.currentIndex)ctx.addIssue({code:"custom",message:"点超过已计算K线边界"});
            if("barIndex" in point&&point.barIndex>=state.currentIndex)ctx.addIssue({code:"custom",message:"K线索引越界"});
            if("sourceIndex" in point&&(level===0||point.sourceIndex>=(levels[level-1]?.length??0)))ctx.addIssue({code:"custom",message:"来源层索引越界"});
        }
    });
});
const BarSchema=z.object({time:z.string().regex(/^\d{4}-\d{2}-\d{2}$/),open:z.number(),high:z.number(),low:z.number(),close:z.number(),volume:z.number().nonnegative(),factor:z.number().positive(),raw_close:z.number()}).strict();
/** 与趋势dataVersion完全相同的复权行情快照；不能用普通不复权行情替代。 */
export const TrendBarsSchema=z.object({schemaVersion:z.literal(1),symbol:z.string(),timeframe:z.enum(["1d","1w","1mo","3mo","1y"]),dataVersion:z.string().regex(/^[a-f0-9]{64}$/),source:z.enum(["akshare","tdx"]),asof:z.string(),priceBasis:z.literal("causal_adjusted_equivalent"),bars:z.array(BarSchema).max(20_000)}).strict();
export type TTrendAnalysisDto=z.infer<typeof TrendAnalysisSchema>;
export type TTrendBarsDto=z.infer<typeof TrendBarsSchema>;
