// A selected candle fixes A. Only later, already visible candles may supply B.
export function waveCProjection(bars, events, selectedTime) {
    const highIndex = bars.findIndex((bar) => bar.time === selectedTime);
    if (highIndex < 0 || highIndex >= bars.length - 1) return null;
    const highBar = bars[highIndex];
    if (!Number.isFinite(highBar.high)) return null;

    for (const event of [...(events || [])].reverse()) {
        if (event.event !== "n_completed" || event.direction !== "up") continue;
        const attackIndex = bars.findIndex((bar) => bar.time === event.time);
        const originIndex = bars.findIndex((bar) => bar.time === event.shape?.[0]?.time);
        const origin = event.shape?.[0]?.value;
        const oneP = event.levels?.find((level) => level.name === "1P 投影")?.price;
        if (
            attackIndex < 0 ||
            originIndex < 0 ||
            originIndex >= attackIndex ||
            attackIndex > highIndex ||
            event.available_at > selectedTime ||
            !Number.isFinite(origin) ||
            !Number.isFinite(oneP) ||
            (highIndex === attackIndex ? highBar.close <= oneP : highBar.high <= oneP) ||
            !Number.isFinite(event.defense) ||
            bars.slice(attackIndex + 1, highIndex + 1).some((bar) => bar.low < event.defense) ||
            bars.slice(attackIndex, highIndex).some((bar) => bar.high > highBar.high)
        )
            continue;

        let bottom = null;
        let corrected = false;
        for (let index = highIndex + 1; index < bars.length; index++) {
            const bar = bars[index];
            // Once A is exceeded, later lows belong to a new phase.
            if (bar.high > highBar.high) break;
            if (bar.low < event.defense) return null;
            if (bar.close < bars[index - 1].close) corrected = true;
            if (!bottom || bar.low < bottom.low) bottom = bar;
        }
        if (!corrected || !bottom || bottom.low <= origin || bottom.low >= highBar.high) return null;
        return {
            nTime: event.time,
            origin,
            oneP,
            aTime: selectedTime,
            aHigh: highBar.high,
            bTime: bottom.time,
            bLow: bottom.low,
            target: bottom.low + highBar.high - origin,
        };
    }
    return null;
}
