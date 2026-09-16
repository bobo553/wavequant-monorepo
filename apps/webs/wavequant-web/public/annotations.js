// Presentation-only mappings. Strategy conditions remain in the Python engine.
import { label, num, pct } from "./labels.js";

const RULES = {
    hierarchy_alternation_ready: ["分级空多交替", "一级及以上的翻多后确认更高低点；V2 不使用交替回撤比例过滤。", 94],
    hierarchy_bull_matured: ["分级完整多头", "交替已知后的后续收盘超过冻结翻多高点，之后按浅回撤买点筛选。", 93],
    hierarchy_context_invalidated: ["分级多头失效", "结构防守被破坏，原分级入场依据失效。", 80],
    n_completed: ["N 字完成", "实过与虚过（或实破与虚破）完成；防守点取攻击棒虚拟低／高。", 100],
    bear_to_bull_flip: ["翻空为多", "收盘突破此前冻结的末跌高；只是趋势转换的第一步。", 95],
    bear_bull_alternation: ["空多交替", "翻多之后的回档比例与结构通过交替判定。", 94],
    bullish_entry_permission_confirmed: [
        "多头趋势确认",
        "翻多、交替及高低点多头结构成立；之后的新 N 字才有入场资格。",
        93,
    ],
    regime_confirmation: ["盘态确认", "攻击后的抵抗及后续确认由六大盘态引擎判定，不等于成交。", 90],
    squeeze_resumption_observed: ["轧空回压恢复", "压回之后重新恢复上涨，仍须通过趋势、量能和风险收益条件。", 89],
    washout_reattack: ["洗盘再攻击", "已知洗盘结构后的新 N 字攻击，不单独构成买入指令。", 85],
    turn_confirmed: ["盘势扭转确认", "波段力道与次级连线条件满足当前引擎的扭转判定。", 84],
    turn_observation: ["盘势扭转疑虑", "观察阶段，尚不能等同于完整扭转确认。", 60],
    bull_permission_revoked: ["多头许可撤销", "原多头链条失效；在新链条成立之前不能沿用旧入场资格。", 80],
    bearish_context_frozen: ["冻结末跌高", "使用当时已确认的空头结构冻结参考压力与波段低点。", 65],
    strict_structure_interrupted: [
        "严格折线未解",
        "缺少次级路径时不猜测高低先后，停止沿用该段结构。",
        30,
        "diagnostic",
    ],
    entry_rejected: ["入场条件未通过", "仅为候选规则筛选失败，不代表委托被交易所拒绝。", 25, "diagnostic"],
    entry_preflight_rejected: ["盈亏比未通过", "当时参考价到失效位和最近未达目标的收益风险比不足。", 25, "diagnostic"],
    n_geometry_rejected: ["N 字几何未成立", "候选拐点未满足标准 N 字几何条件。", 20, "diagnostic"],
    long_signal: ["引擎入场观察", "当前引擎重算事件；买入信号与模拟成交分别展示，信号不保证成交。", 20, "diagnostic"],
    exit_signal: ["引擎退出观察", "风险退出观察可能在空仓时发生，不等于实际卖出。", 20, "diagnostic"],
    long_transition_evidence: ["入场趋势链证据", "记录该入场观察所依赖的翻多、交替和多头确认日期。", 25, "diagnostic"],
};
const REASONS = {
    system_squeeze_pullback_resume: "轧空回压后恢复上涨",
    system_n_continuation: "N 字延续",
    system_transition_squeeze: "第一类：交替后正 N 轧空（不限制回撤比例）",
    system_mature_shallow_squeeze: "第二类 · 重点：完整多头浅回撤后正 N 轧空",
    hierarchy_transition_not_ready: "一级及以上翻多交替未齐备",
    mature_pullback_sequence_not_ready: "完整多头之后的新回撤时序未齐备",
    mature_pullback_not_shallow: "完整多头回撤不小于 1/3",
    early_n_expired_after_maturity: "已进入完整多头，早期 N 不能继续沿用第一类免比例过滤",
    system_washout_reattack: "洗盘后的再次攻击",
    target_observed: "上一根已观察到目标到达",
    structural_stop_observed: "上一根已观察到结构止损条件",
    last_rise_low_close_broken: "收盘跌破末升低",
    inverse_n_risk_exit: "倒 N 字风险退出",
    negative_turn_risk_exit: "负扭转风险退出",
    strict_structure_unresolved: "严格折线高低路径未解",
    not_squeeze_regime: "不是轧空／强轧空盘",
    bullish_transition_not_ready: "翻多与交替链条尚未成立",
    n_attack_not_after_bullish_confirmation: "N 字攻击早于多头确认",
    attack_not_in_same_bullish_episode: "攻击与当前多头趋势不在同一结构段",
    countermove_too_deep: "回档幅度超限",
    attack_volume_unavailable_or_low: "量能不足或无法计算",
    no_live_structural_risk_reward: "没有有效结构止损或未达目标",
    insufficient_close_gross_reward_risk: "收盘参考盈亏比不足",
    risk_budget_below_one_lot: "单笔风险预算不足以买入一手",
    position_weight_below_one_lot: "单股仓位上限不足以买入一手",
    liquidity_below_one_lot: "流动性限额不足以买入一手",
    cash_below_one_lot: "可用现金不足以买入一手",
    cash_after_fees_below_one_lot: "扣除费用后现金不足以买入一手",
    insufficient_net_reward_risk: "开盘含费净盈亏比不足",
    time_exit: "达到最长持仓期限",
    entry_gap: "向上跳空超过入场上限",
    invalidated_at_open: "开盘已触及结构失效位",
    target_exhausted_at_open: "开盘目标空间已耗尽",
    not_buyable: "该开盘不满足可买条件",
    already_held: "已有持仓",
    position_limit: "持仓数量达到上限",
    expired: "入场委托已过期",
    countermove_not_below_exact_two_thirds: "回档不小于精确的三分之二",
    same_bar_vertices_require_lower_timeframe_n: "同日高低点不能组成日线 N，须提供次级周期数据",
};
export function reasonText(reason) {
    return (reason || "")
        .split("|")
        .map((r) => REASONS[r] || r)
        .join("；");
}
export function reversalWindowSummary(strokes, from, to, marketBars = []) {
    const path = [...strokes].reverse().find((s) => s.points.some((p) => p.time >= from && p.time <= to));
    if (!path) return null;
    const points = path.points.filter((p) => p.time >= from && p.time <= to),
        highs = points.filter((p) => p.kind === "H"),
        lows = points.filter((p) => p.kind === "L");
    const high = highs.reduce((best, p) => (!best || p.value > best.value ? p : best), null);
    const lowestLow = lows.reduce((best, p) => (!best || p.value < best.value ? p : best), null);
    // The Python domain layer owns close-break re-anchoring.  The browser only
    // selects an event that is already knowable by the visible window end and
    // resolves its referenced formal point; it does not reconstruct the rule.
    const lastFallHighReanchor = (path.key_transitions || [])
        .filter(
            (event) =>
                event.kind === "last_fall_high_reanchor" &&
                event.available_at <= to &&
                event.broken_low?.index === lowestLow?.index,
        )
        .sort((a, b) => a.available_at.localeCompare(b.available_at))
        .at(-1);
    const reanchoredLow = lastFallHighReanchor
        ? points.find(
              (point) =>
                  point.index === lastFallHighReanchor.active_low?.index &&
                  point.kind === lastFallHighReanchor.active_low?.kind,
          ) || lastFallHighReanchor.active_low
        : null;
    const low = reanchoredLow || lowestLow;
    const lowPosition = low ? path.points.indexOf(low) : -1,
        highPosition = high ? path.points.indexOf(high) : -1;
    // 连续显示路径可包含有真实来源的桥点；按图上交替顺序找相邻关键点，避免标注与折线口径分裂。
    const lastFallHigh = lastFallHighReanchor
        ? lastFallHighReanchor.new_key
        : lowPosition >= 0
          ? path.points
                .slice(0, lowPosition)
                .reverse()
                .find((point) => point.kind === "H") || low.preceding_turn
          : null;
    const lastRiseLow =
        highPosition >= 0
            ? path.points
                  .slice(0, highPosition)
                  .reverse()
                  .find((point) => point.kind === "L") || high.preceding_turn
            : null;
    // 图上虚线表达市场价格何时实际越过关键位，而非等待该 K 线日后被确认为新的同级 H。
    // 收盘必须从不高于关键位严格穿越到上方；盘中上影越线或相等收盘仍不算突破。
    const breakoutStart = low ? (low.available_at > low.time ? low.available_at : low.time) : null;
    let marketBreakout = null;
    if (low && lastFallHigh && breakoutStart && marketBars.length) {
        for (let index = 1; index < marketBars.length; index++) {
            const previous = marketBars[index - 1],
                current = marketBars[index],
                previousClose = Number(previous.close),
                close = Number(current.close);
            if (
                current.time >= breakoutStart &&
                current.time <= to &&
                Number.isFinite(previousClose) &&
                Number.isFinite(close) &&
                previousClose <= lastFallHigh.value &&
                close > lastFallHigh.value
            ) {
                marketBreakout = {
                    index: current.index ?? index,
                    time: current.time,
                    available_at: current.time,
                    kind: "K",
                    label: "收盘突破 K线",
                    value: close,
                    high: Number(current.high),
                    breakout_basis: "close_cross",
                };
                break;
            }
        }
    }
    // 同级高点只在低点已知之后、图窗截止日前完成确认时才可作为结构突破，
    // 发生日决定虚线终点，available_at 负责阻止使用当时尚不可知的未来信息。
    const structuralPoint =
        lowPosition >= 0 && lastFallHigh
            ? path.points
                  .slice(lowPosition + 1)
                  .find(
                      (p) =>
                          p.time <= to &&
                          p.available_at >= breakoutStart &&
                          p.available_at <= to &&
                          p.kind === "H" &&
                          p.value > lastFallHigh.value,
                  ) || null
            : null;
    const structuralBreakout = structuralPoint
        ? { ...structuralPoint, breakout_basis: "confirmed_same_level_high" }
        : null;
    // 市场首次收盘严格穿越是优先证据；没有收盘突破时，已确认且严格高于
    // 关键位的同级 H 仍是正式结构突破，不能因为传入了行情 K 线就被屏蔽。
    const lastFallHighBreakout = marketBreakout || structuralBreakout;
    const monotone = (sign) =>
        [highs, lows].every((seq) => seq.slice(1).every((p, i) => sign * (p.value - seq[i].value) > 0));
    const windowTrend =
        highs.length < 2 || lows.length < 2
            ? "反转点不足"
            : monotone(1)
              ? "多头趋势"
              : monotone(-1)
                ? "空头趋势"
                : "高低点不同向或相等";
    return {
        trend: points.at(-1).trend,
        lastFallHigh,
        lastFallHighBreakout,
        lastFallHighReanchor,
        lastRiseLow,
        high,
        low,
        lowestLow,
        path: path.id,
        displaySummary: Boolean(path.display_summary),
        latestKnown: points.at(-1).available_at,
        windowTrend,
    };
}

const LAST_FALL_HIGH_LEVELS = {
    1: { color: "#8fb8ff", label: "一级", numeral: "Ⅰ" },
    2: { color: "#d6a3ff", label: "二级", numeral: "Ⅱ" },
    3: { color: "#ffad72", label: "三级", numeral: "Ⅲ" },
};

/**
 * 将视窗趋势摘要转换成可核验的末跌高标识。
 *
 * 标识固定落在“图窗最低已确认 L 左侧最近的同级已确认 H”上，并保留
 * 对应低点及其确认日。这里不使用窗口最高点，也不从未确认尾端补点。
 */
export function lastFallHighAnnotations(levelSummaries) {
    return levelSummaries.flatMap(({ level, summary }) => {
        const spec = LAST_FALL_HIGH_LEVELS[level],
            key = summary?.lastFallHigh,
            low = summary?.low,
            breakout = summary?.lastFallHighBreakout,
            reanchor = summary?.lastFallHighReanchor,
            displaySummary = Boolean(summary?.displaySummary);
        if (!spec || !key || !low) return [];
        const breakoutText = breakout
            ? breakout.breakout_basis === "close_cross"
                ? `随后 ${breakout.time} K 线收盘 ${num(breakout.value)} 首次从关键位下方严格突破，水平虚线延长到这根 K 线；盘中上影越线或收盘相等不算突破。`
                : `随后 ${breakout.label}（${breakout.time}，${num(breakout.value)}）首次严格突破该末跌高，水平虚线延长到这根 K 线。`
            : null;
        const activeLowLevel = LAST_FALL_HIGH_LEVELS[reanchor?.active_low_source_level]?.label,
            activeLowText = reanchor
                ? reanchor.active_low_source_level === level
                    ? `把当前段切换到 ${reanchor.active_low.label}（${reanchor.active_low.time}，${num(reanchor.active_low.value)}）`
                    : `以${activeLowLevel || "下一级"}确认低点 ${reanchor.active_low.label}（${reanchor.active_low.time}，${num(reanchor.active_low.value)}）作为当前尾部低点证据`
                : null,
            reanchorText = reanchor
                ? `原${spec.label}低点 ${reanchor.broken_low.label}（${reanchor.broken_low.time}，${num(reanchor.broken_low.value)}）在 ${reanchor.confirmed_by.time} 被收盘 ${num(reanchor.confirmed_by.value)} 严格跌破；Python 领域规则${activeLowText}，其左侧 ${reanchor.new_key.label}（${reanchor.new_key.time}，${num(reanchor.new_key.value)}）成为新的末跌高，换锚从 ${reanchor.available_at} 起可知。`
                : null;
        return [
            {
                id: `last-fall-high:${level}:${summary.path}:${low.index}:${key.index}:${reanchor?.available_at ?? "base"}:${breakout?.index ?? "open"}`,
                time: key.time,
                sourceTime: key.time,
                kind: "trend-key",
                category: "trend-keys",
                price: key.value,
                title: `${spec.numeral} 末跌高 · ${key.label} ${num(key.value)}`,
                description: reanchorText
                    ? `${spec.label}趋势线当前末跌高已发生因果换锚。${reanchorText}${breakoutText || "新的末跌高截至当前图窗尚无收盘价严格突破。"}`
                    : displaySummary
                      ? `${spec.label}趋势线当前图窗按连续显示路径，以最低已确认来源低点 ${low.label}（${low.time}，${num(low.value)}）为分析低点；它左侧最近的交替高点 ${key.label}（${key.time}，${num(key.value)}）是图上末跌高。显示桥只统一图线与标注口径，不写回服务端趋势、二三级、策略或回测。${breakoutText || "截至当前图窗尚无收盘价严格突破，盘中上影越线或收盘相等不算突破。"}`
                      : `${spec.label}趋势线当前图窗以最低已确认低点 ${low.label}（${low.time}，${num(low.value)}）为分析低点；它左侧最近的同级已确认高点 ${key.label}（${key.time}，${num(key.value)}）就是该低点的末跌高。该低点到 ${low.available_at} 才确认；窗口最高点和后续普通反弹都不会替换此定义。${breakoutText || "截至当前图窗尚无收盘价严格突破，盘中上影越线或收盘相等不算突破。"}`,
                sourceLabel: `${spec.label}趋势线${displaySummary ? "连续显示路径" : ""} · 末跌高定义核验`,
                priority: 145 - level,
                color: spec.color,
                levels: [],
                raw: {
                    trend_level: level,
                    definition: reanchor
                        ? "server_confirmed_close_break_reanchor"
                        : "nearest_confirmed_same_level_high_left_of_selected_low",
                    key,
                    selected_low: low,
                    displaced_low: reanchor?.broken_low || null,
                    reanchor: reanchor || null,
                    breakout,
                    path: summary.path,
                    display_summary: displaySummary,
                    known_at: low.available_at,
                },
            },
        ];
    });
}

/**
 * 将 Python 已确认的翻空为多高点转换为图表标识。
 *
 * 领域层决定哪个来源 H 完成了同级翻多以及何时可知；浏览器只按当前
 * 图窗和因果日期筛选，不使用窗口最高价替换服务端地标。
 */
export function bearToBullHighAnnotations(levelLandmarks, from, to) {
    return levelLandmarks.flatMap(({ level, landmarks = [] }) => {
        const spec = LAST_FALL_HIGH_LEVELS[level];
        if (!spec) return [];
        return landmarks
            .filter(
                (landmark) =>
                    landmark.kind === "H" &&
                    landmark.time >= from &&
                    landmark.time <= to &&
                    landmark.available_at <= to,
            )
            .map((landmark) => {
                const low = landmark.confirmed_low,
                    key = landmark.broken_key,
                    keyText = key
                        ? `；该高点严格突破当时冻结的末跌高 ${key.label}（${key.time}，${num(key.value)}）`
                        : "";
                return {
                    id: `bear-to-bull-high:${level}:${landmark.id}`,
                    time: landmark.time,
                    sourceTime: landmark.time,
                    kind: "trend-key",
                    category: "trend-flip-highs",
                    price: landmark.value,
                    markerPosition: "atPriceTop",
                    markerShape: "arrowDown",
                    title: `${spec.numeral} 空翻多高点 · ${landmark.label} ${num(landmark.value)}`,
                    description: `${spec.label}趋势线正式低点 ${low.label}（${low.time}，${num(low.value)}）确认由空翻多时，${landmark.label}（${landmark.time}，${num(landmark.value)}）是完成转换的确认高点${keyText}。整条证据到 ${landmark.available_at} 才可知；它不是浏览器按当前窗口选择的最高价，也不等于买卖信号。`,
                    sourceLabel: `${spec.label}趋势线 · Python 空翻多确认高点`,
                    priority: 150 - level,
                    color: spec.color,
                    levels: key ? [{ name: `${spec.label}冻结末跌高`, price: key.value }] : [],
                    raw: {
                        ...landmark,
                        definition: "python_confirmed_bear_to_bull_source_high",
                    },
                };
            });
    });
}

/**
 * 将 Python 已确认的空多交替回档低点转换为图表标识。
 *
 * Core 已保存此前的空翻多高点、冻结末跌高和回档判定；浏览器只做
 * 图窗与回放截面过滤。确认日可以晚于低点所在图窗，但不能晚于当前
 * 回放截面；绝不从 K 线最低价猜测一个尚未确认的交替低点。
 */
export function bearBullAlternationLowAnnotations(levelLandmarks, from, to, knownAt = to) {
    return levelLandmarks.flatMap(({ level, landmarks = [] }) => {
        const spec = LAST_FALL_HIGH_LEVELS[level];
        if (!spec) return [];
        return landmarks
            .filter(
                (landmark) =>
                    landmark.kind === "L" &&
                    landmark.time >= from &&
                    landmark.time <= to &&
                    landmark.available_at <= knownAt,
            )
            .map((landmark) => {
                const high = landmark.confirmed_flip_high,
                    bearLow = landmark.confirmed_bear_low,
                    key = landmark.broken_key,
                    origin = landmark.retracement_origin;
                return {
                    id: `bear-bull-alternation-low:${level}:${landmark.id}`,
                    time: landmark.time,
                    sourceTime: landmark.time,
                    kind: "trend-key",
                    category: "trend-alternation-lows",
                    price: landmark.value,
                    markerPosition: "atPriceBottom",
                    markerShape: "arrowUp",
                    title: `${spec.numeral} 空多交替低点 · ${landmark.label} ${num(landmark.value)}`,
                    description: `${spec.label}趋势线在 ${high.label}（${high.time}，${num(high.value)}）严格突破冻结末跌高 ${key.label}（${key.time}，${num(key.value)}）完成空翻多后，${landmark.label}（${landmark.time}，${num(landmark.value)}）相对 ${origin.label}（${origin.time}，${num(origin.value)}）形成 ${pct(landmark.retracement_ratio)} 回档，并保持高于原空头低点 ${bearLow.label}（${bearLow.time}，${num(bearLow.value)}）。该低点到 ${landmark.available_at} 才完成空多交替确认；未确认回档、达到三分之二或跌破原低点都不会标记。`,
                    sourceLabel: `${spec.label}趋势线 · Python 已确认空多交替低点`,
                    priority: 145 - level,
                    color: spec.color,
                    levels: [
                        { name: `${spec.label}空翻多高点`, price: high.value },
                        { name: `${spec.label}冻结末跌高`, price: key.value },
                        { name: `${spec.label}原空头低点`, price: bearLow.value },
                    ],
                    raw: {
                        ...landmark,
                        definition: "python_confirmed_bear_bull_alternation_low",
                    },
                };
            });
    });
}

/**
 * 将空多交替后第一段已确认上涨趋势的终点高点转换为图表标识。
 *
 * 高点和对应交替低点均由 Core 选择；浏览器只负责因果日期与图窗过滤，
 * 不从后续 K 线或当前视窗最高价重算“多头段高点”。
 */
export function postAlternationBullHighAnnotations(levelLandmarks, from, to) {
    return levelLandmarks.flatMap(({ level, landmarks = [] }) => {
        const spec = LAST_FALL_HIGH_LEVELS[level];
        if (!spec) return [];
        return landmarks
            .filter(
                (landmark) =>
                    landmark.kind === "H" &&
                    landmark.time >= from &&
                    landmark.time <= to &&
                    landmark.available_at <= to,
            )
            .map((landmark) => {
                const low = landmark.confirmed_alternation_low,
                    flipHigh = landmark.confirmed_flip_high,
                    key = landmark.broken_key;
                return {
                    id: `post-alternation-bull-high:${level}:${landmark.id}`,
                    time: landmark.time,
                    sourceTime: landmark.time,
                    kind: "trend-key",
                    category: "trend-post-alternation-bull-highs",
                    price: landmark.value,
                    markerPosition: "atPriceTop",
                    markerShape: "arrowDown",
                    title: `${spec.numeral} 交替后多头段高点 · ${landmark.label} ${num(landmark.value)}`,
                    description: `${spec.label}趋势线在 ${low.label}（${low.time}，${num(low.value)}）完成空多交替后，紧接着的同级上涨段以 ${landmark.label}（${landmark.time}，${num(landmark.value)}）结束。该点是 Core 沿同一路径确认的第一个 L→H 高点，到 ${landmark.available_at} 才可知；此前空翻多高点为 ${flipHigh.label}（${flipHigh.time}，${num(flipHigh.value)}），冻结末跌高为 ${key.label}（${key.time}，${num(key.value)}）。浏览器不会跳过下一结构点去选择更远或更高的点。`,
                    sourceLabel: `${spec.label}趋势线 · Python 交替后首段多头高点`,
                    priority: 142 - level,
                    color: spec.color,
                    levels: [
                        { name: `${spec.label}空多交替低点`, price: low.value },
                        { name: `${spec.label}此前空翻多高点`, price: flipHigh.value },
                        { name: `${spec.label}冻结末跌高`, price: key.value },
                    ],
                    raw: {
                        ...landmark,
                        definition: "python_confirmed_post_alternation_first_bull_leg_high",
                    },
                };
            });
    });
}

/** 将 Core 预计算的交替后首次收盘突破转换为突破 K 标识。 */
export function bullishTurnSignalAnnotations(levelLandmarks, from, to) {
    return levelLandmarks.flatMap(({ level, landmarks = [] }) => {
        const spec = LAST_FALL_HIGH_LEVELS[level];
        if (!spec) return [];
        return landmarks
            .filter(
                (landmark) =>
                    landmark.kind === "K" &&
                    landmark.time >= from &&
                    landmark.time <= to &&
                    landmark.available_at <= to,
            )
            .map((landmark) => {
                const flipHigh = landmark.confirmed_flip_high,
                    alternationLow = landmark.confirmed_alternation_low;
                const confirmationText = alternationLow
                    ? `${spec.label}趋势线在 ${alternationLow.label}（${alternationLow.time}，${num(alternationLow.value)}）完成空多交替后，`
                    : `${spec.label}趋势线的空翻多高点已在 ${flipHigh.available_at || flipHigh.time} 完成确认；当前没有发布合格的同级空多交替低点。`;
                return {
                    id: `bullish-turn-signal:${level}:${landmark.id}`,
                    time: landmark.time,
                    sourceTime: landmark.time,
                    kind: "trend-key",
                    side: "LONG",
                    category: "trend-bullish-turn-signals",
                    price: landmark.value,
                    markerPosition: "belowBar",
                    markerShape: "arrowUp",
                    title: `${spec.numeral} 转多信号 · ${landmark.label} ${num(landmark.value)}`,
                    description: `${confirmationText}${landmark.label}（${landmark.time}）收盘 ${num(landmark.previous_close)} → ${num(landmark.value)}，首次从下向上严格突破此前空翻多高点 ${flipHigh.label}（${flipHigh.time}，${num(flipHigh.value)}）。盘中触碰、收盘相等或该高点确认前的突破均不产生转多信号。`,
                    sourceLabel: `${spec.label}趋势线 · Python 预计算转多信号`,
                    priority: 148 - level,
                    color: spec.color,
                    levels: [
                        { name: `${spec.label}空翻多高点`, price: flipHigh.value },
                        ...(alternationLow
                            ? [{ name: `${spec.label}空多交替低点`, price: alternationLow.value }]
                            : []),
                    ],
                    raw: {
                        ...landmark,
                        definition: "python_precomputed_first_close_cross_after_confirmed_alternation",
                    },
                };
            });
    });
}
export function ruleTitle(e) {
    if (e.event === "n_completed") return e.direction === "up" ? "正 N · 突破" : "倒 N · 跌破";
    if (e.event === "regime_confirmation") return e.regime || "盘态确认";
    if (e.event === "turn_confirmed") return e.direction === "up" ? "正扭转确认" : "负扭转确认";
    return RULES[e.event]?.[0] || label(e.event);
}
export function buildAnnotations(view, theory) {
    if (!view) return [];
    const marketDates = new Set(view.bars.map((b) => b.time));
    const items = view.markers.map((m) => {
        const fill = m.kind === "fill",
            signal = m.kind === "signal";
        const title = fill
            ? m.side === "BUY"
                ? "B 买入成交"
                : "S 卖出成交"
            : signal
              ? m.side === "LONG"
                  ? "买入信号"
                  : "退出信号"
              : `委托${label(m.status)}`;
        const levels = [];
        if (Number.isFinite(m.price)) levels.push({ name: fill ? "成交价" : "信号参考价", price: m.price });
        if (Number.isFinite(m.stop)) levels.push({ name: "原始失效参考", price: m.stop });
        if (Number.isFinite(m.target)) levels.push({ name: "原始目标投影", price: m.target });
        return {
            ...m,
            title,
            levels,
            category: fill ? "fills" : signal ? "signals" : "orders",
            priority: fill ? 200 : 150,
            description: reasonText(m.reason) + (m.side === "EXIT" ? "。空仓时也可能出现，不代表已卖出。" : ""),
            sourceLabel:
                m.source === "single_stock_backtest"
                    ? signal
                        ? "个股独立回测信号"
                        : "个股独立回测委托／成交"
                    : fill || !signal
                      ? "封存委托／成交记录"
                      : "封存策略信号",
            raw: m,
        };
    });
    for (const event of theory?.events || []) {
        const spec = RULES[event.event];
        items.push({
            id: event.id,
            time: event.available_at,
            sourceTime: event.time,
            kind: "rule",
            side: event.direction,
            price: event.price,
            title: ruleTitle(event),
            description: spec?.[1] || "当前引擎已记录的规则事件。",
            category: spec?.[3] || "rules",
            priority: spec?.[2] || 10,
            levels: event.levels || [],
            sourceLabel: "当前引擎 · 所选历史前缀重算",
            reason: event.reason,
            raw: event,
        });
    }
    return items
        .filter((m) => m.time <= view.asof && marketDates.has(m.time))
        .sort((a, b) => a.time.localeCompare(b.time) || b.priority - a.priority || a.id.localeCompare(b.id));
}
export function visibleAnnotations(items, options) {
    return items.filter((m) =>
        m.category === "fills"
            ? options.fills
            : m.category === "signals"
              ? options.signals
              : m.category === "orders"
                ? options.fills && options.diagnostics
                : m.category === "diagnostic"
                  ? options.rules && options.diagnostics
                  : m.category === "trend-keys"
                    ? options.trendKeys
                    : m.category === "trend-flip-highs"
                      ? options.bullFlipHighs
                      : m.category === "trend-alternation-lows"
                        ? options.bullAlternationLows
                        : m.category === "trend-post-alternation-bull-highs"
                          ? options.postAlternationBullHighs
                          : m.category === "trend-bullish-turn-signals"
                            ? options.bullishTurnSignals
                          : options.rules,
    );
}
export function markerGroups(items, options, span = 140) {
    const groups = [];
    const rules = new Map();
    for (const item of visibleAnnotations(items, options)) {
        if (item.kind === "rule") {
            const key = item.time;
            if (!rules.has(key)) rules.set(key, []);
            rules.get(key).push(item);
        } else groups.push({ id: item.id, time: item.time, items: [item] });
    }
    for (const [time, entries] of rules) {
        entries.sort((a, b) => b.priority - a.priority);
        groups.push({ id: entries[0].id, time, items: entries });
    }
    return groups
        .sort((a, b) => a.time.localeCompare(b.time))
        .map((g, index) => {
            const item = g.items[0],
                isFill = item.kind === "fill",
                isRule = item.kind === "rule",
                isTrendKey = item.kind === "trend-key",
                buy = item.side === "BUY" || item.side === "LONG";
            const text = isFill
                ? buy
                    ? "B"
                    : "S"
                : isRule
                  ? `${item.title}${g.items.length > 1 ? " +" + (g.items.length - 1) : ""}`
                   : item.title;
            const markerPosition =
                item.markerPosition ||
                ((isFill || isTrendKey) && Number.isFinite(item.price)
                    ? buy
                        ? "atPriceBottom"
                        : "atPriceTop"
                    : isRule
                      ? "aboveBar"
                      : buy
                        ? "belowBar"
                        : "aboveBar");
            return {
                ...g,
                marker: {
                    id: g.id,
                    time: g.time,
                    position: markerPosition,
                    ...((isFill || isTrendKey) &&
                    Number.isFinite(item.price) &&
                    markerPosition.startsWith("atPrice")
                        ? { price: item.price }
                        : {}),
                    color: isFill
                        ? buy
                            ? "#ff7d8c"
                            : "#40d6a3"
                        : isTrendKey
                          ? item.color
                          : isRule
                            ? item.category === "diagnostic"
                                ? "#8292a9"
                                : "#b69af5"
                            : item.kind === "order"
                              ? "#8292a9"
                              : buy
                                ? "#49d5dc"
                                : "#e6ba64",
                    shape: isFill
                        ? buy
                            ? "arrowUp"
                            : "arrowDown"
                        : isTrendKey
                          ? item.markerShape || "arrowDown"
                          : isRule
                          ? "circle"
                            : "circle",
                    text: isRule && span > 70 && index % Math.ceil(span / 70) !== 0 ? "" : text,
                    size: isFill
                        ? 1.5
                        : isTrendKey
                          ? 0.8
                          : isRule
                            ? 0.65
                            : item.kind === "signal" && item.side === "EXIT"
                              ? 0.45
                              : 1,
                },
            };
        });
}

// Keep every icon; suppress only colliding text. Details stay accessible.
export function avoidLabelCollisions(groups, timeToX) {
    const occupied = [];
    function bounds(group) {
        const x = timeToX(group.time);
        if (x === null) return null;
        const half = Math.max(30, group.marker.text.length * 3.5);
        return [x - half, x + half];
    }
    for (const g of groups.filter((g) => g.items[0].kind === "fill")) {
        const box = bounds(g);
        if (box) occupied.push(box);
    }
    const ranked = groups
        .filter((g) => g.items[0].kind !== "fill")
        .sort((a, b) => b.items[0].priority - a.items[0].priority);
    for (const g of ranked) {
        if (!g.marker.text) continue;
        const box = bounds(g);
        if (!box) continue;
        if (occupied.some((b) => box[0] < b[1] + 8 && box[1] > b[0] - 8)) g.marker.text = "";
        else occupied.push(box);
    }
    return groups;
}
