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

export function selectedTertiaryThirds(selected, theory, bars, asof) {
    const point = selected?.raw?.point;
    const raw = selected?.raw;
    if (selected?.kind !== "trend" || raw?.trend_level !== 3 || !point || !bars?.length) return [];
    const developing = raw.scope === "display_only_developing_path";
    if (!developing && raw.scope !== "lecture_level3_not_strategy_confirmation") return [];
    const source = developing
        ? theory?.secondary_trends?.strokes?.find((stroke) => stroke.id === raw.stroke?.source_path)
        : theory?.tertiary_trends?.strokes?.find((stroke) => stroke.id === raw.stroke_id);
    const points = source?.points || [];
    const position = points.findIndex(
        (candidate) =>
            candidate.time === point.time &&
            candidate.kind === point.kind &&
            candidate.value === point.value &&
            candidate.index === point.index,
    );
    if (position < 1) return [];
    const neighbor = points.slice(0, position).findLast((candidate) => candidate.kind !== point.kind);
    if (!neighbor || ![point.value, neighbor.value].every(Number.isFinite)) return [];
    const first = neighbor;
    const last = point;
    const end = bars.findLast((bar) => bar.time <= asof)?.time;
    const start = first.time < bars[0].time ? bars[0].time : first.time;
    if (!end || last.time > end || point.available_at > asof || neighbor.available_at > asof || start >= end) return [];
    const high = point.kind === "H" ? point : neighbor;
    const low = point.kind === "L" ? point : neighbor;
    if (high.value <= low.value) return [];
    return [1, 2].map((thirds) => {
        const price = high.value - ((high.value - low.value) * thirds) / 3;
        const firstBreak = bars.find(
            (bar) =>
                bar.time > point.time &&
                bar.time <= end &&
                (point.kind === "H"
                    ? Number.isFinite(bar.low) && bar.low < price
                    : Number.isFinite(bar.high) && bar.high > price),
        );
        return { title: `Ⅲ 波段 ${thirds}/3`, price, start, end: firstBreak?.time || end, low, high };
    });
}
