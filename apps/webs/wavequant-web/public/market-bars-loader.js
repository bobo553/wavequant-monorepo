const sources = new Set(["tdx", "akshare"]);
const timeframes = new Set(["1d", "1w", "1mo", "3mo", "1y"]);
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
/** 浏览器响应只通过共享契约解析；单元测试显式注入同一解析器。 */
export function validateMarketBarsPage(value, parser) {
    const shared = parser ?? globalThis.window?.wavequantParseMarketBarsPage;
    if (!shared) throw new Error("年度行情契约解析器尚未就绪");
    return shared(value);
}
/** 预览属于固定行情请求；同股同日的回测账户不能成为行情预览。 */
export function marketPreviewMatches(view, request, selection) {
    return (
        selection?.kind === "market" &&
        selection.source === request.source &&
        selection.symbol === request.symbol &&
        selection.requestedAsOf === request.asof &&
        selection.timeframe === request.timeframe &&
        view?.symbol === request.symbol &&
        view.asof === selection.resolvedAsOf &&
        (view.timeframe || "1d") === request.timeframe &&
        view.data_version === selection.dataVersion &&
        view.price_basis === selection.priceBasis &&
        view.backtest == null &&
        view.bars?.length > 0
    );
}
function matches(bundle, request, page) {
    return (
        bundle?.schema_version === 1 &&
        bundle.source === request.source &&
        bundle.symbol === request.symbol &&
        bundle.requested_asof === request.asof &&
        bundle.timeframe === request.timeframe &&
        bundle.resolved_asof === page.resolved_asof &&
        bundle.data_version === page.data_version &&
        bundle.view?.data_version === page.data_version &&
        bundle.view.backtest == null &&
        bundle.view.price_basis === page.view.price_basis &&
        bundle.view.symbol === page.symbol &&
        bundle.view.asof === page.resolved_asof &&
        (bundle.view.timeframe || "1d") === page.timeframe &&
        Array.isArray(bundle.view.bars) &&
        bundle.view.bars.length === page.total_bars &&
        bundle.view.bars[0]?.time === page.history_start &&
        bundle.view.bars.at(-1)?.time === page.history_end &&
        bundle.view.bars.every((bar, index, all) => index === 0 || bar.time > all[index - 1].time)
    );
}

/** 按服务器提供的年份倒序加载；预览只合并行情，不计算或缓存局部趋势。 */
export async function loadMarketBarsHistory(
    request,
    {
        signal,
        onPage = () => {},
        fetcher = fetch,
        parsePage,
        timeoutMs = 45_000,
        getCachedBundle = () => null,
        initialPreview = null,
    } = {},
) {
    if (
        !sources.has(request.source) ||
        !timeframes.has(request.timeframe) ||
        typeof request.asof !== "string" ||
        !datePattern.test(request.asof)
    )
        throw new Error("年度行情请求无效");
    let first = null;
    let year;
    let view = null;
    let loadedYears = 0;
    const bars = new Map();
    const sessions = new Set();
    try {
        do {
            signal?.throwIfAborted();
            const query = new URLSearchParams({
                source: request.source,
                symbol: request.symbol,
                asof: request.asof,
                timeframe: request.timeframe,
            });
            if (year !== undefined) query.set("year", String(year));
            if (first) query.set("data_version", first.data_version);
            const deadline = AbortSignal.timeout(timeoutMs);
            const response = await fetcher(`/api/market-bars?${query}`, {
                cache: "no-store",
                signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
            });
            const body = await response.json();
            signal?.throwIfAborted();
            if (!response.ok) {
                const error = new Error(body.error || `HTTP ${response.status}`);
                error.code = body.code;
                throw error;
            }
            const page = validateMarketBarsPage(body, parsePage);
            if (
                page.source !== request.source ||
                page.symbol !== request.symbol ||
                page.requested_asof !== request.asof ||
                page.timeframe !== request.timeframe ||
                (year !== undefined && page.year !== year)
            )
                throw new Error("年度行情与当前股票请求不一致");
            if (
                first &&
                ["data_version", "resolved_asof", "history_start", "history_end", "total_bars"].some(
                    (field) => page[field] !== first[field],
                )
            )
                throw new Error("行情版本已变化，请重试以保持年度数据一致");
            if (first && JSON.stringify(page.available_years) !== JSON.stringify(first.available_years))
                throw new Error("行情年份已变化，请重试");
            if (first && page.view.price_basis !== first.view.price_basis)
                throw new Error("行情价格口径已变化，请重试");
            if (!first) {
                first = page;
                if (page.year !== page.available_years[0]) throw new Error("首批行情不是最新可用年份");
                if (
                    marketPreviewMatches(initialPreview?.view, request, initialPreview?.selection) &&
                    initialPreview.selection.dataVersion === page.data_version &&
                    initialPreview.view.price_basis === page.view.price_basis &&
                    initialPreview.view.symbol === page.symbol &&
                    initialPreview.view.asof === page.resolved_asof &&
                    (initialPreview.view.timeframe || "1d") === page.timeframe
                ) {
                    initialPreview.view.bars.forEach((bar) => bars.set(bar.time, bar));
                    initialPreview.view.sessions.forEach((session) => sessions.add(session));
                }
            }
            page.view.bars.forEach((bar) => bars.set(bar.time, bar));
            page.view.sessions.forEach((session) => sessions.add(session));
            const merged = [...bars.values()].sort((left, right) => left.time.localeCompare(right.time));
            view = { ...page.view, bars: merged, sessions: [...sessions].sort() };
            loadedYears++;
            const cachedBundle = getCachedBundle();
            const cached = matches(cachedBundle, request, page) ? cachedBundle : null;
            if (cached) view = cached.view;
            await onPage({ page, view, loadedYears, totalYears: page.available_years.length, cachedBundle: cached });
            signal?.throwIfAborted();
            if (cached) return { view, dataVersion: page.data_version, cachedBundle: cached };
            year = page.next_year;
        } while (year !== null);
        if (view.bars.length !== first.total_bars) throw new Error("年度行情未覆盖完整历史，请重试");
        return { view, dataVersion: first.data_version, cachedBundle: null };
    } catch (error) {
        if (signal?.aborted) throw signal.reason;
        const failure = error.name === "TimeoutError" ? new Error("年度行情请求超时，请重试", { cause: error }) : error;
        failure.partialView = view;
        failure.year = year ?? first?.available_years[0] ?? Number(request.asof.slice(0, 4));
        throw failure;
    }
}
