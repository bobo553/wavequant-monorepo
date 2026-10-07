import { bottomNTargetCaption, isBottomNTargetSource } from "./bottom-n-targets.js";

/** 买点优先绑定 C 启动时已确认的正 N；原 A 的 N 不冒充新 C 的 N。 */
export function buyNTargetLevels(marker, wave, view, theory) {
    const signalDate = marker.signal_time || marker.signal_timestamp?.slice(0, 10) || marker.time;
    const knownAt = theory?.asof && theory.asof < view.asof ? theory.asof : view.asof;
    const knownEvents = (theory?.events || []).filter(
        (event) =>
            event.event === "n_completed" &&
            event.direction === "up" &&
            event.time <= signalDate &&
            (event.available_at || event.time) <= signalDate &&
            (event.available_at || event.time) <= knownAt,
    );
    const events = knownEvents.filter((event) => isBottomNTargetSource(event, theory));
    const sameDay = events
        .filter((event) => event.time === signalDate)
        .sort((left, right) => (right.n_level || 0) - (left.n_level || 0))[0];
    const cN = wave?.wave_b_low_date
        ? events
              .filter((event) => event.time >= wave.wave_b_low_date && event.shape?.[2]?.time >= wave.wave_b_low_date)
              .at(-1)
        : null;
    const proof = marker.decision_evidence?.find((proof) => proof.event === "long_signal");
    const attack = proof?.attack;
    const attackDate = proof?.attack_date;
    const attackEvent = attackDate
        ? knownEvents.find((event) => event.time === attackDate)
        : knownEvents.find((event) => Number.isInteger(attack) && event.bar_index === attack) ||
          knownEvents.find((event) => Number.isInteger(attack) && event.time === view.bars[attack]?.time);
    const attackN = isBottomNTargetSource(attackEvent, theory)
        ? attackEvent
        : events.find((event) => event.time === attackEvent?.target_source_date);
    // 原攻击棒抵抗失败的目标属于原 N；成交日恰好出现的新 N 不改写此测幅。
    const originalAttack = proof?.squeeze_confirmation === "resistance_attack_bar_break";
    const source =
        proof && Object.hasOwn(proof, "target_source_date")
            ? events.find((event) => event.time === proof.target_source_date)
            : originalAttack
              ? attackN
              : sameDay || cN || (!wave && attackN);
    if (!source || source.levels?.some((level) => level.valid_until && level.valid_until < signalDate)) return [];
    const oneP = source.one_p ?? source.levels?.find((level) => level.name === "1P 投影")?.price;
    const twoT = source.two_t ?? source.levels?.find((level) => level.name === "2T 投影")?.price;
    if (!Number.isFinite(oneP) || !Number.isFinite(twoT) || twoT <= oneP) return [];
    const availableAt = source.available_at || source.time;
    const anchor = source.shape?.[2]?.time || source.time;
    const caption = bottomNTargetCaption(source);
    const validUntil = source.levels?.find((level) => level.stage === "one_p")?.valid_until;
    const levels = [
        { name: "一饱（启动正 N）", stage: "one_p", price: oneP },
        { name: "二吐（启动正 N）", stage: "two_t", price: twoT },
    ].map((level) => ({
        ...level,
        ...(caption ? { display_name: `${level.name} · ${caption}` } : {}),
        anchor_at: anchor,
        available_at: availableAt,
        n_date: source.time,
        ...(source.levels?.find((original) => original.stage === level.stage)?.valid_until
            ? { valid_until: source.levels.find((original) => original.stage === level.stage).valid_until }
            : {}),
    }));
    if (!wave && source.shape?.length >= 3) {
        const [originPoint, highPoint, bottomPoint] = source.shape;
        const amplitude = highPoint.value - originPoint.value;
        if (Number.isFinite(amplitude) && amplitude > 0 && Number.isFinite(bottomPoint.value)) {
            levels.unshift(
                ...[
                    ["c_0618", "0.618×A", 0.618],
                    ["c_equal", "1×A", 1],
                ].map(([stage, title, ratio]) => ({
                    name: `C 浪目标 ${title}`,
                    stage,
                    price: Number((bottomPoint.value + ratio * amplitude).toFixed(12)),
                    anchor_at: bottomPoint.time,
                    available_at: availableAt,
                    n_date: source.time,
                })),
            );
        }
    }
    // 仅供图上预估：假设二吐后不回、五顶恰好到位；实际堆箱与超越五顶会改变远端目标。
    const box = twoT - oneP;
    const origin = oneP - 2 * box;
    const fiveTop = twoT + 3 * box;
    const estimates = { five_top: fiveTop, ten_full: 2 * fiveTop - origin };
    for (const [stage, title] of [
        ["five_top", "五顶"],
        ["ten_full", "十满"],
    ]) {
        const confirmed = source.levels?.find(
            (level) =>
                level.stage === stage &&
                Number.isFinite(level.price) &&
                (!level.available_at || level.available_at <= knownAt),
        );
        levels.push(
            confirmed
                ? {
                      ...confirmed,
                      ...(caption ? { display_name: `${confirmed.display_name || confirmed.name} · ${caption}` } : {}),
                      anchor_at: confirmed.available_at || source.time,
                      n_date: source.time,
                  }
                : {
                      name: `${title}（启动正 N · 预估叠箱）`,
                      ...(caption ? { display_name: `${title}（预估叠箱） · ${caption}` } : {}),
                      stage,
                      price: Number(estimates[stage].toFixed(12)),
                      estimated: true,
                      anchor_at: anchor,
                      available_at: availableAt,
                      ...(validUntil ? { valid_until: validUntil } : {}),
                      n_date: source.time,
                  },
        );
    }
    return levels;
}
