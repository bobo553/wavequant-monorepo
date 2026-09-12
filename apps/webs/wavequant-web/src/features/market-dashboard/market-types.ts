export type TMarketTabId = "overview" | "sectors" | "themes" | "ladder" | "leaders" | "radar" | "multi" | "review";

export type TMarketTheme = "dark" | "light";
export type TMarketColorTheme = "market-blue" | "wavequant-teal";
export type TMarketDensity = "comfortable" | "compact";
export type TStockPool = "sealed" | "broken" | "touched" | "yesterday" | "down";
export type TSortDirection = "asc" | "desc" | "none";
export type TStockSortKey = "price" | "change" | "amount" | "turnover";
export type TWatchGroup = "core" | "risk" | "verify";

export type TMarketDialog =
    | { kind: "search" }
    | { kind: "settings" }
    | { kind: "notifications" }
    | { body: string; kind: "info"; title: string }
    | { code: string; kind: "stock" }
    | null;

export interface IMarketTab {
    count?: number;
    id: TMarketTabId;
    label: string;
}

export interface IIndexQuote {
    change: string;
    label: string;
    points: number[];
    value: string;
}

export interface IMarketMetric {
    detail: string;
    label: string;
    suffix?: string;
    tone: "amber" | "muted" | "positive" | "up";
    value: string;
}

export interface ISectorHeat {
    change: string;
    detail: string;
    name: string;
}

export interface IMarketEvent {
    category: string;
    detail: string;
    name: string;
    time: string;
}

export interface ILadderStock {
    board: string;
    change: string;
    name: string;
    streak: number;
    theme: string;
}

export interface ILadderLevel {
    label: string;
    level: number;
    stocks: ILadderStock[];
}

export interface IStockQuote {
    amount: string;
    board: string;
    change: string;
    code: string;
    firstSeal: string;
    name: string;
    openCount: number;
    price: string;
    status: string;
    streak: number;
    turnover: string;
}
