// Presentation-only mappings. Strategy conditions remain in the Python engine.
import { label, num, pct } from "./labels.js";

const RULES = {
    squeeze_alternation_confirmed: ["空多交替确认 · 正 N 轧空", "回调合格后，正 N 与轧空或强轧空确认 b 低点。", 168],
    squeeze_alternation_breakout: ["空多交替后突破 a 高点", "收盘严格突破 a 高点，解除突破前的 b 低点失效条件。", 169],
    squeeze_alternation_invalidated: ["空多交替低点失效", "尚未收盘突破 a 高点，最低价先跌破 b 低点。", 169],
    tertiary_c_candidate: ["Ⅲ c 段启动候选", "正 N 与轧空确认后的三级结构观察。", 168],
    tertiary_c_breakout: ["Ⅲ c 突破 a 高点", "收盘严格突破 a 段高点。", 169],
    tertiary_c_invalidated: ["Ⅲ c 候选失效", "最低价跌破原 b 段低点。", 169],
    hierarchy_alternation_ready: ["分级空多交替", "一级及以上的翻多后确认更高低点；V2 不使用交替回撤比例过滤。", 94],
    hierarchy_bull_matured: ["分级完整多头", "交替已知后的后续收盘超过冻结翻多高点，之后按浅回撤买点筛选。", 93],
    hierarchy_context_invalidated: ["分级多头失效", "结构防守被破坏，原分级入场依据失效。", 80],
    n_completed: ["N 字完成", "实过与虚过（或实破与虚破）完成；防守点取攻击棒虚拟低／高。", 100],
    bear_to_bull_flip: ["翻空为多", "收盘突破此前冻结的末跌高；只是趋势转换的第一步。", 95],
    bear_bull_alternation: ["空多交替", "翻多后确认更高回档低点，并通过浅回档或随后确认高点再次突破的结构判定。", 94],
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
    system_transition_squeeze: "第一类：交替后正 N 轧空",
    system_mature_shallow_squeeze: "第二类 · 重点：完整多头浅回撤后正 N 轧空",
    hierarchy_transition_not_ready: "一级及以上翻多交替未齐备",
    mature_pullback_sequence_not_ready: "完整多头之后的新回撤时序未齐备",
    mature_pullback_not_shallow: "完整多头回撤不小于 1/3",
    early_n_expired_after_maturity: "已进入完整多头，早期 N 不能继续沿用第一类免比例过滤",
    system_washout_reattack: "洗盘后的再次攻击",
    target_observed: "上一根已观察到目标到达",
    structural_stop_observed: "上一根已观察到结构止损条件",
    support_low_close_break_reduce: "最低价与收盘分别跌破前回踩 K 线，首次减仓",
    inverse_n_bull_resistance_failed_exit: "倒 N 减仓后多头抵抗失败，收盘跌破抵抗 K 虚拟低，当日清仓",
    volume_down_small_n_reduce_30: "放量回落但小实体仍在正 N 突破 K 范围内，次日累计减仓 30%",
    volume_down_reduce_70: "放量下跌且收盘低于前收，次日累计减仓至原持仓 70%",
    volume_down_support_break_clear: "放量下跌后跌破冻结回踩低点，当日清仓",
    trend_flip_resistance_adverse_clear: "高层级翻多受阻后出现不利K线，当日清仓",
    pressure_adverse_clear: "正 N 上攻前期巨量阴线压力区，出现不利 K 线，当日清仓",
    wave_gap_reversal_reduce: "目标阶段放量跳空高开大幅回落，减仓",
    wave_volume_shadows_reduce: "目标阶段放量大振幅、上下长影，当日累计减仓",
    wave_bull_resistance_failed_clear: "目标阶段大阴线击穿多头抵抗低点，当日清仓",
    wave_bearish_engulf_clear: "目标阶段大阴线反包前日阳线，当日清仓",
    volume_inverse_n_clear: "倒 N 确认且成交量超过前日，当日直接清仓",
    inverse_n_close_reduce_90: "收盘确认倒 N，当日累计减仓至原持仓 90%",
    inverse_n_after_reduction_90: "首次减仓后确认倒 N，累计减仓至原持仓 90%",
    support_low_break_reduce: "最低价跌破前回踩 K 线，收盘未跌破，减仓至 35%",
    reduction_below_one_lot: "目标减仓数量不足一手，未成交",
    weak_rebound_two_thirds_exit: "反弹最高价未突破下跌段 2/3，收盘再破破位低点清仓",
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
    wave_no_alternation_at_attack: "N 字攻击时尚无已确认的空多交替",
    first_buy_requires_level_two_or_three: "第一类买点需要二级或三级空多交替，一级不符合",
    wave_second_close_pullback_too_deep: "第二类买点的收盘回撤超过当前方案阈值",
    wave_context_no_longer_live: "攻击时的多头结构已失效",
    wave_alternation_not_before_n: "空多交替尚未先于 N 字攻击确认",
    wave_flip_origin_broken: "翻多起点已被跌破",
    wave_first_pullback_not_deep: "第一类买点的交替回撤未达到当前方案阈值",
    wave_second_peak_pullback_sequence: "第二类买点的高点与回撤时序未成立",
    risk_budget_below_one_lot: "单笔风险预算不足以买入一手",
    position_weight_below_one_lot: "单股仓位上限不足以买入一手",
    liquidity_below_one_lot: "流动性限额不足以买入一手",
    cash_below_one_lot: "可用现金不足以买入一手",
    cash_after_fees_below_one_lot: "扣除费用后现金不足以买入一手",
    insufficient_net_reward_risk: "成交价含费净盈亏比不足",
    time_exit: "达到最长持仓期限",
    entry_gap: "向上跳空超过入场上限",
    invalidated_at_open: "开盘已触及结构失效位",
    target_exhausted_at_open: "开盘目标空间已耗尽",
    not_buyable: "该开盘不满足可买条件",
    not_buyable_at_close: "当日收盘不满足可买条件",
    not_buyable_intraday: "盘中执行价格不满足可买条件",
    invalidated_at_close: "收盘已触及结构失效位",
    target_exhausted_at_close: "收盘目标空间已耗尽",
    same_day_exit_priority: "当日退出信号优先，取消收盘买入",
    already_held: "已有持仓",
    position_limit: "持仓数量达到上限",
    expired: "入场委托已过期",
    countermove_not_below_exact_two_thirds: "回档不小于精确的三分之二",
    same_bar_vertices_require_lower_timeframe_n: "同日高低点不能组成日线 N，须提供次级周期数据",
};
// These orders reached the execution layer after a strategy signal, but a
// portfolio/price risk guard prevented the simulated buy. Expiry and duplicate
// holdings are separate diagnostics, not evidence that risk sizing failed.
const EXECUTION_RISK_REASONS = new Set([
    "position_limit",
    "invalidated_at_open",
    "invalidated_at_close",
    "entry_gap",
    "target_exhausted_at_open",
    "target_exhausted_at_close",
    "risk_budget_below_one_lot",
    "position_weight_below_one_lot",
    "liquidity_below_one_lot",
    "cash_below_one_lot",
    "cash_after_fees_below_one_lot",
    "insufficient_net_reward_risk",
]);
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

export function visibleLastFallHighGuides(items, from, to) {
    return items.filter((item) => {
        const level = item.raw?.trend_level;
        if (level !== 2 && level !== 3) return true;
        const breakoutTime = item.raw?.breakout?.time;
        return (
            (item.time >= from && item.time <= to) ||
            (breakoutTime !== undefined && breakoutTime >= from && breakoutTime <= to)
        );
    });
}

/**
 * 将 Python 已确认的翻空为多高点转换为图表标识。
 *
 * 领域层决定哪个来源 H 完成了同级翻多以及何时可知；浏览器只按当前
 * 图窗和因果日期筛选，不使用窗口最高价替换服务端地标。
 */
export function bearToBullHighAnnotations(levelLandmarks, from, to, knownAt = to) {
    return levelLandmarks.flatMap(({ level, landmarks = [] }) => {
        const spec = LAST_FALL_HIGH_LEVELS[level];
        if (!spec) return [];
        return landmarks
            .filter(
                (landmark) =>
                    landmark.kind === "H" &&
                    landmark.time >= from &&
                    landmark.time <= to &&
                    landmark.available_at <= knownAt,
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
                    origin = landmark.retracement_origin,
                    rebreak = landmark.confirmed_rebreak_high;
                const squeeze = landmark.confirmation_rule === "positive_n_and_squeeze_after_qualified_b";
                const invalidated = landmark.invalidated_at && landmark.invalidated_at <= knownAt;
                const brokenOut = landmark.breakout_at && landmark.breakout_at <= knownAt;
                const confirmationText = squeeze
                    ? `价格或时间回调条件合格后，正 N + ${landmark.confirmation_evidence.regime}于 ${landmark.available_at} 确认 b 低点。${invalidated ? `${landmark.invalidated_at} 尚未收盘突破 a 高点就跌破 b 低点，本次交替低点已失效。` : brokenOut ? `${landmark.breakout_at} 收盘已严格突破 a 高点，解除突破前先跌破 b 的失效条件。` : "尚未收盘突破 a 高点前，若最低价跌破 b 低点，本次交替低点失效。"}`
                    : rebreak
                      ? `虽回档 ${pct(landmark.retracement_ratio)}，但随后已确认的 ${rebreak.label}（${rebreak.time}，${num(rebreak.value)}）严格突破原空翻多高点；完整证据到 ${landmark.available_at} 才可知。`
                      : `回档严格小于三分之二，完整证据到 ${landmark.available_at} 才可知。`;
                return {
                    id: `bear-bull-alternation-low:${level}:${landmark.id}`,
                    time: landmark.time,
                    sourceTime: landmark.time,
                    kind: "trend-key",
                    category: "trend-alternation-lows",
                    price: landmark.value,
                    markerPosition: "atPriceBottom",
                    markerShape: "arrowUp",
                    title: `${spec.numeral} 空多交替低点${invalidated ? "（已失效）" : ""} · ${landmark.label} ${num(landmark.value)}`,
                    description: squeeze
                        ? `${spec.label} a 段：${origin.time} ${num(origin.value)} → ${high.time} ${num(high.value)}；b 低点发生于 ${landmark.time}，回档 ${pct(landmark.retracement_ratio)}。${confirmationText}${squeezeConditionText(landmark.confirmation_evidence)}低点日期与确认日期分开记录，结构确认不等于买入成交。`
                        : `${spec.label}趋势线在 ${high.label}（${high.time}，${num(high.value)}）严格突破冻结末跌高 ${key.label}（${key.time}，${num(key.value)}）完成空翻多后，${landmark.label}（${landmark.time}，${num(landmark.value)}）相对 ${origin.label}（${origin.time}，${num(origin.value)}）形成 ${pct(landmark.retracement_ratio)} 回档，并保持高于原空头低点 ${bearLow.label}（${bearLow.time}，${num(bearLow.value)}）。${confirmationText}未确认回档或跌破原低点不会标记。`,
                    sourceLabel: `${spec.label}趋势线 · Python 已确认空多交替低点${squeeze ? "（正 N 轧空确认）" : landmark.source_level < level ? `（${LAST_FALL_HIGH_LEVELS[landmark.source_level]?.label || landmark.source_level}已确认回档证据）` : ""}`,
                    priority: 170 - level,
                    color: invalidated ? "#8292a9" : spec.color,
                    levels: [
                        { name: `${spec.label}空翻多高点`, price: high.value },
                        { name: `${spec.label}冻结末跌高`, price: key.value },
                        { name: `${spec.label}原空头低点`, price: bearLow.value },
                    ],
                    raw: {
                        ...landmark,
                        invalidated_at: invalidated ? landmark.invalidated_at : undefined,
                        breakout_at: brokenOut ? landmark.breakout_at : undefined,
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
                        ...(alternationLow ? [{ name: `${spec.label}空多交替低点`, price: alternationLow.value }] : []),
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
function squeezeConditionText(event) {
    const paths = [];
    if (event.price_path)
        paths.push(`价格条件：b 最低价 ${num(event.b_low_price)} ≥ 2/3 回撤价 ${num(event.two_thirds_price)}`);
    if (event.time_path)
        paths.push(
            `时间条件：b ${event.b_duration} 根 > a ${event.a_duration} 根，且 b 最低收盘 ${num(event.b_minimum_close)} < 1/2 回撤价 ${num(event.half_price)}`,
        );
    return `${paths.join("；")}。`;
}

function abcDescription(event, view) {
    const date = (index) => view.bars[index]?.time || "—";
    const outcome =
        event.event === "squeeze_alternation_confirmed"
            ? "b 低点确认为空多交替低点；此 N 只用于确认交替，须等待后续合格正 N 才能筛选买点。"
            : event.event === "squeeze_alternation_breakout"
              ? "收盘已严格突破 a 高点，解除突破前先跌破 b 的交替失效条件。"
              : event.event === "squeeze_alternation_invalidated"
                ? "尚未收盘突破 a 高点就先跌破 b 低点，本次交替低点失效，撤销其入场许可。"
                : event.event === "tertiary_c_candidate"
                  ? "b 回调可能结束、c 段可能启动，尚不保证突破 a 高点。"
                  : event.event === "tertiary_c_breakout"
                    ? "当前收盘已严格突破 a 高点，c 段突破得到确认。"
                    : "当前最低价已跌破原 b 低点，这次 c 启动候选失效。";
    return `a：${date(event.a_origin_index)} ${num(event.a_origin_price)} → ${date(event.a_high_index)} ${num(event.a_high_price)}；b 低点：${date(event.b_low_index)} ${num(event.b_low_price)}。${squeezeConditionText(event)}时间按交易 K 线间隔计算，b 截止低点，不含等待 N 的时间。${date(event.attack)} 正 N，${date(event.candidate_index)} ${event.regime}确认。${outcome}结构、策略买入信号与实际成交分别判断。`;
}

export function buildAnnotations(view, theory) {
    if (!view) return [];
    const marketDates = new Set(view.bars.map((b) => b.time));
    const items = view.markers.map((m) => {
        const fill = m.kind === "fill",
            signal = m.kind === "signal",
            riskRejection =
                m.kind === "order" &&
                m.side === "BUY" &&
                m.status === "cancelled" &&
                EXECUTION_RISK_REASONS.has(m.reason);
        const title = fill
            ? m.side === "BUY"
                ? "B 买入成交"
                : "S 卖出成交"
            : signal
              ? m.side === "LONG"
                  ? "买入信号"
                  : "退出信号"
              : riskRejection
                ? "风控未通过 · 买入未成交"
                : `委托${label(m.status)}`;
        const levels = [];
        if (Number.isFinite(m.price))
            levels.push({ name: fill ? "成交价" : riskRejection ? "拟买价（未成交）" : "信号参考价", price: m.price });
        if (Number.isFinite(m.stop)) levels.push({ name: "原始失效参考", price: m.stop });
        if (Number.isFinite(m.target)) levels.push({ name: "原始目标投影", price: m.target });
        const signalDate = m.signal_time || m.signal_timestamp?.slice(0, 10);
        const riskComparison =
            m.reason === "risk_budget_below_one_lot" &&
            Number.isFinite(m.risk_budget) &&
            Number.isFinite(m.one_lot_price_risk)
                ? `单笔风险预算 ${num(m.risk_budget)} 元，一手预估风险 ${num(m.one_lot_price_risk)} 元。`
                : "";
        return {
            ...m,
            title,
            levels,
            category: fill ? "fills" : signal ? "signals" : riskRejection ? "risk-rejections" : "orders",
            priority: fill ? 200 : riskRejection ? 160 : 150,
            description: riskRejection
                ? `${signalDate ? `${signalDate} 产生买入信号，` : ""}${m.time} 尝试买入时被执行风控拒绝：${reasonText(m.reason)}。${riskComparison}未实际买入。`
                : reasonText(m.reason) + (m.side === "EXIT" ? "。空仓时也可能出现，不代表已卖出。" : ""),
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
        const abc = ["tertiary_c_candidate", "tertiary_c_breakout", "tertiary_c_invalidated"].includes(event.event);
        const squeezeAlternation = event.event.startsWith("squeeze_alternation_");
        const candidateRejection = event.event === "entry_rejected" || event.event === "entry_preflight_rejected";
        const eventLevels = (event.levels || []).filter(
            (level) =>
                !level.available_at ||
                (level.available_at <= view.asof && level.available_at <= (theory.asof || view.asof)),
        );
        const attackDate = Number.isInteger(event.attack) ? view.bars[event.attack]?.time : null;
        const riskRatio =
            event.event === "entry_preflight_rejected" && Number.isFinite(event.gross_reward_risk)
                ? `收盘收益风险比 ${num(event.gross_reward_risk)}，要求至少 ${num(event.required_reward_risk)}。`
                : "";
        items.push({
            id: event.id,
            time: event.available_at,
            sourceTime: event.time,
            kind: abc ? "trend-key" : candidateRejection ? "candidate" : "rule",
            side: candidateRejection ? "LONG" : event.direction,
            price: event.price,
            title: candidateRejection ? "入场候选未通过" : ruleTitle(event),
            description:
                abc || squeezeAlternation
                    ? abcDescription(event, view)
                    : candidateRejection
                      ? `当日入场候选未通过策略筛选：${reasonText(event.reason)}。${attackDate ? `对应 N 字攻击 ${attackDate}。` : ""}${riskRatio}未产生买入信号，也未提交买单。`
                      : spec?.[1] || "当前引擎已记录的规则事件。",
            category: abc ? "tertiary-abc" : candidateRejection ? "entry-rejections" : spec?.[3] || "rules",
            priority: candidateRejection ? 155 : spec?.[2] || 10,
            levels: eventLevels,
            sourceLabel: abc
                ? "三级 a/b/c 结构观察 · 非成交"
                : candidateRejection
                  ? "当前引擎 · 策略入场候选评估"
                  : "当前引擎 · 所选历史前缀重算",
            ...(abc
                ? {
                      color: event.event === "tertiary_c_invalidated" ? "#8292a9" : "#ffad72",
                      markerPosition: "atPriceBottom",
                      markerShape: "circle",
                  }
                : {}),
            reason: event.reason,
            raw: { ...event, levels: eventLevels },
        });
    }
    const knownItems = items
        .filter((m) => m.time <= view.asof && marketDates.has(m.time))
        .sort((a, b) => a.time.localeCompare(b.time) || b.priority - a.priority || a.id.localeCompare(b.id));
    const longSignalsByDate = new Map();
    for (const item of knownItems) {
        if (item.kind !== "signal" || item.side !== "LONG") continue;
        if (!longSignalsByDate.has(item.time)) longSignalsByDate.set(item.time, []);
        longSignalsByDate.get(item.time).push(item);
    }
    for (const rejection of knownItems.filter((item) => item.category === "risk-rejections")) {
        const signalDate = rejection.raw.signal_time || rejection.raw.signal_timestamp?.slice(0, 10);
        const candidates = longSignalsByDate.get(signalDate) || [];
        const priceMatches = candidates.filter(
            (signal) =>
                Number.isFinite(rejection.raw.reference_price) &&
                Math.abs(signal.price - rejection.raw.reference_price) < 0.000001,
        );
        // 执行在次日开盘发生，只把已知证据挂到明确对应的原买入信号；歧义时不猜测归属。
        const signal = priceMatches.length === 1 ? priceMatches[0] : candidates.length === 1 ? candidates[0] : null;
        if (signal) (signal.executionRiskRejections ||= []).push(rejection);
    }
    return knownItems;
}
export function visibleAnnotations(items, options) {
    return items.filter((m) =>
        m.category === "fills"
            ? options.fills
            : m.category === "signals"
              ? options.signals
              : m.category === "entry-rejections"
                ? options.candidateRejections
                : m.category === "risk-rejections"
                  ? false
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
                                : m.category === "tertiary-abc"
                                  ? options.tertiaryAbc
                                  : options.rules,
    );
}
export function markerGroups(items, options, span = 140) {
    const groups = [];
    const rules = new Map();
    const candidates = new Map();
    for (const item of visibleAnnotations(items, options)) {
        if (item.kind === "rule" || item.category === "entry-rejections") {
            const grouped = item.category === "entry-rejections" ? candidates : rules;
            if (!grouped.has(item.time)) grouped.set(item.time, []);
            grouped.get(item.time).push(item);
        } else groups.push({ id: item.id, time: item.time, items: [item] });
    }
    for (const [time, entries] of rules) {
        entries.sort((a, b) => b.priority - a.priority);
        groups.push({ id: entries[0].id, time, items: entries });
    }
    for (const [time, entries] of candidates) {
        groups.push({ id: entries[0].id, time, items: entries });
    }
    return groups
        .sort((a, b) => a.time.localeCompare(b.time))
        .map((g, index) => {
            const item = g.items[0],
                isFill = item.kind === "fill",
                isRule = item.kind === "rule",
                isTrendKey = item.kind === "trend-key",
                isCandidateRejection = item.category === "entry-rejections",
                buy = item.side === "BUY" || item.side === "LONG";
            const text = isCandidateRejection
                ? ""
                : isFill
                  ? buy
                      ? "B"
                      : "S"
                  : isRule
                    ? `${item.title}${g.items.length > 1 ? " +" + (g.items.length - 1) : ""}`
                    : item.title;
            const markerPosition =
                item.markerPosition ||
                ((isFill || isTrendKey || isCandidateRejection) && Number.isFinite(item.price)
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
                    ...((isFill || isTrendKey || isCandidateRejection) &&
                    Number.isFinite(item.price) &&
                    markerPosition.startsWith("atPrice")
                        ? { price: item.price }
                        : {}),
                    color: isFill
                        ? buy
                            ? "#ff7d8c"
                            : "#40d6a3"
                        : isCandidateRejection
                          ? "#8c9db599"
                          : isTrendKey
                            ? item.color
                            : isRule
                              ? item.category === "diagnostic"
                                  ? "#8292a9"
                                  : item.raw?.event === "n_completed" && item.side === "down"
                                    ? "#40d6a3"
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
                        : isCandidateRejection
                          ? 0.8
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
