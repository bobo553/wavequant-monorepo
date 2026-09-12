"use client";

import { type JSX, type ReactNode, createContext, use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { stockQuotes } from "@/features/market-dashboard/market-data";
import type {
    TMarketDensity,
    TMarketDialog,
    TMarketTabId,
    TMarketTheme,
    TWatchGroup,
} from "@/features/market-dashboard/market-types";

const STORAGE_KEY = "wavequant.market.v2";
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

interface IWorkspaceState {
    activeTab: TMarketTabId;
    date: string;
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

interface IMarketWorkspaceContext extends IWorkspaceState {
    closeDialog: () => void;
    dialog: TMarketDialog;
    downloadCsv: (name: string, rows: ReadonlyArray<ReadonlyArray<string | number>>) => void;
    downloadJson: (name: string, payload?: unknown) => void;
    downloadMarkdown: () => void;
    isPlaying: boolean;
    locateTime: (time: string) => void;
    openInfo: (title: string, body: string) => void;
    openNotifications: () => void;
    openSearch: () => void;
    openSettings: () => void;
    openStock: (code: string) => void;
    replaceMultiStock: (index: number, code: string) => void;
    saveNote: () => void;
    setActiveTab: (tab: TMarketTabId) => void;
    setDate: (date: string) => void;
    setDensity: (density: TMarketDensity) => void;
    setIncludeRisk: (include: boolean) => void;
    setMultiCodes: (codes: string[]) => void;
    setNote: (note: string) => void;
    setPalette: (palette: "classic" | "accessible") => void;
    setRadarPaused: (paused: boolean) => void;
    setScope: (scope: string) => void;
    setSelectedSector: (sector: string) => void;
    setTheme: (theme: TMarketTheme) => void;
    setTimeIndex: (index: number) => void;
    time: string;
    toast: string | null;
    toggleEventRead: (eventId: string) => void;
    toggleFavorite: (code: string) => void;
    togglePlaying: () => void;
    toggleWatchGroup: (code: string, group: TWatchGroup) => void;
}

const defaultState: IWorkspaceState = {
    activeTab: "overview",
    date: marketDates[2],
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

const MarketWorkspaceContext = createContext<IMarketWorkspaceContext | null>(null);

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function restoreState(value: unknown): IWorkspaceState {
    if (!isRecord(value) || value.version !== 2 || !isRecord(value.state)) return defaultState;
    const saved = value.state;
    return {
        ...defaultState,
        date:
            typeof saved.date === "string" && marketDates.includes(saved.date as (typeof marketDates)[number])
                ? saved.date
                : defaultState.date,
        density: saved.density === "compact" ? "compact" : "comfortable",
        favorites: Array.isArray(saved.favorites)
            ? saved.favorites.filter((item): item is string => typeof item === "string")
            : defaultState.favorites,
        includeRisk: typeof saved.includeRisk === "boolean" ? saved.includeRisk : defaultState.includeRisk,
        multiCodes: Array.isArray(saved.multiCodes)
            ? saved.multiCodes.filter((item): item is string => typeof item === "string").slice(0, 9)
            : defaultState.multiCodes,
        note: typeof saved.note === "string" ? saved.note : "",
        palette: saved.palette === "accessible" ? "accessible" : "classic",
        scope: typeof saved.scope === "string" ? saved.scope : defaultState.scope,
        selectedSector: typeof saved.selectedSector === "string" ? saved.selectedSector : defaultState.selectedSector,
        theme: saved.theme === "light" ? "light" : "dark",
        timeIndex:
            typeof saved.timeIndex === "number"
                ? Math.max(0, Math.min(marketTimes.length - 1, saved.timeIndex))
                : defaultState.timeIndex,
        watchGroups: isRecord(saved.watchGroups)
            ? (saved.watchGroups as IWorkspaceState["watchGroups"])
            : defaultState.watchGroups,
    };
}

function triggerDownload(name: string, type: string, content: string): void {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
}

/** 保存并共享看盘日期、时点、范围、观察组和用户偏好。 */
export function MarketWorkspaceProvider({ children }: { children: ReactNode }): JSX.Element {
    const [state, setState] = useState<IWorkspaceState>(defaultState);
    const [dialog, setDialog] = useState<TMarketDialog>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [toast, setToast] = useState<string | null>(null);
    const restoredRef = useRef(false);
    const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const notify = useCallback((message: string): void => {
        setToast(message);
        if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
        toastTimerRef.current = setTimeout(() => setToast(null), 2600);
    }, []);

    useEffect(() => {
        const timer = window.setTimeout(() => {
            try {
                const raw = localStorage.getItem(STORAGE_KEY);
                if (raw) setState(restoreState(JSON.parse(raw) as unknown));
            } catch {
                notify("本地设置读取失败，已使用默认工作区");
            } finally {
                restoredRef.current = true;
            }
        }, 0);
        return () => window.clearTimeout(timer);
    }, [notify]);

    useEffect(() => {
        if (!restoredRef.current) return;
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 2, state }));
        } catch {
            queueMicrotask(() => notify("浏览器拒绝保存，本次设置仅在当前页面有效"));
        }
    }, [notify, state]);

    useEffect(() => {
        document.documentElement.classList.toggle("light", state.theme === "light");
        document.documentElement.classList.toggle("dark", state.theme === "dark");
        document.documentElement.dataset.palette = state.palette;
        document.documentElement.dataset.density = state.density;
    }, [state.density, state.palette, state.theme]);

    useEffect(() => {
        function handleShortcut(event: KeyboardEvent): void {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !event.isComposing) {
                event.preventDefault();
                setDialog({ kind: "search" });
            }
            if (event.key === "Escape") setDialog(null);
        }
        window.addEventListener("keydown", handleShortcut);
        return () => window.removeEventListener("keydown", handleShortcut);
    }, []);

    useEffect(() => {
        if (!isPlaying) return;
        const timer = window.setInterval(() => {
            setState((current) => {
                if (current.timeIndex >= marketTimes.length - 1) {
                    setIsPlaying(false);
                    return current;
                }
                return { ...current, timeIndex: current.timeIndex + 1 };
            });
        }, 900);
        return () => window.clearInterval(timer);
    }, [isPlaying]);

    useEffect(
        () => () => {
            if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
        },
        [],
    );

    const update = useCallback(<Key extends keyof IWorkspaceState>(key: Key, value: IWorkspaceState[Key]): void => {
        setState((current) => ({ ...current, [key]: value }));
    }, []);

    const downloadCsv = useCallback(
        (name: string, rows: ReadonlyArray<ReadonlyArray<string | number>>): void => {
            const safe = (value: string | number): string => {
                const raw = String(value);
                const guarded = /^[=+\-@]/.test(raw) ? `'${raw}` : raw;
                return `"${guarded.replaceAll('"', '""')}"`;
            };
            triggerDownload(
                name,
                "text/csv;charset=utf-8",
                `\uFEFF${rows.map((row) => row.map(safe).join(",")).join("\n")}`,
            );
            notify("CSV 已导出");
        },
        [notify],
    );

    const contextValue = useMemo<IMarketWorkspaceContext>(
        () => ({
            ...state,
            closeDialog: () => setDialog(null),
            dialog,
            downloadCsv,
            downloadJson: (name, payload = state) => {
                triggerDownload(name, "application/json;charset=utf-8", JSON.stringify(payload, null, 2));
                notify("工作区快照已导出");
            },
            downloadMarkdown: () => {
                const title = state.timeIndex === marketTimes.length - 1 ? "收盘复盘" : "盘中观察";
                triggerDownload(
                    `WaveQuant_${state.date}_${title}.md`,
                    "text/markdown;charset=utf-8",
                    `# ${title}\n\n- 样本日期：${state.date}\n- 观察时点：${marketTimes[state.timeIndex]}\n- 市场范围：${state.scope}\n- 题材范围：${state.selectedSector}\n\n## 人工笔记\n\n${state.note || "（未填写）"}\n`,
                );
                notify("Markdown 报告已导出");
            },
            isPlaying,
            locateTime: (time) => {
                const index = marketTimes.indexOf(time as (typeof marketTimes)[number]);
                if (index >= 0) update("timeIndex", index);
            },
            openInfo: (title, body) => setDialog({ body, kind: "info", title }),
            openNotifications: () => setDialog({ kind: "notifications" }),
            openSearch: () => setDialog({ kind: "search" }),
            openSettings: () => setDialog({ kind: "settings" }),
            openStock: (code) => setDialog({ code, kind: "stock" }),
            replaceMultiStock: (index, code) =>
                update(
                    "multiCodes",
                    state.multiCodes.map((item, itemIndex) => (itemIndex === index ? code : item)),
                ),
            saveNote: () => notify("人工笔记已保存在当前样本日"),
            setActiveTab: (tab) => {
                update("activeTab", tab);
                setIsPlaying(false);
            },
            setDate: (date) => update("date", date),
            setDensity: (density) => update("density", density),
            setIncludeRisk: (include) => update("includeRisk", include),
            setMultiCodes: (codes) => update("multiCodes", codes),
            setNote: (note) => update("note", note),
            setPalette: (palette) => update("palette", palette),
            setRadarPaused: (paused) => update("radarPaused", paused),
            setScope: (scope) => update("scope", scope),
            setSelectedSector: (sector) => update("selectedSector", sector),
            setTheme: (theme) => update("theme", theme),
            setTimeIndex: (index) => update("timeIndex", Math.max(0, Math.min(marketTimes.length - 1, index))),
            time: marketTimes[state.timeIndex] ?? marketTimes[0],
            toast,
            toggleEventRead: (eventId) =>
                update(
                    "readEvents",
                    state.readEvents.includes(eventId)
                        ? state.readEvents.filter((id) => id !== eventId)
                        : [...state.readEvents, eventId],
                ),
            toggleFavorite: (code) => {
                update(
                    "favorites",
                    state.favorites.includes(code)
                        ? state.favorites.filter((item) => item !== code)
                        : [...state.favorites, code],
                );
                notify(state.favorites.includes(code) ? "已移出观察" : "已加入观察");
            },
            togglePlaying: () => {
                if (state.timeIndex >= marketTimes.length - 1) update("timeIndex", 0);
                setIsPlaying((playing) => !playing);
            },
            toggleWatchGroup: (code, group) => {
                const current = state.watchGroups[code] ?? [];
                const next = current.includes(group) ? current.filter((item) => item !== group) : [...current, group];
                update("watchGroups", { ...state.watchGroups, [code]: next });
                notify(current.includes(group) ? "已从观察组移除，可再次点击恢复" : "已加入观察组");
            },
        }),
        [dialog, downloadCsv, isPlaying, notify, state, toast, update],
    );

    return <MarketWorkspaceContext value={contextValue}>{children}</MarketWorkspaceContext>;
}

/** 读取当前看盘工作区上下文。 */
export function useMarketWorkspace(): IMarketWorkspaceContext {
    const context = use(MarketWorkspaceContext);
    if (!context) throw new Error("useMarketWorkspace must be used inside MarketWorkspaceProvider");
    return context;
}
