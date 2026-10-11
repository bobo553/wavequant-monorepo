import { MarketBarsPageSchema, type TMarketBarsPage } from "@repo/contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResearchWorkbench } from "../src/features/research-workbench/research-workbench";

const chartState = vi.hoisted(() => ({
    installs: [] as { data: TMarketBarsPage["view"]; preserve: boolean }[],
    theory: vi.fn(),
    clipboard: vi.fn(),
    chart: null as { data: TMarketBarsPage["view"] | null; container: HTMLElement } | null,
}));

vi.mock("../src/features/research-workbench/runtime/research-runtime", () => ({ ResearchRuntime: () => null }));
vi.mock("../src/features/research-workbench/topology/strategy-topology-page", () => ({
    StrategyTopologyPage: () => null,
}));
vi.mock("../public/charts.js", () => ({
    PriceChart: class {
        data: TMarketBarsPage["view"] | null = null;
        autoWaveProjections = [];
        constructor(public container: HTMLElement) {
            chartState.chart = this;
        }
        setData(data: TMarketBarsPage["view"], options: { preserveViewport?: boolean }) {
            this.data = data;
            chartState.installs.push({ data, preserve: Boolean(options.preserveViewport) });
        }
        setTheory = chartState.theory;
        clearTheory() {}
        setTrendPriceLabelsVisible() {}
        refreshMarkers() {}
        drawLevels() {}
        navigationState() {
            return null;
        }
        reset() {}
    },
    PerformanceCharts: class {
        update() {}
    },
}));

const version = "a".repeat(64);
const symbol = "sz.000678";
const otherSymbol = "sh.600519";
const requestedAsOf = "2026-10-11";
const resolvedAsOf = "2026-10-09";
function annualPage(year: number, selected = symbol, timeframe: "1d" | "1w" = "1d"): TMarketBarsPage {
    return {
        schema_version: 1,
        source: "tdx",
        symbol: selected,
        requested_asof: requestedAsOf,
        resolved_asof: resolvedAsOf,
        timeframe,
        data_version: version,
        year,
        next_year: year === 2026 ? 2025 : null,
        has_more: year === 2026,
        available_years: [2026, 2025],
        history_start: "2025-10-09",
        history_end: resolvedAsOf,
        total_bars: 2,
        view: {
            symbol: selected,
            asof: resolvedAsOf,
            timeframe,
            price_basis: "raw_unadjusted",
            data_version: version,
            data_source: "通达信本地行情",
            result_scope: "tdx",
            bars: [
                {
                    time: `${year}-10-09`,
                    open: 10,
                    high: 11,
                    low: 9,
                    close: 10.5,
                    raw_close: 10.5,
                    volume: 1000,
                    factor: 1,
                },
            ],
            sessions: [`${year}-10-09`, `${year}-10-12`],
            markers: [],
            orders: [],
            signals: [],
            trades: [],
            curve: [],
            metrics: null,
            backtest: null,
            evidence: "年度行情预览",
        },
    };
}
function completeBundle(selected = symbol, timeframe: "1d" | "1w" = "1d") {
    const first = annualPage(2026, selected, timeframe);
    return {
        schema_version: 1,
        source: first.source,
        symbol: selected,
        requested_asof: requestedAsOf,
        resolved_asof: resolvedAsOf,
        timeframe,
        data_version: version,
        snapshot_id: "complete",
        algorithm_version: "test",
        view: {
            ...first.view,
            bars: [...annualPage(2025, selected, timeframe).view.bars, ...first.view.bars],
            sessions: ["2025-10-09", "2025-10-12", "2026-10-09", "2026-10-12"],
        },
        theory: { symbol: selected, asof: resolvedAsOf, states: [], diagnostics: [], strokes: [] },
    };
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => {
        resolve = done;
    });
    return { resolve, promise };
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const element = (id: string) => document.getElementById(id)!;
const pendingSignals: AbortSignal[] = [];
function setupRequests(
    market: (url: URL, signal: AbortSignal) => Promise<Response>,
    catalog?: (url: URL) => Promise<Response> | null,
) {
    vi.stubGlobal(
        "fetch",
        vi.fn(async (input: string, options: { signal: AbortSignal }) => {
            const url = new URL(input, "http://localhost");
            if (url.pathname === "/api/catalog")
                return json({
                    runs: [{ id: "run", start: "2025-01-01", end: resolvedAsOf, symbols: [] }],
                    variants: { lecture_v3: "V3" },
                    scenarios: { base: "base" },
                });
            if (url.pathname.endsWith("-catalog"))
                return (
                    catalog?.(url) ??
                    json({
                        available: true,
                        with_daily: 2,
                        latest: requestedAsOf,
                        stocks: [symbol, otherSymbol].map((value) => ({
                            symbol: value,
                            name: value === symbol ? "襄阳轴承" : "贵州茅台",
                            has_data: true,
                            last: requestedAsOf,
                        })),
                    })
                );
            if (url.pathname === "/api/watchlists") return json({ error: "测试不启用账户同步" }, 503);
            if (url.pathname.startsWith("/api/market-")) {
                pendingSignals.push(options.signal);
                return market(url, options.signal);
            }
            return json({ active: 0, max_active: 4, jobs: [], strategy_version: "test" });
        }),
    );
}
async function startApp() {
    await import("../public/app.js");
}

beforeEach(() => {
    vi.resetModules();
    chartState.installs.length = 0;
    chartState.theory.mockClear();
    chartState.clipboard.mockReset().mockResolvedValue(undefined);
    chartState.chart = null;
    pendingSignals.length = 0;
    localStorage.clear();
    window.history.replaceState({}, "", `/?source=tdx&symbol=${symbol}&asof=${requestedAsOf}`);
    document.body.innerHTML = `<h1 id="page-title"></h1><button id="reload">刷新</button>${renderToStaticMarkup(<ResearchWorkbench />)}`;
    Object.assign(window, { wavequantParseMarketBarsPage: (value: unknown) => MarketBarsPageSchema.parse(value) });
    vi.stubGlobal(
        "navigator",
        Object.assign(Object.create(navigator), { clipboard: { writeText: chartState.clipboard } }),
    );
    vi.spyOn(globalThis, "setInterval").mockImplementation(() => 0 as unknown as ReturnType<typeof setInterval>);
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

describe("real research app annual loading events", () => {
    it("publishes the first year interactively, prepends history and installs complete geometry once", async () => {
        const older = deferred<Response>();
        const complete = deferred<Response>();
        const requests: URL[] = [];
        setupRequests(async (url) => {
            requests.push(url);
            if (url.pathname === "/api/market-timeframe") return complete.promise;
            return url.searchParams.has("year") ? older.promise : json(annualPage(2026));
        });
        await startApp();
        await vi.waitFor(() => expect(chartState.installs).toHaveLength(1));
        expect(element("chart-loading-overlay").hidden).toBe(true);
        expect(element("loading").hidden).toBe(true);
        expect(element("price-chart")).toHaveAttribute("aria-busy", "false");
        expect(element("stock-data-load-message")).toHaveTextContent("正在加载 2025 年");
        expect(element("run-stock-backtest")).not.toBeDisabled();
        (element("copy-candle") as HTMLButtonElement).click();
        await vi.waitFor(() => expect(chartState.clipboard).toHaveBeenCalledTimes(1));
        expect(chartState.clipboard.mock.calls[0]![0]).toContain("2026-10-09");
        expect(chartState.theory).not.toHaveBeenCalled();
        (element("show-theory") as HTMLInputElement).dispatchEvent(new Event("change", { bubbles: true }));
        expect(requests.every((url) => url.pathname === "/api/market-bars")).toBe(true);
        older.resolve(json(annualPage(2025)));
        await vi.waitFor(() => expect(chartState.installs).toHaveLength(2));
        expect(chartState.installs[1]!.data.bars.map((bar) => bar.time)).toEqual(["2025-10-09", "2026-10-09"]);
        expect(chartState.installs[1]!.preserve).toBe(true);
        expect(chartState.installs[1]!.data.sessions).toContain("2026-10-12");
        expect(chartState.theory).not.toHaveBeenCalled();
        complete.resolve(json(completeBundle()));
        await vi.waitFor(() =>
            expect(element("stock-data-load-message").textContent).not.toContain("正在加载完整趋势"),
        );
        expect(element("stock-data-load-message").textContent).not.toContain("加载失败");
        expect(element("error").textContent).not.toContain("加载失败");
        await vi.waitFor(() => expect(chartState.theory).toHaveBeenCalledTimes(1));
        expect(chartState.installs[2]!.preserve).toBe(true);
        expect(element("stock-data-load-status").hidden).toBe(true);
        expect(requests.map((url) => url.searchParams.get("year"))).toEqual([null, "2025", null]);
    });

    it.each(["timeframe", "stock", "source"])(
        "real %s switching aborts the old annual chain and ignores its late page",
        async (target) => {
            const older = deferred<Response>();
            let oldSignal: AbortSignal | null = null;
            setupRequests(async (url, signal) => {
                if (
                    url.searchParams.get("timeframe") === "1w" ||
                    url.searchParams.get("symbol") === otherSymbol ||
                    url.searchParams.get("source") === "akshare"
                )
                    return new Promise(() => {});
                if (url.searchParams.has("year")) {
                    oldSignal = signal;
                    return older.promise;
                }
                return json(annualPage(2026));
            });
            await startApp();
            await vi.waitFor(() => expect(oldSignal).not.toBeNull());
            if (target === "timeframe") document.querySelector<HTMLButtonElement>('[data-timeframe="1w"]')!.click();
            else {
                const select = element(target === "stock" ? "symbol-select" : "result-scope") as HTMLSelectElement;
                select.value = target === "stock" ? otherSymbol : "akshare";
                select.dispatchEvent(new Event("change", { bubbles: true }));
            }
            await vi.waitFor(() => expect(oldSignal!.aborted).toBe(true));
            older.resolve(json(annualPage(2025)));
            await Promise.resolve();
            expect(chartState.installs).toHaveLength(1);
            expect(chartState.theory).not.toHaveBeenCalled();
        },
    );

    it("retains a weekend-cutoff preview on older-year failure and shows an actionable retry", async () => {
        const requests: URL[] = [];
        setupRequests(async (url) => {
            requests.push(url);
            return url.searchParams.has("year") ? json({ error: "旧年暂不可用" }, 503) : json(annualPage(2026));
        });
        await startApp();
        await vi.waitFor(() => expect(element("stock-data-load-retry").hidden).toBe(false));
        expect(chartState.installs).toHaveLength(1);
        expect(element("error").hidden).toBe(true);
        expect(element("chart-loading-overlay").hidden).toBe(true);
        expect(element("stock-data-load-message")).toHaveTextContent("2025 年行情加载失败");
        expect(element("stock-data-load-message").textContent).not.toContain("正在加载");
        (element("stock-data-load-retry") as HTMLButtonElement).click();
        await vi.waitFor(() => expect(requests).toHaveLength(4));
        expect(requests[2]!.searchParams.get("asof")).toBe(requestedAsOf);
        expect(chartState.installs[1]!.preserve).toBe(true);
    });

    it("source switching cancels annual history before waiting for its new catalog", async () => {
        const newCatalog = deferred<Response>();
        const older = deferred<Response>();
        let oldSignal: AbortSignal | null = null;
        setupRequests(
            async (url, signal) => {
                if (url.searchParams.has("year")) {
                    oldSignal = signal;
                    return older.promise;
                }
                return json(annualPage(2026));
            },
            (url) => (url.pathname === "/api/akshare-catalog" ? newCatalog.promise : null),
        );
        await startApp();
        await vi.waitFor(() => expect(oldSignal).not.toBeNull());
        const scope = element("result-scope") as HTMLSelectElement;
        scope.value = "akshare";
        scope.dispatchEvent(new Event("change", { bubbles: true }));
        expect(oldSignal!.aborted).toBe(true);
        expect(scope.disabled).toBe(true);
        older.resolve(json(annualPage(2025)));
        newCatalog.resolve(json({ available: false, stocks: [], with_daily: 0 }));
        await vi.waitFor(() => expect(scope.disabled).toBe(false));
        expect(chartState.installs).toHaveLength(1);
        expect(element("chart-loading-overlay").hidden).toBe(true);
    });

    it("a changed full geometry version keeps completed annual candles interactive", async () => {
        setupRequests(async (url) =>
            url.pathname === "/api/market-timeframe"
                ? json({ ...completeBundle(), data_version: "b".repeat(64) })
                : json(annualPage(Number(url.searchParams.get("year") || 2026))),
        );
        await startApp();
        await vi.waitFor(() => expect(element("stock-data-load-retry").hidden).toBe(false));
        expect(chartState.installs).toHaveLength(2);
        expect(chartState.theory).not.toHaveBeenCalled();
        expect(element("error").hidden).toBe(true);
        expect(element("stock-data-load-message")).toHaveTextContent("完整趋势加载失败");
        expect(element("price-chart")).toHaveAttribute("aria-busy", "false");
    });

    it("a first-page timeout clears the in-progress status and exposes retry", async () => {
        const timeout = AbortSignal.timeout.bind(AbortSignal);
        vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) =>
            timeout(milliseconds === 45_000 ? 10 : milliseconds),
        );
        setupRequests(
            async (_url, signal) =>
                new Promise((_, reject) => {
                    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
                }),
        );
        await startApp();
        await vi.waitFor(() => expect(element("error").hidden).toBe(false));
        expect(element("stock-data-load-message")).toHaveTextContent("年度行情请求超时");
        expect(element("stock-data-load-message").textContent).not.toContain("正在加载");
        expect(element("stock-data-load-retry").hidden).toBe(false);
        expect(chartState.installs).toHaveLength(0);
    });

    it("first-page failure replaces the loading status and the error retry starts a new annual request", async () => {
        let calls = 0;
        setupRequests(async () => {
            calls++;
            return json({ error: "首年暂不可用" }, 503);
        });
        await startApp();
        await vi.waitFor(() => expect(element("error").hidden).toBe(false));
        expect(element("stock-data-load-message")).toHaveTextContent("行情加载失败");
        expect(element("stock-data-load-message").textContent).not.toContain("正在加载");
        expect(element("loading").hidden).toBe(true);
        const retry = [...element("error").querySelectorAll("button")].find(
            (button) => button.textContent === "重试加载行情",
        )!;
        retry.click();
        await vi.waitFor(() => expect(calls).toBe(2));
        expect(chartState.installs).toHaveLength(0);
    });
});
