export interface ITopologyGate {
    id: string;
    question: string;
    detail: string;
    source: string;
    yes: string;
    no: string;
    yesNext?: string;
    noNext?: string;
    row?: number;
}

export interface ITopologyFlow {
    id: string;
    label: string;
    description: string;
    mode: "gates" | "exits";
    gates: readonly ITopologyGate[];
    completion: string;
}

/**
 * 每个判断都显式给出两条出口。源码变更由 strategy-topology.test.mjs
 * 的指纹门禁提醒维护者同步复核这里的条件和路径。
 */
export const topologyFlows: readonly [ITopologyFlow, ...ITopologyFlow[]] = [
    {
        id: "structure",
        label: "① 结构与候选",
        description: "常规 N 买点只使用截至当前交易日已确认的结构；V3 浅回撤横盘突破另走独立待选通道。",
        mode: "gates",
        completion: "进入买点分类与入场确认",
        gates: [
            {
                id: "history",
                question: "日线输入有效且日期唯一？",
                detail: "空输入直接返回；重复交易日期拒绝计算。历史截面只看截至当前日的数据。",
                source: "integrated_strategy.py · generate_system_signals:159–176",
                yes: "逐日观察结构",
                no: "停止：输入不可用",
            },
            {
                id: "pivot",
                question: "确认点足以形成 N 结构？",
                detail: "按策略配置使用讲义因果、严格折线或日线代理确认点；至少三个点，且不能处于未解决的结构区间。",
                source: "integrated_strategy.py · pivot_history:101–150；generate_system_signals:205–225",
                yes: "检查 N 几何",
                no: "等待更多确认点",
            },
            {
                id: "n-geometry",
                question: "N 点次序、窗口、完成条件成立？",
                detail: "常规 N 的起点、颈线、回档点严格按时间排序；母 N 特例单独处理。超过结构窗口或未完成的结构不进入候选。",
                source: "integrated_strategy.py · generate_system_signals:212–253",
                yes: "冻结攻击与防守位",
                no: "跳过该结构",
            },
            {
                id: "direction",
                question: "这是可参与做多的正 N？",
                detail: "倒 N 进入风险退出路径，不参与买入。一个攻击只发出一次常规入场；独立的盘整与 C 波证据可另行确认。",
                source: "integrated_strategy.py · generate_system_signals:550–592",
                yes: "检查盘态",
                no: "倒 N：退出或等待新正 N",
            },
            {
                id: "regime",
                question: "形成多头 / 强多头轧空盘态？",
                detail: "常规 V1、V2、V3 买点要求已确认的轧空盘态。V3 抵抗后若盘中短暂跌破滚动低点，但守住原 N 防守、当日无新空抵且阳线收盘创本轮新高，也可当日确认普通轧空。回档续攻、盘整缺口和 C 波续攻按各自证据恢复到多头盘态；浅回撤横盘突破另走独立待选通道。",
                source: "integrated_strategy.py · generate_system_signals:356–444, 626–641",
                yes: "交给买点分类",
                no: "候选拒绝：not_squeeze_regime",
            },
        ],
    },
    {
        id: "entry",
        label: "② 买点与公共门禁",
        description:
            "常规 V3 买点路径；独立的浅回撤横盘突破见“浅回撤待选突破”通道。V1/V2 和 V3 幅度变体的差异在下方方案说明中列明。",
        mode: "gates",
        completion: "生成 LONG 信号，交给执行价、仓位与成交门禁",
        gates: [
            {
                id: "first-buy",
                question: "第一类资格成立？",
                detail: "攻击时翻多尚未成熟，二/三级已确认交替的低点不低于翻多起点，之后有新正 N。幅度变体另要求交替低点或冻结窗口最低收盘的深回撤严格大于设定比例。",
                source: "whole_wave_entry.py · select_wave_entry:40–83；strategy_profiles.py · WAVE_PROFILES",
                yes: "第一类 · transition_squeeze",
                no: "继续检查第二类资格",
                yesNext: "pressure",
                noNext: "second-buy",
            },
            {
                id: "second-buy",
                question: "第二类资格成立？",
                detail: "翻多成熟须在攻击前确认；之后先有新高，再有回档点，再有新正 N。以峰值到翻多起点为分母，峰值到已观察最低收盘的回撤须符合当前方案的 ≤1/3、≤1/2 或 <1/2。",
                source: "whole_wave_entry.py · select_wave_entry:84–107；strategy_profiles.py · WAVE_PROFILES",
                yes: "第二类 · mature_shallow_squeeze",
                no: "拒绝：无有效第一类或第二类上下文",
                yesNext: "pressure",
                row: 1,
            },
            {
                id: "pressure",
                question: "上方二级阻力已解决？",
                detail: "二级突破受阻后，至少隔一日由无新空抵的阳线收盘越过抵抗阶段全部前高，且守住原突破防守，才解除阻断；盘中跌破前根滚动低点但收盘创本轮新高可通过。同一次多级突破证据或完成 A/B 后的 C 波恢复也可解除。",
                source: "integrated_strategy.py · generate_system_signals:593–611",
                yes: "检查原 N 收盘",
                no: "拒绝：secondary_breakout_resistance_unresolved",
            },
            {
                id: "original-close",
                question: "确认收盘高于原 N 攻击收盘？",
                detail: "低于或等于原 N 攻击棒收盘的反弹不能复活旧 N。",
                source: "integrated_strategy.py · generate_system_signals:617–620",
                yes: "检查弱 N",
                no: "拒绝：squeeze_below_original_n_close",
            },
            {
                id: "weak-n",
                question: "弱 N 已由连续轧空或新证据修复？",
                detail: "弱攻击需要未破防守、放量收盘突破续攻位；盘整、C 波、多级突破、反转或独立重确认可提供替代证据。",
                source: "integrated_strategy.py · generate_system_signals:621–635",
                yes: "检查倒 N 后再入场",
                no: "拒绝：weak_n_requires_uninterrupted_squeeze",
            },
            {
                id: "inverse-reentry",
                question: "倒 N 后重入场阻断已解除？",
                detail: "若旧倒 N 阻断仍在，必须出现新强轧空、深回档恢复、C 波恢复或符合条件的缺口路径。",
                source: "integrated_strategy.py · generate_system_signals:665–683；inverse_reentry.py",
                yes: "检查确认日量能",
                no: "拒绝：倒 N 后重入场未获证据",
            },
            {
                id: "volume",
                question: "量能通过或 C 波价格突破豁免？",
                detail: "V3 确认日累计量严格大于前一日总量；C 波已验证的价格突破可作为替代。V1/V2 使用相对量能门槛。",
                source: "integrated_strategy.py · generate_system_signals:687–699；strategy_profiles.py",
                yes: "检查止损与目标",
                no: "拒绝：attack_volume_unavailable_or_low",
            },
            {
                id: "target",
                question: "防守位低于价格且有未命中目标？",
                detail: "目标取最近未命中的等幅、1P、2T 或有效波段投影，不可跳过近目标抬高盈亏比。C 波使用冻结的防守和等幅目标。",
                source: "integrated_strategy.py · generate_system_signals:701–718",
                yes: "检查策略盈亏比",
                no: "拒绝：no_live_structural_risk_reward",
            },
            {
                id: "preflight",
                question: "前置盈亏比已通过或关闭？",
                detail: "V1/V2 启用时，收盘价到最近目标的毛盈亏比需达到 1.5。V3 关闭此信号前置门禁，执行价格仍需通过净盈亏比门禁。",
                source: "integrated_strategy.py · generate_system_signals:719–727；strategy_profiles.py",
                yes: "V3 关闭，或 V1/V2 毛盈亏比 ≥1.5",
                no: "拒绝：insufficient_close_gross_reward_risk；不消耗 N",
            },
        ],
    },
    {
        id: "wave",
        label: "③ A/B → C 波续攻",
        description: "普通 A 与强 A 分路，所有锚点在当天攻击前冻结。",
        mode: "gates",
        completion: "C 波证据回到公共入场门禁，目标为 B + A 等幅",
        gates: [
            {
                id: "ab-known",
                question: "原 N 已轧空，且此前 A/B 可冻结？",
                detail: "A 顶必须在今天之前出现，随后有真实回落并形成 B；B 不得跌破原 N 防守位。当天高点不能反过来构造自己的 A/B。",
                source: "wave_continuation.py · wave_pullback_context:11–40",
                yes: "比较 A 与原 N 目标",
                no: "等待完整 A/B 或防守失效",
            },
            {
                id: "a-class",
                question: "A 达到原 N 的 2T？",
                detail: "是＝强 A，冻结 2T 与防守 B，可观察等幅、1.618、2.618；否＝普通 A，必须至少到 1P，仅观察等幅目标。",
                source: "wave_continuation.py · wave_pullback_context:20–65",
                yes: "强 A：缺口或强实体放量",
                no: "普通 A：阳线收盘突破已知回档高点",
                noNext: "ordinary-trigger",
            },
            {
                id: "strong-trigger",
                question: "强 A 有缺口，或强实体且放量？",
                detail: "强实体需实体 ≥ 开盘价 3%，且实体 ≥ 全日振幅 60%；非缺口强实体还需高于前日成交量。",
                source: "wave_continuation.py · wave_gap_entry:84–97",
                yes: "检查突破证据",
                no: "强 A 暂不续攻；继续观察",
                yesNext: "defense-target",
            },
            {
                id: "ordinary-trigger",
                question: "普通 A：阳线收盘突破已知回档高点？",
                detail: "普通 A 至少触达 1P、未到 2T；B 守住原 N 防守位。当前棒须为阳线，且收盘高于 A 后已确认的回档高点。",
                source: "wave_continuation.py · wave_pullback_context:20–45；wave_gap_entry:112–126",
                yes: "普通 A 回升通道",
                no: "普通 A 等待收盘突破",
                yesNext: "defense-target",
                row: 1,
            },
            {
                id: "defense-target",
                question: "防守仍在，且价格低于 B+A 等幅？",
                detail: "当天低点不得失守原防守；已达到等幅目标则不再追入。非缺口棒若刷新 B，使用当前已观察低点重算目标。",
                source: "wave_continuation.py · wave_gap_entry:89–111",
                yes: "检查阻力与收盘",
                no: "失守防守或目标已达：不入场",
            },
            {
                id: "known-high",
                question: "对应的回升 / 突破证据成立？",
                detail: "阻力高点如被使用，必须位于 A 之后、今天之前，且已在今天之前确认。普通 A 必须阳线收盘站上；强 A 可走缺口价格突破、放量缺口或放量强实体收盘突破。",
                source: "wave_continuation.py · wave_gap_entry:112–126",
                yes: "形成 C 波价格 / 量能证据",
                no: "对应突破不成立：继续观察",
            },
        ],
    },
    {
        id: "execution",
        label: "④ 执行与成交",
        description: "LONG 信号通过后才进入模拟执行；任一拒单都不能显示为 B 成交。",
        mode: "gates",
        completion: "记录 BUY 成交、持仓和成本证据",
        gates: [
            {
                id: "execution-time",
                question: "信号仍有效，执行时点已到？",
                detail: "V3 常规买点按当天收盘模拟，盘整与 C 波可在已完成的 5 分钟证据后入场；待执行信号超出 TTL 则到期，不补造历史成交。",
                source: "backtest.py · run_backtest:349–378, 546–598；strategy_profiles.py · whole_wave_profile execution",
                yes: "取得实际执行价格",
                no: "到期或未确认：不成交",
            },
            {
                id: "position-slot",
                question: "持仓槽位可用且未持有该证券？",
                detail: "已达最大持仓数量或同一证券已有持仓时取消该笔买单。",
                source: "backtest.py · execute_entry:269–278",
                yes: "检查可买状态",
                no: "取消：position_limit / already_held",
            },
            {
                id: "buyable",
                question: "该时点可买入？",
                detail: "普通不可买或一字涨停拒单；V3 已观察到非一字涨停可按所选模型模拟成交，并明确标记未验证排队成交的假设。",
                source: "backtest.py · execute_entry:256–274；strategy_profiles.py · daily_limit_fill",
                yes: "检查执行价与防守",
                no: "取消：not_buyable / not_buyable_at_close",
            },
            {
                id: "price",
                question: "执行价高于失效位且跳空未超限？",
                detail: "实际执行价不得触及或低于信号失效位；相对信号参考价的跳空不得超过 max_entry_gap。",
                source: "backtest.py · execute_entry:273–278",
                yes: "检查目标是否仍有效",
                no: "取消：invalidated / entry_gap",
            },
            {
                id: "live-target",
                question: "最近目标仍高于实际执行价？",
                detail: "若目标已被开盘或收盘执行价耗尽，不把信号当成交。",
                source: "backtest.py · execute_entry:279–292",
                yes: "按四项上限计算股数",
                no: "取消：target_exhausted",
            },
            {
                id: "sizing",
                question: "仓位、风险、流动性、现金足够一手？",
                detail: "数量取仓位权重、风险预算、流动性、现金四个上限的最小值，再按复权整手向下取整，并计入手续费；不足最低买入数量则取消。",
                source: "backtest.py · execute_entry:293–315",
                yes: "检查净盈亏比",
                no: "取消：限制项低于一手 / 费用后现金不足",
            },
            {
                id: "net-rr",
                question: "启用时，执行价净盈亏比达到门槛？",
                detail: "以实际执行价、滑点、买卖费用和确定的股数计算净收益/净风险；启用过滤且低于信号要求时拒单，V3 默认要求 1.5。",
                source: "backtest.py · execute_entry:316–325；strategy_profiles.py · primary_filters",
                yes: "模拟 BUY 成交",
                no: "取消：insufficient_net_reward_risk",
            },
        ],
    },
    {
        id: "exit",
        label: "⑤ 风险退出与减仓",
        description: "先检查整仓风险退出，再考虑分段减仓；无触发时继续持有并观察下一根 K 线。",
        mode: "exits",
        completion: "无退出证据：持仓延续至下一交易日",
        gates: [
            {
                id: "structure-exit",
                question: "结构未解、倒 N、空头转向或前上升低点被收盘跌破？",
                detail: "系统信号先于同日新入场判断；触发 EXIT 后该日不再生成 LONG。",
                source: "integrated_strategy.py · generate_system_signals:550–573",
                yes: "EXIT 风险信号；停止当日入场",
                no: "检查 V3 整仓风险",
            },
            {
                id: "hard-risk",
                question: "放量倒 N、巨量高开反包、异常波段反转或趋势翻空？",
                detail: "V3 巨量高开强阴反包、放量倒 N、波段异常与趋势翻空可整仓退出。正 N 上攻巨量阴线压力区遇不利 K 通常全清；未回补跳空且收高时先减半，成交后首次收跌清余仓。持仓大幅上涨后盘中突破压力高点并遇空头抵抗，后续首次不利 K 也清仓。",
                source: "strategy_profiles.py · whole_wave_profile definition；wave_exhaustion_exit.py；trend_flip_exit.py；pressure_exit.py",
                yes: "优先整仓退出",
                no: "检查二级 C 浪抵抗失败",
            },
            {
                id: "secondary-c-exit",
                question: "已知二级 A/B/C 等幅目标到位后，双长影次日收低破位？",
                detail: "已确认二级低点与来源 A 高、B 低先于突破可知；盘中越过 A 高后连续两日空头抵抗，下一日触及等幅目标且上下影各占振幅至少 30%，再下一日阴线收盘跌破长影 K 线低点与 A 高，才按收盘清空余仓。",
                source: "trend_flip_exit.py · _secondary_wave_exhaustion_history；strategy_profiles.py · secondary_c_wave_reversal_exit",
                yes: "当日收盘清空余仓",
                no: "检查防守与目标状态",
            },
            {
                id: "target-not-exit",
                question: "只是达到 1P / 2T / 等幅 / 延伸目标？",
                detail: "V3 测量目标是观察里程碑，不自动生成目标卖单。V1/V2 保留各自封存的目标退出规则。",
                source: "strategy_profiles.py · whole_wave_profile:42–118",
                yes: "记录目标并继续观察退出证据",
                no: "检查分段减仓",
            },
            {
                id: "staged",
                question: "已验证分钟窗口跌破 35% / 65% 位或弱反弹？",
                detail: "V3 分段减仓由已完成的 5 分钟窗口触发，下一时间片撮合；更高优先级整仓风险先执行。分钟数据缺失时显式标记日线回退。",
                source: "staged_exit.py；strategy_profiles.py · whole_wave_profile execution",
                yes: "按阶段减仓 / 剩余仓位清退",
                no: "检查小 N 与放量下跌",
            },
            {
                id: "partial",
                question: "压力区未补跳空、小 N、放量下跌、普通 C 异常或减仓后倒 N 触发？",
                detail: "压力区不利 K 若跳空未回补且收高，先卖出当前持仓 50%；小 N 可累计减仓 30%，放量下跌累计 70%，普通 A 的 C 浪异常累计 80%，减仓后倒 N 累计 90%。各比例受已有卖出和整仓优先级约束。",
                source: "strategy_profiles.py · whole_wave_profile definition；integrated_strategy.py",
                yes: "记录对应累计减仓目标",
                no: "继续持仓",
            },
        ],
    },
    {
        id: "shallow-base",
        label: "浅回撤待选突破",
        description:
            "V3 独立买入通道，默认开启、可关闭。0.618 至 2/3 的低点先作为空多交替待选，不擅自升级为正式二/三级低点。",
        mode: "gates",
        completion: "只在突破确认日生成一次 LONG；仍须通过执行与成交门禁",
        gates: [
            {
                id: "shallow-enabled",
                question: "V3 浅回撤横盘突破开关已开启？",
                detail: "默认开启；关闭后该通道不生成待选证据或买点，常规 V3 信号不受影响。",
                source: "integrated_strategy.py · SystemStrategy.shallow_base_breakout_enabled；visualization.py · tdx_backtest",
                yes: "核对冻结的结构锚点",
                no: "通道关闭：不产生特殊买点",
            },
            {
                id: "shallow-candidate",
                question: "确认低点回撤达到 0.618、但不足 2/3？",
                detail: "使用当时可知的正式二/三级起点与高点，以及已确认的一级来源低点；回撤 0.618 ≤ 深度 < 2/3，且没有跌破原起点。只记待选，不改变正式趋势线。",
                source: "shallow_base_breakout.py · shallow_candidate_from_geometry；chart_entry_history.py",
                yes: "观察低点之后的横盘",
                no: "等待其他结构或普通买点",
            },
            {
                id: "shallow-base-range",
                question: "守低横盘至少 40 根、最多 120 根 K 线？",
                detail: "从待选低点到突破日经历 40–120 个交易日，期间不得跌破待选低点；最近 40 根 K 线的最高至最低振幅不超过 18%。",
                source: "shallow_base_breakout.py · shallow_base_history",
                yes: "检查阳线与量价突破",
                no: "继续等待或候选失效",
            },
            {
                id: "shallow-breakout",
                question: "放量大阳线收盘突破整段横盘前高？",
                detail: "阳线实体至少为开盘价 5%、占全天振幅至少 60%，收盘落在全天振幅上部 20%；量为此前 20 日均量至少 2 倍且大于前日，收盘超过待选低点之后全部已知 K 线的最高价。",
                source: "shallow_base_breakout.py · shallow_base_history",
                yes: "检查最近正式高点的空间",
                no: "不生成特殊买点",
            },
            {
                id: "shallow-risk",
                question: "以横盘低点防守、最近正式高点为目标，毛盈亏比至少 1.5？",
                detail: "突破日收盘必须高于最近 40 根 K 线最低价、低于此前冻结的正式波段高点；按该防守和最近目标计算毛盈亏比，低于策略门槛则放弃。",
                source: "shallow_base_breakout.py · shallow_base_history；integrated_strategy.py · generate_system_signals",
                yes: "浅回撤横盘突破 LONG",
                no: "空间不足：不追入",
            },
        ],
    },
] as const;

export const topologyProfileNotes = [
    "V3 默认：第一类为二/三级确认交替后新正 N；第二类浅回撤 ≤1/3。确认日量能严格大于前日；信号阶段不做毛盈亏比前置筛选。",
    "V3 幅度方案：第一类可要求 >2/3 或 >1/2，第二类可选 ≤1/3、≤1/2 或最低收盘价 <1/2；具体边界由 WAVE_PROFILES 决定。",
    "V3 独立浅回撤横盘突破默认开启、可关闭：0.618 ≤ 回撤 < 2/3 的一级来源低点先记为交替待选，守低窄幅横盘至少 40 根 K 线后，放量大阳线收盘突破整段横盘前高并满足风险收益门槛才生成买点。",
    "V2：分级双买点的旧定义；V1：讲义因果轧空定义；严格折线与日线代理是研究对照，不能把代理结果解释为 V3 因果证据。",
    "信号、执行和成交是不同层：拓扑中的 LONG 只代表候选通过。成本、风险预算、可用现金、整手与涨停撮合仍可能拒单。",
] as const;

/** 策略源码指纹；策略或证据逻辑变更时，复核路径后在此更新。 */
export const strategySourceDigest = "d276ed2a4533c8f8bee153642d61067327859d6f4b76202be11d21ecbdf69e31";
