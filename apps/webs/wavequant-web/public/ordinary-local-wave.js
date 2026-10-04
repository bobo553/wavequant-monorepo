/**
 * 正 N 起点内的局部观察复用 secondary_trend._structural_reversals 的严格关键位突破主干。
 * 只消费已确认一级点；不执行全局二级高点升级，也不写回正式趋势或交易证据。
 */
export function ordinaryLocalWavePoints(points) {
    const highs = [],
        lows = [],
        selected = [];
    let direction = null,
        anchor = null,
        key = null;
    for (const [index, point] of points.entries()) {
        (point.kind === "H" ? highs : lows).push(index);
        if (!direction) {
            if (highs.length < 2 || lows.length < 2) continue;
            const highChange = points[highs.at(-1)].value - points[highs.at(-2)].value;
            const lowChange = points[lows.at(-1)].value - points[lows.at(-2)].value;
            direction = highChange > 0 && lowChange > 0 ? "up" : highChange < 0 && lowChange < 0 ? "down" : null;
            if (!direction) continue;
            const pool = direction === "up" ? highs : lows;
            anchor = pool.reduce((best, candidate) =>
                (
                    direction === "up"
                        ? points[candidate].value > points[best].value
                        : points[candidate].value < points[best].value
                )
                    ? candidate
                    : best,
            );
            key = anchor > 0 ? anchor - 1 : null;
            if (key === null) {
                anchor = pool.at(-1);
                key = anchor > 0 ? anchor - 1 : null;
            }
            continue;
        }
        const up = direction === "up",
            target = up ? "H" : "L",
            sign = up ? 1 : -1;
        if (point.kind === target && sign * (point.value - points[anchor].value) > 0) {
            anchor = index;
            key = index - 1;
        }
        if (key === null || point.kind === target || sign * (point.value - points[key].value) >= 0) continue;
        const extreme = points[anchor];
        selected.push({
            ...extreme,
            available_at: point.available_at,
            source_available_at: extreme.available_at,
            local_wave_turn: up ? "up_to_down" : "down_to_up",
            confirmation_rule: "n_origin_local_structural_key_break",
            confirmed_by: { time: point.time, value: point.value, available_at: point.available_at },
        });
        direction = up ? "down" : "up";
        const pool = points
            .slice(anchor + 1, index + 1)
            .map((candidate, offset) => ({
                candidate,
                index: anchor + 1 + offset,
            }))
            .filter(({ candidate }) => candidate.kind === (up ? "L" : "H"));
        anchor = pool.reduce((best, candidate) =>
            (up ? candidate.candidate.value < best.candidate.value : candidate.candidate.value > best.candidate.value)
                ? candidate
                : best,
        ).index;
        key = anchor - 1;
    }
    return selected;
}
