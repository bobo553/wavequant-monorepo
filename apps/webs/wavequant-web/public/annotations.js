// Presentation-only mappings. Strategy conditions remain in the Python engine.
import { label, num } from "./labels.js";

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
export function reversalWindowSummary(strokes, from, to) {
    const path = [...strokes].reverse().find((s) => s.points.some((p) => p.time >= from && p.time <= to));
    if (!path) return null;
    const points = path.points.filter((p) => p.time >= from && p.time <= to),
        highs = points.filter((p) => p.kind === "H"),
        lows = points.filter((p) => p.kind === "L");
    const high = highs.reduce((best, p) => (!best || p.value > best.value ? p : best), null);
    const low = lows.reduce((best, p) => (!best || p.value < best.value ? p : best), null);
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
        lastFallHigh: low?.preceding_turn,
        lastRiseLow: high?.preceding_turn,
        high,
        low,
        path: path.id,
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
            low = summary?.low;
        if (!spec || !key || !low) return [];
        return [
            {
                id: `last-fall-high:${level}:${summary.path}:${low.index}:${key.index}`,
                time: key.time,
                sourceTime: key.time,
                kind: "trend-key",
                category: "trend-keys",
                price: key.value,
                title: `${spec.numeral} 末跌高 · ${key.label} ${num(key.value)}`,
                description: `${spec.label}趋势线当前图窗以最低已确认低点 ${low.label}（${low.time}，${num(low.value)}）为分析低点；它左侧最近的同级已确认高点 ${key.label}（${key.time}，${num(key.value)}）就是该低点的末跌高。该低点到 ${low.available_at} 才确认；窗口最高点和后续普通反弹都不会替换此定义。`,
                sourceLabel: `${spec.label}趋势线 · 末跌高定义核验`,
                priority: 145 - level,
                color: spec.color,
                levels: [],
                raw: {
                    trend_level: level,
                    definition: "nearest_confirmed_same_level_high_left_of_selected_low",
                    key,
                    selected_low: low,
                    path: summary.path,
                    known_at: low.available_at,
                },
            },
        ];
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
                ? `${buy ? "B 买入" : "S 卖出"} ${num(item.price)}`
                : isRule
                  ? `${item.title}${g.items.length > 1 ? " +" + (g.items.length - 1) : ""}`
                  : item.title;
            return {
                ...g,
                marker: {
                    id: g.id,
                    time: g.time,
                    position:
                        (isFill || isTrendKey) && Number.isFinite(item.price)
                            ? buy
                                ? "atPriceBottom"
                                : "atPriceTop"
                            : isRule
                              ? "aboveBar"
                              : buy
                                ? "belowBar"
                                : "aboveBar",
                    ...((isFill || isTrendKey) && Number.isFinite(item.price) ? { price: item.price } : {}),
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
                          ? "arrowDown"
                          : isRule
                            ? "square"
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
