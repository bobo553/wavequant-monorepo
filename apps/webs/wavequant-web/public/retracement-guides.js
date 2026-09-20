// Only server-confirmed anchors define the wave; viewport candle extrema must not reprice it.
export function tertiaryRetracementGuides(theory, bars, windowEnd) {
    if (!bars?.length) return [];
    const asof = theory?.asof || bars.at(-1).time;
    const end = bars.findLast((bar) => bar.time <= asof)?.time;
    if (!end) return [];
    const cutoff = windowEnd && windowEnd < end ? windowEnd : end;
    const highs = (theory?.tertiary_trends?.bear_to_bull_highs || []).filter((high) => {
        const low = high.confirmed_low;
        return (
            low &&
            Number.isFinite(low.value) &&
            Number.isFinite(high.value) &&
            high.value > low.value &&
            low.time < high.time &&
            high.time <= cutoff &&
            high.available_at <= asof &&
            (!low.available_at || low.available_at <= asof)
        );
    });
    const high = highs
        .sort((a, b) => a.time.localeCompare(b.time) || a.available_at.localeCompare(b.available_at))
        .at(-1);
    if (!high) return [];
    const low = high.confirmed_low;
    const start = low.time < bars[0].time ? bars[0].time : low.time;
    if (start >= end) return [];
    return [1, 2].flatMap((thirds) => {
        const price = high.value - ((high.value - low.value) * thirds) / 3;
        const firstBreak = bars.find(
            (bar) => bar.time > high.time && bar.time <= end && Number.isFinite(bar.low) && bar.low < price,
        );
        const guideEnd = firstBreak?.time || end;
        // A visible window beginning on the break has no horizontal segment to draw.
        if (start >= guideEnd) return [];
        return [{ title: `Ⅲ 回撤 ${thirds}/3`, price, start, end: guideEnd, low, high }];
    });
}
