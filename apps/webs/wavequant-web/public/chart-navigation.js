const MIN_VISIBLE_BARS = 20;
const RIGHT_PADDING_BARS = 6;

export function chartNavigationState(range, barCount) {
    if (!range || !Number.isFinite(range.from) || !Number.isFinite(range.to) || barCount < 1) return null;
    const rightEdge = barCount - 1 + RIGHT_PADDING_BARS;
    const minimumSpan = Math.min(MIN_VISIBLE_BARS, rightEdge);
    const span = Math.max(1, Math.min(rightEdge, range.to - range.from));
    const maxStart = Math.max(0, rightEdge - span);
    const start = Math.max(0, Math.min(maxStart, range.from));
    return {
        from: start,
        to: start + span,
        span,
        maxStart,
        canPanLeft: start > 0.01,
        canPanRight: start < maxStart - 0.01,
        canZoomIn: span > minimumSpan + 0.01,
        canZoomOut: span < rightEdge - 0.01,
        firstIndex: Math.max(0, Math.min(barCount - 1, Math.ceil(range.from))),
        lastIndex: Math.max(0, Math.min(barCount - 1, Math.floor(range.to))),
    };
}

export function panChartRange(state, direction) {
    const step = Math.max(1, Math.round(state.span * 0.2));
    const from = Math.max(0, Math.min(state.maxStart, state.from + direction * step));
    return { from, to: from + state.span };
}

export function zoomChartRange(state, barCount, direction) {
    const rightEdge = barCount - 1 + RIGHT_PADDING_BARS;
    const minimumSpan = Math.min(MIN_VISIBLE_BARS, rightEdge);
    const span = Math.max(minimumSpan, Math.min(rightEdge, state.span * (direction === "in" ? 0.8 : 1.25)));
    const centeredStart = (state.from + state.to - span) / 2;
    const from =
        state.from < 0.01
            ? 0
            : state.to > rightEdge - 0.01
              ? rightEdge - span
              : Math.max(0, Math.min(rightEdge - span, centeredStart));
    return { from, to: from + span };
}

export function seekChartRange(state, position) {
    const from = Math.max(0, Math.min(state.maxStart, position));
    return { from, to: from + state.span };
}

export function chartNavigationKeyPosition(state, key) {
    if (key === "Home") return 0;
    if (key === "End") return state.maxStart;
    if (key === "ArrowLeft" || key === "ArrowDown") return state.from - 1;
    if (key === "ArrowRight" || key === "ArrowUp") return state.from + 1;
    if (key === "PageDown") return state.from - Math.max(1, Math.round(state.span * 0.2));
    if (key === "PageUp") return state.from + Math.max(1, Math.round(state.span * 0.2));
    return null;
}
