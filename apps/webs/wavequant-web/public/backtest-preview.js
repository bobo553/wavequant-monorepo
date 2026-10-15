function invalidBar(bar, index, bars) {
    return !bar || typeof bar.time !== "string"
        || !/^\d{4}-\d{2}-\d{2}$/.test(bar.time) || (index > 0 && bar.time <= bars[index - 1].time)
        || ![bar.open, bar.high, bar.low, bar.close, bar.volume, bar.factor, bar.raw_close].every(Number.isFinite);
}

// 同一任务、同一天的完整增量快照，不能用空成交列表替换真实B/S。
export function backtestPreviewView(payload, { job, symbol, source }) {
    const preview = payload?.preview;
    if (payload?.job !== job || !preview || preview.symbol !== symbol || preview.timeframe !== "1d") return null;
    if (!Number.isSafeInteger(payload.revision) || payload.revision !== preview.processed
        || !Number.isSafeInteger(preview.total) || preview.processed <= 0 || preview.processed > preview.total
        || !Array.isArray(preview.bars) || preview.bars.length !== preview.processed
        || preview.bars.at(-1)?.time !== preview.asof || preview.theory?.asof !== preview.asof) return null;
    if (preview.bars.some(invalidBar)) return null;
    if (preview.market_bars !== undefined || preview.market_asof !== undefined) {
        if (!Array.isArray(preview.market_bars) || preview.market_bars.length !== preview.total
            || preview.market_bars.some(invalidBar) || preview.market_bars.at(-1)?.time !== preview.market_asof
            || preview.bars.some((bar, index) =>
                ["time", "open", "high", "low", "close", "volume", "factor", "raw_close"].some(
                    (key) => bar[key] !== preview.market_bars[index][key]))) return null;
    }
    if (preview.phase !== "daily" || !preview.backtest
        || ["markers", "signals", "orders", "trades", "audit", "curve"].some((key) => !Array.isArray(preview[key]))) return null;
    if (preview.curve.length !== preview.processed || preview.curve.at(-1)?.timestamp?.slice(0, 10) !== preview.asof)
        return null;
    if ([...preview.markers, ...preview.signals, ...preview.orders, ...preview.audit, ...preview.curve].some((row) => {
        const date = row?.time || row?.timestamp?.slice(0, 10);
        return typeof date !== "string" || date > preview.asof;
    })) return null;
    if (preview.trades.some((row) => typeof row?.exit_time !== "string" || row.exit_time.slice(0, 10) > preview.asof)) return null;
    return {
        ...preview,
        data_source: source,
        result_scope: "stock-preview",
        timeframe_label: "日线",
        sessions: preview.bars.map((bar) => bar.time),
        backtest: { ...preview.backtest, status: "running" },
        preview_job: job,
    };
}
