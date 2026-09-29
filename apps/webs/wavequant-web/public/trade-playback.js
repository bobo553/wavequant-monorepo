export function filledTradePlaybackEvents(view) {
    if (view?.result_scope !== "stock" || !view.backtest || view.backtest.status === "data_unavailable") return [];
    const plottedDates = new Set((view.bars || []).map((bar) => bar.time));
    return (view.markers || [])
        .map((marker, order) => ({ marker, order }))
        .filter(
            ({ marker }) =>
                marker.kind === "fill" &&
                ["BUY", "SELL"].includes(marker.side) &&
                typeof marker.id === "string" &&
                typeof marker.time === "string" &&
                marker.time <= view.asof &&
                plottedDates.has(marker.time),
        )
        .sort((left, right) => {
            const dateOrder = left.marker.time.localeCompare(right.marker.time);
            if (dateOrder) return dateOrder;
            const leftTimestamp = left.marker.execution_timestamp || left.marker.timestamp;
            const rightTimestamp = right.marker.execution_timestamp || right.marker.timestamp;
            return (
                (leftTimestamp && rightTimestamp ? leftTimestamp.localeCompare(rightTimestamp) : 0) ||
                left.order - right.order
            );
        })
        .map(({ marker }) => marker);
}

/** Advances through already calculated fills without requesting another backtest. */
export class TradePlayback {
    constructor({ onSelect, onChange, schedule = setTimeout, cancel = clearTimeout, intervalMs = 1_600 }) {
        Object.assign(this, { onSelect, onChange, schedule, cancel, intervalMs });
        this.events = [];
        this.index = -1;
        this.playing = false;
        this.completed = false;
        this.timer = null;
        this.emit();
    }

    snapshot() {
        return {
            total: this.events.length,
            index: this.index,
            current: this.events[this.index] || null,
            playing: this.playing,
            finished: this.completed,
        };
    }

    emit() {
        this.onChange(this.snapshot());
    }

    cancelNext() {
        if (this.timer === null) return;
        this.cancel(this.timer);
        this.timer = null;
    }

    setView(view) {
        this.cancelNext();
        this.playing = false;
        this.completed = false;
        this.events = filledTradePlaybackEvents(view);
        this.index = -1;
        this.emit();
    }

    scheduleNext() {
        this.timer = this.schedule(() => {
            this.timer = null;
            if (this.playing) this.advance();
        }, this.intervalMs);
    }

    advance() {
        const events = this.events;
        this.index++;
        this.onSelect(this.events[this.index], this.index);
        if (!this.playing || this.events !== events) {
            this.emit();
            return;
        }
        if (this.index === this.events.length - 1) {
            this.playing = false;
            this.completed = true;
        } else this.scheduleNext();
        this.emit();
    }

    play() {
        if (!this.events.length) return false;
        if (this.playing) return true;
        if (this.index === this.events.length - 1) this.index = -1;
        this.completed = false;
        this.playing = true;
        if (this.index < 0) this.advance();
        else {
            this.scheduleNext();
            this.emit();
        }
        return true;
    }

    pause() {
        if (!this.playing) return;
        this.cancelNext();
        this.playing = false;
        this.emit();
    }

    step(delta) {
        if (!this.events.length) return false;
        this.pause();
        const next =
            this.index < 0 && delta < 0
                ? this.events.length - 1
                : Math.max(0, Math.min(this.events.length - 1, this.index + delta));
        if (next === this.index) return false;
        this.index = next;
        this.completed = false;
        this.onSelect(this.events[next], next);
        this.emit();
        return true;
    }

    select(id) {
        const index = this.events.findIndex((event) => event.id === id);
        if (index < 0 || index === this.index) return false;
        this.cancelNext();
        this.playing = false;
        this.completed = false;
        this.index = index;
        this.emit();
        return true;
    }

    setSpeed(intervalMs) {
        if (!Number.isFinite(intervalMs) || intervalMs < 250 || intervalMs > 10_000) return false;
        this.intervalMs = intervalMs;
        if (this.playing) {
            this.cancelNext();
            this.scheduleNext();
        }
        return true;
    }
}
