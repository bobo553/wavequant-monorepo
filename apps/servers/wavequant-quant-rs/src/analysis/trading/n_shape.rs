use super::{Direction, RuleCandle, teaching_inside, teaching_outside, validate_bars};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct PivotRef { pub index: usize, pub confirmed_index: usize, pub ordinal: Option<usize> }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BoxAnchorMode { AttackVirtualExtreme, NecklineExtremeExplicitAlternative }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MilestoneBasis { Extreme, Close }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ValueDomain { Price, Indicator }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NSetup {
    pub symbol: String,
    pub timeframe: String,
    pub direction: Direction,
    pub origin: PivotRef,
    pub neckline: PivotRef,
    pub pullback: PivotRef,
    pub source: String,
    pub box_anchor_mode: BoxAnchorMode,
    #[serde(default)] pub allow_confirmation_bar: bool,
    #[serde(default)] pub allow_outside_close: bool,
    #[serde(default)] pub allow_mother_impulse: bool,
    #[serde(default)] pub staged_defense: bool,
    #[serde(default)] pub allow_mother_pullback: bool,
    #[serde(default)] pub allow_inside_pullback: bool,
    #[serde(default)] pub polyline_structure: bool,
}
impl NSetup {
    pub fn validate(&self) -> Result<(), String> {
        if self.symbol.trim().is_empty() || self.timeframe.trim().is_empty() || self.source.trim().is_empty() {
            return Err("symbol, timeframe and source are required".into());
        }
        let refs = [self.origin, self.neckline, self.pullback];
        if refs.iter().any(|p| p.confirmed_index < p.index)
            || self.origin.confirmed_index > self.neckline.confirmed_index
            || self.neckline.confirmed_index > self.pullback.confirmed_index {
            return Err("pivot confirmations must follow source and be chronological".into());
        }
        let lecture_up = self.source == "lecture_causal" && self.direction == Direction::Up;
        if self.allow_inside_pullback && self.allow_mother_pullback {
            return Err("inside child and outside mother policies must be distinct".into());
        }
        let ordered_polyline = self.polyline_structure && refs.iter().all(|p| p.ordinal.is_some())
            && refs.windows(2).all(|p| (p[0].index, p[0].ordinal) < (p[1].index, p[1].ordinal));
        if self.polyline_structure && (!lecture_up || !ordered_polyline) {
            return Err("polyline structure requires ordered positive lecture pivots".into());
        }
        let mother = lecture_up && self.allow_mother_impulse
            && self.origin.index == self.neckline.index && self.neckline.index < self.pullback.index;
        let pullback = lecture_up && (self.allow_mother_pullback || self.allow_inside_pullback)
            && self.origin.index < self.neckline.index && self.neckline.index == self.pullback.index;
        if !(self.origin.index < self.neckline.index && self.neckline.index < self.pullback.index
            || ordered_polyline || mother || pullback) {
            return Err("A, B, C must be strictly chronological".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NOptions {
    pub timeframe: String,
    pub milestone_basis: MilestoneBasis,
    pub asof_index: Option<usize>,
    pub measurement_start: Option<usize>,
    pub measure_targets: bool,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum NStatus {
    #[serde(rename = "await_anchors")] AwaitAnchors,
    #[serde(rename = "forming")] Forming,
    #[serde(rename = "origin_breached_before_completion")] Invalidated,
    #[serde(rename = "pullback_extended_new_anchor_required")] PullbackExtended,
    #[serde(rename = "completed")] Completed,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NAnchors {
    pub origin: Decimal, pub neckline_extreme: Decimal, pub neckline_close: Decimal,
    pub pullback: Decimal, pub known_at_index: usize, pub retracement_ratio: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NCompletion {
    pub bar_index: usize, pub observed_at: String, pub real_break: bool, pub virtual_break: bool,
    pub virtual_low: Decimal, pub virtual_high: Decimal, pub defense: Decimal, pub defense_name: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NTargets {
    pub equal_wave: Option<Decimal>, pub one_p: Option<Decimal>, pub two_t: Option<Decimal>,
    pub box_anchor: Decimal, pub domain: ValueDomain, pub unavailable: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Milestone {
    pub name: String, pub target: Decimal, pub bar_index: usize, pub observed_at: String,
    pub observation_kind: String, pub shared_bar_with_previous: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NObservation {
    pub status: NStatus, pub asof_index: usize, pub observed_at: String,
    pub anchors: Option<NAnchors>, pub real_break_now: Option<bool>, pub virtual_break_now: Option<bool>,
    pub invalidated_index: Option<usize>, pub completion: Option<NCompletion>, pub targets: Option<NTargets>,
    pub milestones: Vec<Milestone>, pub next_target: Option<String>,
    pub first_defense_breach_index: Option<usize>, pub first_origin_breach_index: Option<usize>,
    pub measured_wave_complete: bool, pub stacking_precondition_observed: bool, pub stacking_rule: String,
}
impl NObservation {
    fn new(status: NStatus, index: usize, bar: &RuleCandle) -> Self {
        Self { status, asof_index: index, observed_at: bar.timestamp.clone(), anchors: None,
            real_break_now: None, virtual_break_now: None, invalidated_index: None, completion: None,
            targets: None, milestones: vec![], next_target: None, first_defense_breach_index: None,
            first_origin_breach_index: None, measured_wave_complete: false, stacking_precondition_observed: false,
            stacking_rule: "undefined_no_automatic_five_or_ten_targets".into() }
    }
}

pub fn project_n_targets(origin: Decimal, neckline: Decimal, pullback: Decimal, box_anchor: Decimal,
    direction: Direction, domain: ValueDomain, allow_equal_origin: bool) -> Result<NTargets, String> {
    if domain == ValueDomain::Price && [origin, neckline, pullback, box_anchor].iter().any(|v| *v <= Decimal::ZERO) {
        return Err("price anchors must be positive".into());
    }
    let sign = direction.sign();
    let distance = sign * (pullback - origin);
    if !((distance > Decimal::ZERO || allow_equal_origin && direction == Direction::Up && distance == Decimal::ZERO)
        && distance < sign * (neckline - origin)) {
        return Err("standard N requires a positive impulse and a partial retracement".into());
    }
    if sign * (box_anchor - neckline) < Decimal::ZERO { return Err("box anchor must be at or beyond neckline".into()); }
    let raw = [pullback + neckline - origin, Decimal::TWO * box_anchor - origin,
        Decimal::from(3) * box_anchor - Decimal::TWO * origin];
    let names = ["equal_wave", "one_p", "two_t"];
    let mut unavailable = vec![];
    let values = std::array::from_fn::<_, 3, _>(|i| {
        if domain == ValueDomain::Price && raw[i] <= Decimal::ZERO { unavailable.push(names[i].into()); None }
        else { Some(raw[i]) }
    });
    Ok(NTargets { equal_wave: values[0], one_p: values[1], two_t: values[2], box_anchor, domain, unavailable })
}

pub fn observe_n(bars: &[RuleCandle], setup: &NSetup, options: &NOptions) -> Result<NObservation, String> {
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("observe_n");

    setup.validate()?;
    if options.timeframe != setup.timeframe { return Err("NSetup with matching timeframe required".into()); }
    let end = options.asof_index.or_else(|| bars.len().checked_sub(1)).ok_or("as-of bar is unavailable")?;
    if end >= bars.len() || options.measurement_start.is_some_and(|start| start > end) {
        return Err("as-of bar or measurement start unavailable".into());
    }
    validate_bars(&bars[..=end])?;
    if bars[0].symbol != setup.symbol { return Err("setup and bar symbols differ".into()); }
    let known = setup.pullback.confirmed_index;
    let mut result = NObservation::new(NStatus::AwaitAnchors, end, &bars[end]);
    if known > end { return Ok(result); }
    let dir = setup.direction;
    let sign = dir.sign();
    let up = dir == Direction::Up;
    let a_index = setup.origin.index;
    let b_index = setup.neckline.index;
    let c_index = setup.pullback.index;
    let bb = &bars[b_index];
    if a_index == b_index && !setup.polyline_structure {
        let mother = &bars[a_index];
        if a_index == 0 || mother.close <= mother.open || mother.high <= bars[a_index - 1].high || mother.low >= bars[a_index - 1].low {
            return Err("mother impulse requires an observed bullish outside candle".into());
        }
    }
    if b_index == c_index && !setup.polyline_structure {
        let invalid = if setup.allow_inside_pullback {
            b_index == 0 || bb.close >= bb.open || bars[b_index - 1].close == bars[b_index - 1].open
                || !teaching_inside(&bars[b_index - 1], bb)
        } else { b_index == 0 || bb.close >= bb.open || bb.high <= bars[b_index - 1].high || bb.low >= bars[b_index - 1].low };
        if invalid { return Err("pullback requires the requested bearish child or outside mother".into()); }
    }
    let (a, b, c) = (dir.adverse(&bars[a_index]), dir.extreme(bb), dir.adverse(&bars[c_index]));
    project_n_targets(a, b, c, b, dir, ValueDomain::Price, setup.polyline_structure)?;
    result.anchors = Some(NAnchors { origin: a, neckline_extreme: b, neckline_close: bb.close,
        pullback: c, known_at_index: known, retracement_ratio: (b - c) / (b - a) });
    let same_confirmation = setup.allow_confirmation_bar && c_index < known
        && (setup.neckline.confirmed_index < known || setup.polyline_structure);
    let outside = setup.allow_outside_close && setup.source == "lecture_causal" && c_index == known && known > 0
        && teaching_outside(&bars[known - 1], &bars[known]);
    let outside_close = outside && sign * (bars[known].close - b) > Decimal::ZERO
        && sign * (bars[known].close - bb.close) > Decimal::ZERO && sign * (bars[known - 1].close - bb.close) <= Decimal::ZERO;
    let outside_turn = outside && setup.neckline.confirmed_index <= known && sign * (bars[known].close - bars[known].open) > Decimal::ZERO;
    let key_known = if same_confirmation { setup.neckline.confirmed_index } else { known };
    let neckline_start = a_index + usize::from(setup.source == "lecture_causal");
    let neckline_end = c_index + usize::from(!(outside_close || outside_turn));
    let mut invalid_neckline = false;
    let mut invalid_pullback = false;
    for (i, bar) in bars.iter().enumerate().take(c_index + 1).skip(a_index) {
        if sign * (dir.adverse(bar) - a) < Decimal::ZERO { return Err("origin is not a valid boundary of supplied structure".into()); }
        if i >= neckline_start && i < neckline_end && sign * (dir.extreme(bar) - b) > Decimal::ZERO { invalid_neckline = true; }
        if i > b_index && sign * (dir.adverse(bar) - c) < Decimal::ZERO { invalid_pullback = true; }
    }
    if invalid_neckline { return Err("neckline is not extreme of supplied A-B-C structure".into()); }
    if invalid_pullback { return Err("C is not pullback extreme of supplied structure".into()); }
    if bars[c_index + 1..=known].iter().any(|bar| sign * (dir.adverse(bar) - c) < Decimal::ZERO) {
        return Err("pullback pivot exceeded before its confirmation".into());
    }
    let start = known + usize::from(!(same_confirmation || outside_close || outside_turn));
    let mut first_stage = None;
    for i in start..=end {
        let bar = &bars[i];
        if sign * (dir.adverse(bar) - a) < Decimal::ZERO || sign * (dir.adverse(bar) - c) < Decimal::ZERO {
            result.status = if sign * (dir.adverse(bar) - a) < Decimal::ZERO { NStatus::Invalidated } else { NStatus::PullbackExtended };
            result.invalidated_index = Some(i);
            return Ok(result);
        }
        let extreme = dir.extreme(bar);
        if first_stage.is_none() && (sign * (bar.close - bb.close) > Decimal::ZERO || sign * (extreme - b) >= Decimal::ZERO) { first_stage = Some(i); }
        if i == known && outside_turn && !outside_close { continue; }
        if (!setup.polyline_structure && sign * (bar.close - bb.close) <= Decimal::ZERO) || sign * (extreme - b) <= Decimal::ZERO { continue; }
        let prev = &bars[i - 1];
        let previous_joint = (setup.polyline_structure || sign * (prev.close - bb.close) > Decimal::ZERO) && sign * (dir.extreme(prev) - b) > Decimal::ZERO;
        if previous_joint && !(outside_turn && !outside_close && i - 1 == known) { continue; }
        let outside_completion = outside_close && i == known;
        if !outside_completion && !setup.polyline_structure {
            let visible = i > key_known;
            let real_cross = visible && sign * (prev.close - bb.close) <= Decimal::ZERO && sign * (bar.close - bb.close) > Decimal::ZERO;
            let virtual_cross = visible && sign * (prev.close - b) <= Decimal::ZERO && sign * (extreme - b) > Decimal::ZERO;
            if !real_cross && !virtual_cross { continue; }
        }
        let vl = prev.close.min(bar.low);
        let vh = prev.close.max(bar.high);
        let mut defense = if up { vl } else { vh };
        if !outside_completion && setup.staged_defense {
            if let Some(stage) = first_stage {
                defense = bars[stage - 1].close;
                for item in &bars[stage..=i] { defense = if up { defense.min(item.low) } else { defense.max(item.high) }; }
            }
        }
        result.completion = Some(NCompletion { bar_index: i, observed_at: bar.timestamp.clone(),
            real_break: sign * (bar.close - bb.close) > Decimal::ZERO, virtual_break: true,
            virtual_low: vl, virtual_high: vh, defense, defense_name: if up { "轧空低" } else { "杀多高" }.into() });
        break;
    }
    let Some(completion) = &result.completion else {
        result.status = NStatus::Forming;
        result.real_break_now = Some(sign * (bars[end].close - bb.close) > Decimal::ZERO);
        result.virtual_break_now = Some(sign * (dir.extreme(&bars[end]) - b) > Decimal::ZERO);
        return Ok(result);
    };
    result.status = NStatus::Completed;
    if !options.measure_targets { return Ok(result); }
    let box_anchor = if setup.box_anchor_mode == BoxAnchorMode::AttackVirtualExtreme {
        if up { completion.virtual_high } else { completion.virtual_low }
    } else { b };
    let targets = project_n_targets(a, b, c, box_anchor, dir, ValueDomain::Price, setup.polyline_structure)?;
    let names = ["equal_wave", "one_p", "two_t"];
    let prices = [targets.equal_wave, targets.one_p, targets.two_t];
    for i in completion.bar_index.max(options.measurement_start.unwrap_or(0))..=end {
        let bar = &bars[i];
        let formation = i == completion.bar_index;
        let first_available = options.measurement_start == Some(i);
        if !formation {
            let adverse = if first_available { bar.close } else { dir.adverse(bar) };
            if result.first_defense_breach_index.is_none() && sign * (adverse - completion.defense) < Decimal::ZERO { result.first_defense_breach_index = Some(i); }
            if result.first_origin_breach_index.is_none() && sign * (adverse - a) < Decimal::ZERO { result.first_origin_breach_index = Some(i); }
        }
        if result.first_origin_breach_index.is_some() || up && result.first_defense_breach_index.is_some() { continue; }
        let price = if formation || first_available || options.milestone_basis == MilestoneBasis::Close { bar.close } else { dir.extreme(bar) };
        while result.milestones.len() < 3 {
            let next = result.milestones.len();
            let Some(target) = prices[next] else { break; };
            if sign * (price - target) < Decimal::ZERO { break; }
            let shared = result.milestones.last().is_some_and(|m| m.bar_index == i);
            let basis = if formation { "completion_close" } else if first_available { "eligibility_close" }
                else if options.milestone_basis == MilestoneBasis::Close { "close" } else { "extreme" };
            result.milestones.push(Milestone { name: names[next].into(), target, bar_index: i,
                observed_at: bar.timestamp.clone(), observation_kind: basis.into(), shared_bar_with_previous: shared });
        }
    }
    result.measured_wave_complete = result.milestones.len() == 3;
    result.stacking_precondition_observed = result.measured_wave_complete;
    result.next_target = names.get(result.milestones.len()).map(|name| (*name).into());
    result.targets = Some(targets);
    Ok(result)
}
