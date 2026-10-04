const WAVE_ENTRY_PATHS = new Set([
    "two_t_held_defense_volume_gap",
    "two_t_held_defense_gap_attack",
    "two_t_strong_a_resistance_rebreak",
    "one_p_held_defense_rebound",
]);

function validWaveEndpoints(points, bars) {
    const marketDates = new Set(bars.map((bar) => bar.time));
    if (
        points.some((point) => !marketDates.has(point.time) || !Number.isFinite(point.price)) ||
        points.some(
            (point, index) =>
                index > 0 &&
                !(
                    points[index - 1].time < point.time ||
                    (point.label === "C 确认" && points[index - 1].time === point.time)
                ),
        )
    )
        return [];
    return points;
}

// C is the known confirmation point at entry, not a future completed wave high.
export function selectedWaveEndpoints(marker, bars) {
    if (marker?.kind !== "fill" || marker.side !== "BUY") return [];
    const proof = marker.decision_evidence?.find((evidence) => WAVE_ENTRY_PATHS.has(evidence.wave_entry_path));
    if (!proof) return [];
    const cTime = marker.signal_time || marker.decision_timestamp?.slice(0, 10);
    const origin = { label: "A 起点", time: proof.wave_a_origin_date, price: proof.wave_a_origin, position: "below" };
    const points = [
        { label: "A 高", time: proof.wave_a_high_date, price: proof.wave_a_high, position: "above" },
        { label: "B", time: proof.wave_b_low_date, price: proof.wave_b_low, position: "below" },
        { label: "C 确认", time: cTime, price: proof.wave_breakout_close, position: "above" },
    ];
    if (origin.time && Number.isFinite(origin.price)) points.unshift(origin);
    return validWaveEndpoints(points, bars);
}

// Structure observations reuse endpoint labels without inventing an executed entry or a future C confirmation.
export function projectionWaveEndpoints(projection, bars, asof = bars.at(-1)?.time) {
    const knownAt = [projection?.aKnownAt, projection?.bKnownAt, projection?.confirmedAt, projection?.bTime]
        .filter((time) => typeof time === "string")
        .sort()
        .at(-1);
    if (!projection || !asof || !knownAt || knownAt > asof) return [];
    const points = [
        { label: "A 起点", time: projection.originTime, price: projection.origin, position: "below" },
        { label: "A 终点", time: projection.aTime, price: projection.aHigh, position: "above" },
        { label: "B", time: projection.bTime, price: projection.bLow, position: "below" },
    ];
    if (
        projection.cTime &&
        projection.cKnownAt &&
        projection.aIsValid !== false &&
        projection.cTime <= projection.cKnownAt &&
        projection.cKnownAt <= asof &&
        Number.isFinite(projection.cHigh)
    )
        points.push({ label: "C 终点", time: projection.cTime, price: projection.cHigh, position: "above" });
    return validWaveEndpoints(points, bars);
}

export class WaveEndpointOverlay {
    constructor(container) {
        this.container = container;
        this.points = [];
        this.projected = [];
        this.views = [this];
    }

    attached({ chart, series, requestUpdate }) {
        this.chart = chart;
        this.series = series;
        this.requestUpdate = requestUpdate;
    }

    paneViews() {
        return this.views;
    }

    zOrder() {
        return "top";
    }

    renderer() {
        return this;
    }

    setPoints(points) {
        this.points = points;
        this.updateAllViews();
        this.requestUpdate?.();
    }

    updateAllViews() {
        if (!this.chart || !this.series) return;
        const scale = this.chart.timeScale();
        this.projected = this.points
            .map((point) => ({
                ...point,
                x: scale.timeToCoordinate(point.time),
                y: this.series.priceToCoordinate(point.price),
            }))
            .filter((point) => point.x !== null && point.y !== null);
        this.container.dataset.waveEndpointCount = String(this.projected.length);
    }

    draw(target) {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            context.save();
            context.textAlign = "center";
            context.textBaseline = "middle";
            context.lineJoin = "round";
            context.font = "700 11px ui-sans-serif, system-ui, sans-serif";
            const colors = {
                "A 起点": "#ebbc70",
                "A 高": "#ebbc70",
                "A 终点": "#ebbc70",
                B: "#5ebeb0",
                "C 确认": "#a29ce0",
                "C 终点": "#a29ce0",
            };
            for (const point of this.projected) {
                if (point.x < 0 || point.x > mediaSize.width || point.y < 8 || point.y > mediaSize.height - 8) continue;
                const text = `${point.label} ${point.price.toFixed(4)}`;
                const textInset = Math.min(mediaSize.width / 2, context.measureText(text).width / 2 + 4);
                const labelX = Math.max(textInset, Math.min(mediaSize.width - textInset, point.x));
                const labelY = Math.max(
                    12,
                    Math.min(mediaSize.height - 12, point.y + (point.position === "below" ? 17 : -17)),
                );
                context.beginPath();
                context.arc(point.x, point.y, 3, 0, Math.PI * 2);
                context.fillStyle = colors[point.label];
                context.fill();
                context.beginPath();
                context.moveTo(point.x, point.y + (point.position === "below" ? 4 : -4));
                context.lineTo(labelX, labelY + (point.position === "below" ? -7 : 7));
                context.strokeStyle = colors[point.label];
                context.lineWidth = 1.5;
                context.stroke();
                context.lineWidth = 3;
                context.strokeStyle = "#102032";
                context.strokeText(text, labelX, labelY);
                context.fillText(text, labelX, labelY);
            }
            context.restore();
        });
    }
}
