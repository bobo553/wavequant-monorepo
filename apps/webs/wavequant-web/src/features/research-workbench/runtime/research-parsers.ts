import {
    ResearchConfigurationSchema,
    type TResearchConfiguration,
    TrendAnalysisSchema,
    TrendBarsSchema,
    type TTrendAnalysisDto,
    type TTrendBarsDto,
    MarketBarsPageSchema,
    type TMarketBarsPage,
    type TWatchlistDocument,
    WatchlistDocumentSchema,
} from "@repo/contracts";

type TWatchlistWindow = Window & {
    wavequantParseResearchConfiguration?: (value: unknown) => TResearchConfiguration;
    wavequantParseTrendAnalysis?: (value: unknown) => TTrendAnalysisDto;
    wavequantParseTrendBars?: (value: unknown) => TTrendBarsDto;
    wavequantParseWatchlistDocument?: (value: unknown) => TWatchlistDocument;
    wavequantParseMarketBarsPage?: (value: unknown) => TMarketBarsPage;
};

/** Schema代码在React挂载后加载，不进入挂载前的同步依赖。 */
export function installResearchParsers(): void {
    const runtimeWindow: TWatchlistWindow = window;
    runtimeWindow.wavequantParseResearchConfiguration = (value: unknown) => ResearchConfigurationSchema.parse(value);
    runtimeWindow.wavequantParseTrendAnalysis = (value: unknown) => TrendAnalysisSchema.parse(value);
    runtimeWindow.wavequantParseTrendBars = (value: unknown) => TrendBarsSchema.parse(value);
    runtimeWindow.wavequantParseWatchlistDocument = (value: unknown) => WatchlistDocumentSchema.parse(value);
    runtimeWindow.wavequantParseMarketBarsPage = (value: unknown) => MarketBarsPageSchema.parse(value);
}
