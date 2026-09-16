// 真实模拟成交与普通规则共享 K 线，但不能被后添加的趋势线盖住。
// 仅消费已成交的标记；信号和候选点不能在这里伪装成 B/S。
export class TradeMarkerOverlay {
    constructor(container) {
        this.container = container;
        this.markers = [];
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

    setMarkers(markers) {
        this.markers = markers;
        this.updateAllViews();
        this.requestUpdate?.();
    }

    updateAllViews() {
        if (!this.chart || !this.series) return;
        const scale = this.chart.timeScale();
        this.projected = this.markers
            .filter((marker) => Number.isFinite(marker.price) && ["BUY", "SELL"].includes(marker.side))
            .map((marker) => ({
                ...marker,
                x: scale.timeToCoordinate(marker.time),
                priceY: this.series.priceToCoordinate(marker.price),
            }))
            .filter((marker) => marker.x !== null && marker.priceY !== null)
            .map((marker) => ({
                ...marker,
                y: marker.priceY + (marker.side === "BUY" ? 19 : -19),
            }));
        this.container.dataset.tradeLabelCount = String(this.projected.length);
    }

    draw(target) {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            context.save();
            context.textAlign = "center";
            context.textBaseline = "middle";
            context.font = "800 18px ui-sans-serif, system-ui, sans-serif";
            context.lineJoin = "round";
            for (const marker of this.projected) {
                if (marker.x < 12 || marker.x > mediaSize.width - 12 || marker.y < 12 || marker.y > mediaSize.height - 12)
                    continue;
                const buy = marker.side === "BUY";
                context.beginPath();
                context.moveTo(marker.x, marker.priceY + (buy ? 5 : -5));
                context.lineTo(marker.x, marker.y + (buy ? -10 : 10));
                context.strokeStyle = buy ? "#ef7180" : "#3fba97";
                context.lineWidth = 2;
                context.stroke();
                const letter = buy ? "B" : "S";
                context.lineWidth = 3;
                context.strokeStyle = "#102032";
                context.strokeText(letter, marker.x, marker.y + 0.5);
                context.fillStyle = buy ? "#ef7180" : "#3fba97";
                context.fillText(letter, marker.x, marker.y + 0.5);
            }
            context.restore();
        });
    }

    hitTest(x, y) {
        for (const marker of [...this.projected].reverse())
            if (Math.hypot(x - marker.x, y - marker.y) <= 11)
                return { externalId: marker.id, zOrder: "top", cursorStyle: "pointer" };
        return null;
    }
}
