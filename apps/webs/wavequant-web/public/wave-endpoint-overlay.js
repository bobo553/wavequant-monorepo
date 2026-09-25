const WAVE_ENTRY_PATHS = new Set([
    "two_t_held_defense_volume_gap",
    "two_t_held_defense_gap_attack",
    "one_p_held_defense_rebound",
]);

// C is the known confirmation point at entry, not a future completed wave high.
export function selectedWaveEndpoints(marker, bars) {
    if (marker?.kind !== "fill" || marker.side !== "BUY") return [];
    const proof = marker.decision_evidence?.find((evidence) => WAVE_ENTRY_PATHS.has(evidence.wave_entry_path));
    if (!proof) return [];
    const cTime = marker.signal_time || marker.decision_timestamp?.slice(0, 10);
    const points = [
        { label: "A", time: proof.wave_a_high_date, price: proof.wave_a_high, position: "above" },
        { label: "B", time: proof.wave_b_low_date, price: proof.wave_b_low, position: "below" },
        { label: "C 确认", time: cTime, price: proof.wave_breakout_close, position: "above" },
    ];
    const marketDates = new Set(bars.map((bar) => bar.time));
    if (
        points.some((point) => !marketDates.has(point.time) || !Number.isFinite(point.price)) ||
        !(points[0].time < points[1].time && points[1].time <= points[2].time)
    )
        return [];
    return points;
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
            const colors = { A: "#ebbc70", B: "#5ebeb0", "C 确认": "#a29ce0" };
            for (const point of this.projected) {
                if (point.x < 20 || point.x > mediaSize.width - 20 || point.y < 8 || point.y > mediaSize.height - 8)
                    continue;
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
                context.lineTo(point.x, labelY + (point.position === "below" ? -7 : 7));
                context.strokeStyle = colors[point.label];
                context.lineWidth = 1.5;
                context.stroke();
                const text = `${point.label} ${point.price.toFixed(4)}`;
                context.lineWidth = 3;
                context.strokeStyle = "#102032";
                context.strokeText(text, point.x, labelY);
                context.fillText(text, point.x, labelY);
            }
            context.restore();
        });
    }
}
