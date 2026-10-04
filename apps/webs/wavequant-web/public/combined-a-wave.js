import { selectWaveConnections } from "./wave-connections.js";

function isDate(value) {
    return (
        typeof value === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(value) &&
        Number.isFinite(Date.parse(value)) &&
        new Date(value).toISOString().slice(0, 10) === value
    );
}

export function latestCombinedAObservation(observations, asof) {
    let latest = null;
    let latestKey = "";
    for (const observation of observations || []) {
        if (
            !isDate(observation?.originTime) ||
            !isDate(observation.cTime) ||
            !isDate(observation.available_at) ||
            observation.originTime >= observation.cTime ||
            observation.cTime > observation.available_at ||
            (asof && observation.available_at > asof) ||
            !Number.isFinite(observation.origin) ||
            !Number.isFinite(observation.cHigh)
        ) {
            continue;
        }
        // 先按截面排除未来确认，再选最新 C；同一 C 保留最近的组合起点。
        const key = [observation.cTime, observation.originTime, observation.available_at, observation.id || ""].join(
            "|",
        );
        if (!latest || key > latestKey) {
            latest = observation;
            latestKey = key;
        }
    }
    return latest;
}

function samePoint(left, right) {
    return (
        left?.time === right?.time &&
        left?.kind === right?.kind &&
        left?.value === right?.value &&
        (left?.index === undefined || right?.index === undefined || left.index === right.index)
    );
}

function pointReference(point) {
    return Object.fromEntries(
        ["time", "kind", "value", "available_at", "index", "label"]
            .filter((key) => point[key] !== undefined)
            .map((key) => [key, point[key]]),
    );
}

function knownPoint(point, kind, cutoff, byTime) {
    return (
        point?.kind === kind &&
        isDate(point.time) &&
        isDate(point.available_at) &&
        point.time <= point.available_at &&
        point.available_at <= cutoff &&
        Number.isFinite(point.value) &&
        point.value === byTime.get(point.time)?.[kind === "H" ? "high" : "low"]
    );
}

function sourcePaths(theory) {
    return new Map(
        ["reversal_trends", "secondary_trends", "tertiary_trends"]
            .flatMap((name) => theory?.[name]?.strokes || [])
            .map((stroke) => [stroke.id, stroke]),
    );
}

function sharesSource(stroke, sourcePath, paths) {
    if (!sourcePath) return false;
    const visited = new Set();
    for (let current = stroke; current && !visited.has(current.id); current = paths.get(current.source_path)) {
        if (current.id === sourcePath || current.source_path === sourcePath) return true;
        visited.add(current.id);
    }
    return false;
}

function projectionSourcePath(projection, theory, byTime, paths, asof) {
    if (projection.sourcePath) return paths.has(projection.sourcePath) ? projection.sourcePath : null;
    const candidates = (theory.secondary_trends?.strokes || []).filter(
        (stroke) =>
            paths.has(stroke.source_path) &&
            [
                { time: projection.aTime, kind: "H", value: projection.aHigh },
                { time: projection.bTime, kind: "L", value: projection.bLow },
                { time: projection.cTime, kind: "H", value: projection.cHigh },
            ].every((anchor) =>
                (stroke.points || []).some(
                    (point) => samePoint(point, anchor) && knownPoint(point, anchor.kind, asof, byTime),
                ),
            ),
    );
    const sources = [...new Set(candidates.map((stroke) => stroke.source_path))];
    return sources.length === 1 ? sources[0] : null;
}

// Freeze the last-fall-high context at the internal N, independently of later viewport summaries.
function formalPrecedingContexts(projection, theory, byTime, paths) {
    const event = (theory.events || []).find(
        (item) =>
            item.event === "n_completed" &&
            item.direction === "up" &&
            item.time === projection.nTime &&
            item.shape?.[0]?.time === projection.originTime &&
            item.shape?.[0]?.value === projection.origin,
    );
    const cutoff = event?.available_at || projection.nKnownAt || projection.nTime;
    if (!isDate(cutoff) || cutoff < projection.originTime || cutoff > projection.aTime) return [];
    return [2, 3].flatMap((level) => {
        const contexts = [];
        for (const stroke of theory[level === 2 ? "secondary_trends" : "tertiary_trends"]?.strokes || []) {
            if (!sharesSource(stroke, projection.sourcePath, paths)) continue;
            const points = stroke.points || [];
            const low = points
                .filter((point) => point.time <= projection.originTime && knownPoint(point, "L", cutoff, byTime))
                .sort(
                    (left, right) =>
                        left.time.localeCompare(right.time) || left.available_at.localeCompare(right.available_at),
                )
                .at(-1);
            if (!low) continue;
            const position = points.indexOf(low);
            let key = points
                .slice(0, position)
                .findLast((point) => point.time < projection.originTime && knownPoint(point, "H", cutoff, byTime));
            const transition = (stroke.key_transitions || [])
                .filter(
                    (item) =>
                        item.kind === "last_fall_high_reanchor" &&
                        isDate(item.available_at) &&
                        item.available_at <= cutoff &&
                        samePoint(item.active_low, low),
                )
                .sort((left, right) => left.available_at.localeCompare(right.available_at))
                .at(-1);
            if (transition) key = points.find((point) => samePoint(point, transition.new_key));
            if (!key || key.time >= low.time || !knownPoint(key, "H", cutoff, byTime)) continue;
            contexts.push({
                level,
                sourcePath: stroke.id,
                contextAsOf: cutoff,
                key: pointReference(key),
                low: pointReference(low),
                knownAt: [key.available_at, low.available_at, transition?.available_at].filter(Boolean).sort().at(-1),
                transition: transition || null,
            });
        }
        const context = contexts
            .sort(
                (left, right) =>
                    left.low.time.localeCompare(right.low.time) ||
                    left.knownAt.localeCompare(right.knownAt) ||
                    left.sourcePath.localeCompare(right.sourcePath),
            )
            .at(-1);
        return context ? [context] : [];
    });
}

// A displayed higher-level key may already be known at its source level before formal promotion.
// Select from that historical source directly; future higher-level membership must not decide the key.
function precedingContexts(projection, theory, byTime, paths) {
    const event = (theory.events || []).find(
        (item) =>
            item.event === "n_completed" &&
            item.direction === "up" &&
            item.time === projection.nTime &&
            item.shape?.[0]?.time === projection.originTime &&
            item.shape?.[0]?.value === projection.origin,
    );
    const cutoff = event?.available_at || projection.nKnownAt || projection.nTime;
    if (!isDate(cutoff) || cutoff < projection.originTime || cutoff > projection.aTime) return [];
    const formal = formalPrecedingContexts(projection, theory, byTime, paths);
    return [2, 3].flatMap((level) => {
        const sources = theory[level === 2 ? "reversal_trends" : "secondary_trends"]?.strokes || [];
        const candidates = sources
            .filter((stroke) => sharesSource(stroke, projection.sourcePath, paths))
            .flatMap((stroke) =>
                (stroke.points || [])
                    .filter((point) => point.time < projection.originTime && knownPoint(point, "H", cutoff, byTime))
                    .map((point) => ({ point, sourcePath: stroke.id })),
            );
        const candidate = candidates
            .sort(
                (left, right) =>
                    left.point.time.localeCompare(right.point.time) ||
                    left.point.available_at.localeCompare(right.point.available_at) ||
                    left.sourcePath.localeCompare(right.sourcePath),
            )
            .at(-1);
        return candidate
            ? [
                  {
                      level,
                      targetLevel: level,
                      sourceLevel: level - 1,
                      sourcePath: candidate.sourcePath,
                      contextAsOf: cutoff,
                      key: pointReference(candidate.point),
                      knownAt: candidate.point.available_at,
                      scope: "source_confirmed_display_context",
                      originEvidence: {
                          time: projection.originTime,
                          value: projection.origin,
                          available_at: cutoff,
                          scope: event ? "formal_n_origin" : "projection_n_origin",
                      },
                      formalContext: formal.find((item) => item.level === level) || null,
                  },
              ]
            : [];
    });
}

function reboundHighs(projection, theory, byTime, paths) {
    return (theory.reversal_trends?.strokes || [])
        .filter((stroke) => sharesSource(stroke, projection.sourcePath, paths))
        .flatMap((stroke) => stroke.points || [])
        .filter((point) => point.time > projection.cTime && knownPoint(point, "H", theory.asof, byTime))
        .sort(
            (left, right) => left.time.localeCompare(right.time) || left.available_at.localeCompare(right.available_at),
        );
}

// These candle thresholds match wave_continuation.py; this module emits chart observations only.
function strengthObservation(bar, previous, highs) {
    if (!previous || !(previous.volume > 0) || !(bar.volume > previous.volume) || !(bar.close > bar.open)) return null;
    const body = bar.close - bar.open;
    const range = bar.high - bar.low;
    const bodyPct = body / bar.open;
    const bodyRatio = range > 0 ? body / range : 0;
    const gap = bar.open > previous.high && bar.low > previous.high;
    const high = highs.findLast((point) => point.time < bar.time && point.available_at < bar.time);
    const bodyBreakout =
        body >= bar.open * 0.03 &&
        body >= range * 0.6 &&
        high &&
        previous.close <= high.value &&
        bar.close > high.value;
    if (!gap && !bodyBreakout) return null;
    return {
        time: bar.time,
        available_at: bar.time,
        type: gap ? "gap" : "body_breakout",
        price: bar.close,
        volume: bar.volume,
        referenceVolume: previous.volume,
        referenceHigh: gap
            ? { time: previous.time, price: previous.high, available_at: previous.time }
            : { time: high.time, price: high.value, available_at: high.available_at },
        bodyPct,
        bodyRatio,
    };
}

/** A confirmed ABC whose post-C pullback overlaps A is a candidate combined A, not an Elliott-wave count. */
export function combinedAObservations(bars, theory, projections) {
    const asof = theory?.asof || bars?.at(-1)?.time;
    if (!theory || !isDate(asof) || !Array.isArray(bars) || !Array.isArray(projections)) return [];
    const visible = bars.filter((bar) => bar.time <= asof);
    if (
        !visible.length ||
        visible.some(
            (bar, index) =>
                !isDate(bar.time) ||
                ![bar.open, bar.high, bar.low, bar.close].every(Number.isFinite) ||
                (index > 0 && visible[index - 1].time >= bar.time),
        )
    )
        return [];
    const byTime = new Map(visible.map((bar) => [bar.time, bar]));
    const paths = sourcePaths(theory);
    const observations = new Map();
    for (const inputProjection of projections) {
        if (!inputProjection) continue;
        const sourcePath = projectionSourcePath(inputProjection, theory, byTime, paths, asof);
        if (!sourcePath) continue;
        const projection = { ...inputProjection, sourcePath };
        if (
            !projection ||
            projection.aIsValid === false ||
            ![projection.originTime, projection.aTime, projection.bTime, projection.cTime, projection.cKnownAt].every(
                isDate,
            ) ||
            !(
                projection.originTime < projection.aTime &&
                projection.aTime < projection.bTime &&
                projection.bTime < projection.cTime &&
                projection.cTime <= projection.cKnownAt &&
                projection.cKnownAt <= asof
            ) ||
            projection.origin !== byTime.get(projection.originTime)?.low ||
            projection.aHigh !== byTime.get(projection.aTime)?.high ||
            projection.bLow !== byTime.get(projection.bTime)?.low ||
            projection.cHigh !== byTime.get(projection.cTime)?.high ||
            !(
                projection.origin < projection.bLow &&
                projection.bLow < projection.aHigh &&
                projection.aHigh <= projection.cHigh
            )
        )
            continue;
        const preconditions = precedingContexts(projection, theory, byTime, paths);
        if (!preconditions.length) continue;
        const pullback = visible.filter((bar) => bar.time > projection.cTime);
        const overlap = pullback.find((bar) => bar.low < projection.aHigh && bar.low >= projection.origin);
        const nextRise = pullback.find((bar) => bar.high > projection.cHigh);
        if (!overlap || (nextRise && nextRise.time <= overlap.time)) continue;
        const availableAt = [
            projection.cKnownAt,
            projection.aKnownAt,
            projection.bKnownAt,
            projection.confirmedAt,
            overlap.time,
            ...preconditions.map((item) => item.knownAt),
        ]
            .filter(Boolean)
            .sort()
            .at(-1);
        if (!isDate(availableAt) || availableAt > asof) continue;
        const invalidation = pullback.find((bar) => bar.low < projection.origin);
        if (invalidation && invalidation.time <= availableAt) continue;
        const price = (projection.origin + projection.cHigh) / 2;
        const end = invalidation?.time || visible.at(-1).time;
        const firstCloseBelowHalf = pullback.find((bar) => bar.time <= end && bar.close < price);
        const highs = reboundHighs(projection, { ...theory, asof }, byTime, paths);
        const strengthObservations = [];
        for (let index = 1; index < visible.length; index++) {
            const bar = visible[index];
            if (bar.time <= availableAt || bar.time <= projection.cTime) continue;
            if (
                (invalidation && bar.time >= invalidation.time) ||
                (firstCloseBelowHalf && bar.time >= firstCloseBelowHalf.time)
            )
                break;
            const strength = strengthObservation(bar, visible[index - 1], highs);
            if (strength) {
                strengthObservations.push(strength);
                break;
            }
        }
        const id = "combined-a:" + projection.sourcePath + ":" + projection.originTime + ":" + projection.cTime;
        const observation = {
            id,
            title: "组合大 A 50%",
            price,
            start: availableAt,
            end,
            available_at: availableAt,
            originTime: projection.originTime,
            origin: projection.origin,
            aTime: projection.aTime,
            aHigh: projection.aHigh,
            cTime: projection.cTime,
            cHigh: projection.cHigh,
            trendLevel: projection.trendLevel || 2,
            overlapTime: overlap.time,
            overlapPrice: overlap.low,
            preconditions,
            invalidatedAt: invalidation?.time || null,
            halfHeld: !firstCloseBelowHalf,
            firstCloseBelowHalf: firstCloseBelowHalf
                ? { time: firstCloseBelowHalf.time, close: firstCloseBelowHalf.close }
                : null,
            strengthObservations,
            scope: "combined_abc_chart_observation",
        };
        const previous = observations.get(id);
        if (
            !previous ||
            observation.available_at < previous.available_at ||
            (observation.available_at === previous.available_at && observation.aTime > previous.aTime)
        )
            observations.set(id, observation);
    }
    return [...observations.values()].sort(
        (left, right) => left.available_at.localeCompare(right.available_at) || left.id.localeCompare(right.id),
    );
}

export function combinedAWaveConnections(observations) {
    return selectWaveConnections(
        observations.map((observation) => ({
            id: observation.id,
            group: `combined-a:${observation.trendLevel || 2}`,
            observation,
            points: [
                { time: observation.originTime, value: observation.origin },
                { time: observation.cTime, value: observation.cHigh },
            ],
        })),
    );
}

export function combinedAAnnotations(observations) {
    const connected = new Set(combinedAWaveConnections(observations).map((line) => line.observation));
    return observations.flatMap((observation) => {
        const context = observation.preconditions
            .map(
                (item) =>
                    item.level +
                    "级显示末跌高 " +
                    item.key.time +
                    " " +
                    item.key.value.toFixed(4) +
                    "（源" +
                    item.sourceLevel +
                    "级 " +
                    item.knownAt +
                    " 已确认；" +
                    item.contextAsOf +
                    " 截面）",
            )
            .join("；");
        const status = observation.firstCloseBelowHalf
            ? observation.firstCloseBelowHalf.time +
              " 收盘 " +
              observation.firstCloseBelowHalf.close.toFixed(4) +
              " 跌破半幅，当前组合的守半强势条件已失效，后续收复不重置。"
            : "截至本截面，C 后回调收盘未跌破半幅；继续观察放量突破 K 线。";
        const description =
            "原 A 起点 " +
            observation.originTime +
            " " +
            observation.origin.toFixed(4) +
            " 至已确认 C 终点 " +
            observation.cTime +
            " " +
            observation.cHigh.toFixed(4) +
            " 作为组合大 A；50% = " +
            observation.price.toFixed(4) +
            "。C 后于 " +
            observation.overlapTime +
            " 低破原 A 终点，回调进入原 A 价格区间，据此作组合观察，不输出正式五浪计数。" +
            context +
            "。" +
            status +
            (observation.invalidatedAt ? observation.invalidatedAt + " 最低价跌破原起点，组合观察终止。" : "") +
            "放量沿用量大于前日；未回补跳空或实体至少3%且占振幅至少60%的前高突破，只作图表观察。";
        const base = {
            id: observation.id,
            time: observation.available_at,
            sourceTime: observation.cTime,
            kind: "combined-a-wave",
            category: "wave-projection",
            price: observation.price,
            markerPosition: "atPriceBottom",
            markerShape: "circle",
            color: "#9ec9f2",
            title: observation.title + " " + observation.price.toFixed(4),
            description,
            sourceLabel: "ABC 重叠组合 · 固定已确认来源的末跌高显示上下文 · 图表观察",
            priority: 190,
            levels: [],
            raw: observation,
        };
        return [
            base,
            ...(connected.has(observation)
                ? [
                      {
                          ...base,
                          id: observation.id + ":origin",
                          time: observation.originTime,
                          sourceTime: observation.originTime,
                          price: observation.origin,
                          title: "组合 A 起",
                          markerPosition: "atPriceBottom",
                          markerShape: "arrowUp",
                          color: "#d986aa",
                          priority: 210,
                      },
                      {
                          ...base,
                          id: observation.id + ":high",
                          time: observation.cTime,
                          price: observation.cHigh,
                          title: "组合 A 顶",
                          markerPosition: "atPriceTop",
                          markerShape: "arrowDown",
                          color: "#d986aa",
                          priority: 210,
                      },
                  ]
                : []),
            ...observation.strengthObservations.map((strength) => ({
                ...base,
                id: observation.id + ":strength:" + strength.time,
                time: strength.time,
                sourceTime: strength.time,
                kind: "combined-a-strength",
                price: strength.price,
                markerPosition: "belowBar",
                markerShape: "arrowUp",
                color: "#e6ba64",
                priority: 195,
                title: strength.type === "gap" ? "大 A 守半 · 放量跳空" : "大 A 守半 · 放量中大阳突破",
                description:
                    description +
                    "触发日 " +
                    strength.time +
                    " 收盘 " +
                    strength.price.toFixed(4) +
                    "，参考前高 " +
                    strength.referenceHigh.time +
                    " " +
                    strength.referenceHigh.price.toFixed(4) +
                    "；成交量 " +
                    strength.volume +
                    " > 前日 " +
                    strength.referenceVolume +
                    "，实体占开盘 " +
                    (strength.bodyPct * 100).toFixed(2) +
                    "%、占振幅 " +
                    (strength.bodyRatio * 100).toFixed(2) +
                    "%。触发时守半条件成立；后续状态另按原组合记录。",
                raw: { ...observation, strengthObservation: strength },
            })),
        ];
    });
}
