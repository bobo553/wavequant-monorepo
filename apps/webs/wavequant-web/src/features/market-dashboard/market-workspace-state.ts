import { stockQuotes } from "@/features/market-dashboard/market-data";
import type {
    TMarketColorTheme,
    TMarketDensity,
    TMarketTabId,
    TMarketTheme,
    TWatchGroup,
} from "@/features/market-dashboard/market-types";

export const marketDates = ["2026-09-03", "2026-09-04", "2026-09-07"] as const;
export const marketTimes = [
    "09:20",
    "09:25",
    "09:30",
    "10:00",
    "10:30",
    "11:30",
    "13:00",
    "13:30",
    "14:00",
    "14:30",
    "14:57",
    "15:00",
] as const;

export interface IMarketWorkspaceState {
    activeTab: TMarketTabId;
    colorTheme: TMarketColorTheme;
    date: string;
    ladderDate: string;
    density: TMarketDensity;
    favorites: string[];
    includeRisk: boolean;
    multiCodes: string[];
    note: string;
    palette: "classic" | "accessible";
    radarPaused: boolean;
    readEvents: string[];
    scope: string;
    selectedSector: string;
    theme: TMarketTheme;
    timeIndex: number;
    watchGroups: Partial<Record<string, TWatchGroup[]>>;
}

export const defaultWorkspaceState: IMarketWorkspaceState = {
    activeTab: "overview",
    colorTheme: "wavequant-teal",
    date: marketDates[2],
    ladderDate: "",
    density: "comfortable",
    favorites: ["SIM001", "SIM009"],
    includeRisk: false,
    multiCodes: stockQuotes.slice(0, 4).map((stock) => stock.code),
    note: "",
    palette: "classic",
    radarPaused: false,
    readEvents: [],
    scope: "全样本市场",
    selectedSector: "全部题材",
    theme: "dark",
    timeIndex: 9,
    watchGroups: { SIM001: ["core"], SIM009: ["verify"] },
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function restoreWorkspaceState(value: unknown): IMarketWorkspaceState {
    if (!isRecord(value) || value.version !== 2 || !isRecord(value.state)) return defaultWorkspaceState;
    const saved = value.state;
    return {
        ...defaultWorkspaceState,
        colorTheme: saved.colorTheme === "market-blue" ? "market-blue" : "wavequant-teal",
        date:
            typeof saved.date === "string" && marketDates.includes(saved.date as (typeof marketDates)[number])
                ? saved.date
                : defaultWorkspaceState.date,
        ladderDate:
            typeof saved.ladderDate === "string" && /^\d{4}-\d{2}-\d{2}$/.test(saved.ladderDate)
                ? saved.ladderDate
                : "",
        density: saved.density === "compact" ? "compact" : "comfortable",
        favorites: Array.isArray(saved.favorites)
            ? saved.favorites.filter((item): item is string => typeof item === "string")
            : defaultWorkspaceState.favorites,
        includeRisk: typeof saved.includeRisk === "boolean" ? saved.includeRisk : defaultWorkspaceState.includeRisk,
        multiCodes: Array.isArray(saved.multiCodes)
            ? saved.multiCodes.filter((item): item is string => typeof item === "string").slice(0, 9)
            : defaultWorkspaceState.multiCodes,
        note: typeof saved.note === "string" ? saved.note : "",
        palette: saved.palette === "accessible" ? "accessible" : "classic",
        scope: typeof saved.scope === "string" ? saved.scope : defaultWorkspaceState.scope,
        selectedSector:
            typeof saved.selectedSector === "string" ? saved.selectedSector : defaultWorkspaceState.selectedSector,
        theme: saved.theme === "light" ? "light" : "dark",
        timeIndex:
            typeof saved.timeIndex === "number"
                ? Math.max(0, Math.min(marketTimes.length - 1, saved.timeIndex))
                : defaultWorkspaceState.timeIndex,
        watchGroups: isRecord(saved.watchGroups)
            ? (saved.watchGroups as IMarketWorkspaceState["watchGroups"])
            : defaultWorkspaceState.watchGroups,
    };
}
