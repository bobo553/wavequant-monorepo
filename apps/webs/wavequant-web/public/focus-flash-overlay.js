// 用户从研究列表主动定位时显示瞬时反馈，不参与常驻策略标记或命中检测。
export class FocusFlashOverlay {
    constructor(container) {
        this.container = container;
        this.views = [this];
        this.active = null;
        this.projected = null;
        this.frame = null;
        this.timer = null;
        this.tick = this.tick.bind(this);
        container.dataset.focusFlashActive = "false";
    }

    attached({ chart, series, requestUpdate }) {
        this.chart = chart;
        this.series = series;
        this.requestUpdate = requestUpdate;
    }

    detached() {
        this.clear();
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

    flash(item, stage) {
        this.clear();
        if (!item || !Number.isFinite(item.price)) return;
        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const token = stage === "execution" ? "--amber" : "--cyan";
        const styles = getComputedStyle(this.container);
        this.active = {
            id: item.id,
            time: item.time,
            price: item.price,
            color: styles.getPropertyValue(token).trim() || "#d5af71",
            background: styles.getPropertyValue("--panel").trim() || "#111d2d",
            reducedMotion,
            startedAt: performance.now(),
            elapsed: 0,
            duration: reducedMotion ? 1200 : 1700,
        };
        this.container.dataset.focusFlashActive = "true";
        this.container.dataset.focusFlashId = item.id;
        this.container.dataset.focusFlashMotion = reducedMotion ? "static" : "pulse";
        this.updateAllViews();
        this.requestUpdate?.();
        if (reducedMotion) this.timer = setTimeout(() => this.clear(), this.active.duration);
        else this.frame = requestAnimationFrame(this.tick);
    }

    tick(now) {
        if (!this.active) return;
        this.active.elapsed = now - this.active.startedAt;
        if (this.active.elapsed >= this.active.duration) {
            this.clear();
            return;
        }
        this.updateAllViews();
        this.requestUpdate?.();
        this.frame = requestAnimationFrame(this.tick);
    }

    updateAllViews() {
        if (!this.active || !this.chart || !this.series) return;
        const x = this.chart.timeScale().timeToCoordinate(this.active.time);
        const y = this.series.priceToCoordinate(this.active.price);
        this.projected = Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
        if (!this.projected) return;
        this.container.dataset.focusFlashX = String(x);
        this.container.dataset.focusFlashY = String(y);
    }

    draw(target) {
        if (!this.active || !this.projected) return;
        const { x, y } = this.projected;
        const { color, background, elapsed, duration, reducedMotion } = this.active;
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            if (x < 0 || x > mediaSize.width || y < 0 || y > mediaSize.height) return;
            const fade = reducedMotion ? 1 : Math.min(1, (duration - elapsed) / 250);
            context.save();
            context.strokeStyle = color;
            context.lineWidth = 3;
            context.shadowColor = color;
            context.shadowBlur = 10;
            for (const offset of reducedMotion ? [0] : [0, 0.5]) {
                const phase = reducedMotion ? 0 : (elapsed / 650 + offset) % 1;
                context.globalAlpha = fade * (reducedMotion ? 0.95 : 0.95 * (1 - phase));
                context.beginPath();
                context.arc(x, y, reducedMotion ? 14 : 8 + phase * 22, 0, 2 * Math.PI);
                context.stroke();
            }
            context.shadowBlur = 0;
            context.globalAlpha = fade;
            context.fillStyle = background;
            context.beginPath();
            context.arc(x, y, 5, 0, 2 * Math.PI);
            context.fill();
            context.stroke();
            context.restore();
        });
    }

    clear() {
        if (this.frame !== null) cancelAnimationFrame(this.frame);
        if (this.timer !== null) clearTimeout(this.timer);
        this.frame = null;
        this.timer = null;
        this.active = null;
        this.projected = null;
        this.container.dataset.focusFlashActive = "false";
        delete this.container.dataset.focusFlashId;
        delete this.container.dataset.focusFlashX;
        delete this.container.dataset.focusFlashY;
        delete this.container.dataset.focusFlashMotion;
        this.requestUpdate?.();
    }
}
