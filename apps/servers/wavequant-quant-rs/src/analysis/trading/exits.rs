use super::{RuleCandle, RuleDecision, ratio};
use super::entries::{is_volume_bearish, medium_bearish_body, volume_upper_shadow_rejection};
use rust_decimal::{Decimal, RoundingStrategy};
use serde::{Deserialize, Serialize};

pub fn support_break_reduction(day_low: Decimal, current_price: Decimal, support_low: Decimal, support_close: Decimal) -> Result<Decimal, String> {
    if [day_low, current_price, support_low, support_close].iter().any(|v| *v <= Decimal::ZERO)
        || day_low > current_price || support_low > support_close { return Err("invalid support-break prices".into()); }
    Ok(if day_low >= support_low { Decimal::ZERO } else if current_price < support_close { ratio(65, 100) } else { ratio(35, 100) })
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClosingReductionIntent { pub target_fraction: Decimal, pub target_quantity: u64, pub order_quantity: u64 }

/// 时间由应用层先转换到交易所时区；只有 14:30（含）至 15:00（不含）可下单。
pub fn closing_reduction_intent(exchange_minute: u16, day_low: Decimal, current_price: Decimal,
    support_low: Decimal, support_close: Decimal, original_quantity: u64, filled_quantity: u64,
    pending_quantity: u64, sellable_quantity: u64, previous_target: Decimal, lot_size: u64)
    -> Result<Option<ClosingReductionIntent>, String> {
    let accounted = filled_quantity.checked_add(pending_quantity).ok_or("share quantity overflow")?;
    if original_quantity == 0 || lot_size == 0 || accounted > original_quantity
        || sellable_quantity > original_quantity - filled_quantity
        || ![Decimal::ZERO, ratio(35, 100), ratio(65, 100)].contains(&previous_target) {
        return Err("invalid holding quantities or cumulative reduction target".into());
    }
    let target = previous_target.max(support_break_reduction(day_low, current_price, support_low, support_close)?);
    if !(870..900).contains(&exchange_minute) { return Ok(None); }
    let percentage: u64 = if target == ratio(65, 100) { 65 } else if target == ratio(35, 100) { 35 } else { 0 };
    let quantity = u128::from(original_quantity) * u128::from(percentage) / (100 * u128::from(lot_size)) * u128::from(lot_size);
    let target_quantity = u64::try_from(quantity).map_err(|_| "target quantity overflow")?;
    let order_quantity = target_quantity.saturating_sub(accounted).min(sellable_quantity.saturating_sub(pending_quantity)) / lot_size * lot_size;
    Ok((order_quantity > 0).then_some(ClosingReductionIntent { target_fraction: target, target_quantity, order_quantity }))
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct StagedExitState {
    pub breakdown_index: Option<usize>, pub support_index: Option<usize>, pub peak_index: Option<usize>,
    pub rebound_seen: bool, pub rebound_peak: Option<Decimal>, pub recovered: bool,
    pub exit_requested: bool, pub reduction_target: Decimal, pub inverse_index: Option<usize>,
}
fn staged_decision(bars: &[RuleCandle], index: usize, state: &StagedExitState, fraction: Decimal, reason: &str) -> Result<RuleDecision, String> {
    let support = bars.get(state.support_index.ok_or("missing support")?).ok_or("support unavailable")?;
    let peak = bars.get(state.peak_index.ok_or("missing peak")?).ok_or("peak unavailable")?;
    let broken = bars.get(state.breakdown_index.ok_or("missing breakdown")?).ok_or("breakdown unavailable")?;
    Ok(RuleDecision::new(reason, index, fraction).with("support_date", &support.timestamp).with("support_low", support.low)
        .with("support_close", support.close).with("decline_high_date", &peak.timestamp).with("decline_high", peak.high)
        .with("breakdown_date", &broken.timestamp).with("breakdown_low", broken.low)
        .with("rebound_threshold", broken.low + (peak.high - broken.low) * ratio(2, 3))
        .with("rebound_basis", "high").with("rebound_peak", state.rebound_peak))
}
fn support_peak(bars: &[RuleCandle], index: usize) -> Option<(usize, usize)> {
    let support = (1..index.saturating_sub(1)).rev().find(|&i| bars[i].low < bars[i - 1].low.min(bars[i + 1].low))?;
    let peak = (support + 1..index).max_by(|&a, &b| bars[a].high.cmp(&bars[b].high).then_with(|| b.cmp(&a)))?;
    Some((support, peak))
}
pub fn observe_staged_exit(bars: &[RuleCandle], index: usize, state: &mut StagedExitState,
    reduction_fraction: Decimal, tiered: bool) -> Result<Option<RuleDecision>, String> {
    let bar = bars.get(index).ok_or("staged exit index unavailable")?;
    if state.breakdown_index.is_none() {
        let Some((support, peak)) = support_peak(bars, index) else { return Ok(None); };
        let target = support_break_reduction(bar.low, bar.close, bars[support].low, bars[support].close)?;
        if target == Decimal::ZERO || !tiered && target != ratio(65, 100) { return Ok(None); }
        if !tiered && bars[index - 1].low < bars[support].low && bars[index - 1].close < bars[support].close { return Ok(None); }
        if (!tiered && bars[peak].high <= bars[support].high) || bars[peak].high <= bar.low { return Ok(None); }
        state.breakdown_index = Some(index); state.support_index = Some(support); state.peak_index = Some(peak);
        let reason = if target == ratio(35, 100) { "support_low_break_reduce" } else { "support_low_close_break_reduce" };
        let mut decision = staged_decision(bars, index, state, if tiered { target } else { reduction_fraction }, reason)?;
        if tiered { state.reduction_target = target; decision.cumulative = true; }
        return Ok(Some(decision));
    }
    let broken = state.breakdown_index.ok_or("missing breakdown")?;
    if state.recovered || state.exit_requested || index <= broken { return Ok(None); }
    let peak = state.peak_index.ok_or("missing peak")?;
    if peak >= bars.len() || broken >= bars.len() { return Err("staged exit history unavailable".into()); }
    state.rebound_peak = Some(state.rebound_peak.unwrap_or(bar.high).max(bar.high));
    let low = bars[broken].low;
    // 比较前交叉相乘，恰好三分之二不会因除法舍入变成严格突破。
    if (state.rebound_peak.unwrap_or(bar.high) - low) * Decimal::from(3) > (bars[peak].high - low) * Decimal::TWO { state.recovered = true; }
    let was_rebounding = state.rebound_seen;
    if bar.high > bars[index - 1].high { state.rebound_seen = true; }
    if !state.recovered && was_rebounding && bar.close < low {
        state.exit_requested = true;
        return Ok(Some(staged_decision(bars, index, state, Decimal::ONE, "weak_rebound_two_thirds_exit")?));
    }
    if tiered && state.reduction_target == ratio(35, 100) {
        let support = bars.get(state.support_index.ok_or("missing support")?).ok_or("support unavailable")?;
        if support_break_reduction(bar.low, bar.close, support.low, support.close)? == ratio(65, 100) {
            state.reduction_target = ratio(65, 100);
            let mut decision = staged_decision(bars, index, state, ratio(65, 100), "support_low_close_break_reduce")?;
            decision.cumulative = true;
            return Ok(Some(decision));
        }
    }
    Ok(None)
}
pub fn observe_intraday_staged_exit(bars: &[RuleCandle], index: usize, state: &mut StagedExitState,
    day_low: Decimal, current_price: Decimal) -> Result<Option<RuleDecision>, String> {
    if index >= bars.len() { return Err("intraday exit index unavailable".into()); }
    if state.recovered || state.exit_requested { return Ok(None); }
    let (support, peak) = match (state.support_index, state.peak_index, state.breakdown_index) {
        (Some(s), Some(p), Some(_)) => (s, p),
        _ => { let Some(pair) = support_peak(bars, index) else { return Ok(None); }; pair }
    };
    if support >= index || peak >= index || bars[peak].high <= day_low { return Ok(None); }
    let target = support_break_reduction(day_low, current_price, bars[support].low, bars[support].close)?;
    if target <= state.reduction_target { return Ok(None); }
    if state.breakdown_index.is_none() { state.breakdown_index = Some(index); state.support_index = Some(support); state.peak_index = Some(peak); }
    state.reduction_target = target;
    let mut decision = staged_decision(bars, index, state, target,
        if target == ratio(65, 100) { "support_low_close_break_reduce" } else { "support_low_break_reduce" })?
        .with("breakdown_low", day_low).with("rebound_threshold", day_low + (bars[peak].high - day_low) * ratio(2, 3));
    decision.cumulative = true;
    Ok(Some(decision))
}
fn consistent(bars: &[RuleCandle]) -> bool {
    bars.first().is_some_and(|first| first.adjustment_factor > Decimal::ZERO && bars.iter().all(|b|
        b.symbol == first.symbol && b.adjustment_factor == first.adjustment_factor && b.raw_trading_active != Some(false)))
        && bars.windows(2).all(|p| p[0].timestamp < p[1].timestamp)
}
pub fn observe_double_volume_lower_close_exit(bars: &[RuleCandle], index: usize) -> Option<RuleDecision> {
    if index < 2 || index >= bars.len() { return None; }
    let (reference, previous, current) = (&bars[index - 2], &bars[index - 1], &bars[index]);
    if !consistent(&bars[index - 2..=index]) || reference.volume <= Decimal::ZERO || previous.volume <= Decimal::ZERO
        || current.volume <= previous.volume || previous.volume < Decimal::TWO * reference.volume || current.close >= previous.close { return None; }
    Some(RuleDecision::new("volume_after_double_lower_close_clear", index, Decimal::ONE)
        .with("double_volume_reference", reference.volume).with("previous_volume", previous.volume)
        .with("previous_close", previous.close).with("observed_close", current.close).with("observed_volume", current.volume))
}
pub fn observe_gap_bearish_exit(bars: &[RuleCandle], index: usize) -> Option<RuleDecision> {
    if index < 1 || index >= bars.len() { return None; }
    let (bar, previous) = (&bars[index], &bars[index - 1]);
    if !consistent(&bars[index - 1..=index]) || bar.open >= previous.close || !medium_bearish_body(bar)
        || bar.low <= Decimal::ZERO || bar.high < bar.open || bar.low > bar.close { return None; }
    let last_bearish = bars[..index].iter().rev().find(|b| b.symbol == bar.symbol && b.close < b.open);
    if !is_volume_bearish(bar, Some(previous), last_bearish) { return None; }
    let reference = if previous.volume > Decimal::ZERO && bar.volume > previous.volume { previous } else { last_bearish? };
    Some(RuleDecision::new("volume_gap_medium_bearish_clear", index, Decimal::ONE)
        .with("volume_reference", reference.volume).with("volume_multiple", bar.volume / reference.volume)
        .with("observed_close", bar.close).with("bearish_body_fraction", (bar.open - bar.close) / bar.open))
}
pub fn volume_shadow_next_decline(bars: &[RuleCandle], index: usize, observed: Option<&RuleCandle>) -> Option<RuleDecision> {
    if index < 2 || index >= bars.len() { return None; }
    let bar = observed.unwrap_or(&bars[index]);
    if bars[index].symbol != bars[index - 1].symbol || bars[index].timestamp <= bars[index - 1].timestamp
        || bars[index].open >= bars[index - 1].close
        || volume_upper_shadow_rejection(&bars[index - 1], Some(&bars[index - 2])).is_none()
        || bar.symbol != bars[index].symbol || bar.timestamp != bars[index].timestamp || bar.low >= bars[index].open { return None; }
    Some(RuleDecision::new("volume_upper_shadow_next_open_decline_clear", index, Decimal::ONE)
        .with("warning_index", index - 1).with("warning_close", bars[index - 1].close)
        .with("observed_open", bars[index].open).with("observed_low", bar.low).with("observed_close", bar.close))
}
pub fn observe_double_volume_fade(bars: &[RuleCandle], index: usize) -> Option<RuleDecision> {
    if index < 4 || index >= bars.len() { return None; }
    let (reference, bullish, current) = (&bars[index - 2], &bars[index - 1], &bars[index]);
    if !consistent(&bars[index - 2..=index]) || reference.volume <= Decimal::ZERO
        || bullish.close <= bullish.open || current.close >= current.open || bullish.volume < Decimal::TWO * reference.volume { return None; }
    let clear = bullish.close > reference.close && bullish.volume >= Decimal::from(3) * reference.volume && current.volume >= Decimal::TWO * bullish.volume;
    if !clear && current.close >= bullish.close { return None; }
    let mut later_high = reference.high;
    let mut key = None;
    for i in (1..index - 2).rev() {
        if !consistent(&bars[i - 1..=i + 1]) || bars[i].adjustment_factor != bullish.adjustment_factor { return None; }
        if bars[i].high >= bars[i - 1].high && bars[i].high > later_high { key = Some(i); break; }
        later_high = later_high.max(bars[i].high);
    }
    let key = key?;
    if bullish.high <= bars[key].high { return None; }
    let mut decision = RuleDecision::new(if clear { "triple_volume_bullish_breakout_next_double_volume_bearish_clear" }
        else { "double_volume_bullish_breakout_next_bearish_lower_close_reduce_90" }, index, if clear { Decimal::ONE } else { ratio(9, 10) })
        .with("bullish_index", index - 1).with("breakout_key_index", key).with("breakout_key_high", bars[key].high)
        .with("bullish_volume", bullish.volume).with("observed_volume", current.volume).with("warning_close", current.close);
    decision.cumulative = !clear;
    Some(decision)
}
pub fn is_sealed_limit_up(previous: &RuleCandle, current: &RuleCandle) -> bool {
    if current.raw_is_st == Some(true) || current.raw_trading_active == Some(false)
        || current.adjustment_factor <= Decimal::ZERO || current.adjustment_factor != previous.adjustment_factor
        || current.close <= previous.close || current.close < current.open || current.symbol != previous.symbol
        || current.timestamp <= previous.timestamp || current.volume <= Decimal::ZERO || current.high != current.close { return false; }
    let s = current.symbol.as_str();
    if s.len() != 9 || !s.is_ascii() || !s[3..].bytes().all(|b| b.is_ascii_digit()) { return false; }
    let rate = if s.starts_with("sh.60") || s.starts_with("sz.00") { ratio(1, 10) }
        else if s.starts_with("sz.30") { if current.timestamp.as_str() >= "2020-08-24" { ratio(1, 5) } else { ratio(1, 10) } }
        else if s.starts_with("sh.68") { ratio(1, 5) }
        else if ["bj.43", "bj.82", "bj.83", "bj.87", "bj.88", "bj.92"].iter().any(|p| s.starts_with(p)) { ratio(3, 10) }
        else { return false; };
    let round = |v: Decimal| v.round_dp_with_strategy(2, RoundingStrategy::MidpointAwayFromZero);
    round(current.close / current.adjustment_factor) == round(round(previous.close / current.adjustment_factor) * (Decimal::ONE + rate))
}
pub fn observe_limit_up_distribution_exit(bars: &[RuleCandle], index: usize) -> Option<RuleDecision> {
    if index < 4 || index >= bars.len() { return None; }
    let (current, previous) = (&bars[index], &bars[index - 1]);
    let body = previous.close - previous.open;
    let midpoint = (previous.open + previous.close) / Decimal::TWO;
    let ceiling = midpoint + body * ratio(2, 100);
    if body <= Decimal::ZERO || current.close >= current.open || current.open >= previous.close
        || current.open > ceiling || current.close >= midpoint { return None; }
    let start = index.saturating_sub(20);
    if !consistent(&bars[start..=index]) { return None; }
    let quiet_end = ((start + 1).max(1) + 1..index - 1).rev().find(|&j| {
        bars[j - 1].volume < bars[j - 2].volume && bars[j].volume < bars[j - 1].volume
            && is_sealed_limit_up(&bars[j - 2], &bars[j - 1]) && is_sealed_limit_up(&bars[j - 1], &bars[j])
    })?;
    let baseline = (bars[quiet_end - 1].volume + bars[quiet_end].volume) / Decimal::TWO;
    if previous.close <= bars[index - 2].close || previous.volume < baseline * Decimal::TWO
        || bars[quiet_end + 1..index].iter().any(|b| b.low < bars[quiet_end - 1].low.min(bars[quiet_end].low)) { return None; }
    let origin = bars[start..quiet_end].iter().min_by_key(|b| b.low)?;
    if origin.low <= Decimal::ZERO || previous.close < origin.low * ratio(13, 10) { return None; }
    Some(RuleDecision::new("limit_up_distribution_midpoint_clear", index, Decimal::ONE)
        .with("distribution_quiet_dates", [&bars[quiet_end - 1].timestamp, &bars[quiet_end].timestamp])
        .with("distribution_volume_mean", baseline).with("distribution_body_midpoint", midpoint)
        .with("distribution_open_ceiling", ceiling).with("distribution_rally_gain", previous.close / origin.low - Decimal::ONE))
}

pub fn observe_inverse_resistance_exit(bars:&[RuleCandle],index:usize,state:&mut StagedExitState)->Option<RuleDecision> {
    let start=state.inverse_index?;
    if index<=start||index>=bars.len(){return None;}
    let defense=if start>0{bars[start].high.max(bars[start-1].close)}else{bars[start].high};
    if bars[index].high>defense{state.inverse_index=None;return None;}
    if index<start+2{return None;}
    let (prior,previous)=(&bars[index-1],&bars[index-2]);
    let shadow=prior.open.min(prior.close)-prior.low;
    let resistance=(prior.open<previous.close&&prior.close>prior.open)||prior.open>previous.close
        ||(prior.high>prior.low&&shadow*Decimal::TWO>=prior.high-prior.low);
    let virtual_low=prior.low.min(previous.close);
    if !resistance||bars[index].close>=virtual_low{return None;}
    Some(RuleDecision::new("inverse_n_bull_resistance_failed_exit",index,Decimal::ONE)
        .with("inverse_n_index",start).with("resistance_index",index-1).with("resistance_virtual_low",virtual_low)
        .with("resistance_close",prior.close).with("failure_close",bars[index].close).with("inverse_defense",defense))
}

#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct DoubleVolumeBullishShadowExit {
    index:Option<usize>,previous:Option<RuleCandle>,last_bearish:Option<RuleCandle>,
    warning:Option<RuleCandle>,warning_baseline:Option<RuleCandle>,decision:Option<RuleDecision>,
}
impl DoubleVolumeBullishShadowExit {
    pub fn advance(&mut self,bars:&[RuleCandle],index:usize)->Option<RuleDecision>{
        if index>=bars.len(){return None;}
        if self.index.is_some_and(|i|index<i){*self=Self::default();}
        for current in self.index.map_or(0,|i|i+1)..=index {
            self.observe(&bars[current],current);self.index=Some(current);
        }
        self.decision.clone()
    }
    fn observe(&mut self,bar:&RuleCandle,index:usize){
        self.decision=None;
        if bar.low<=Decimal::ZERO||bar.low>bar.open.min(bar.close)||bar.high<bar.open.max(bar.close)
            ||bar.volume<Decimal::ZERO||bar.raw_trading_active==Some(false){
            self.previous=None;self.last_bearish=None;self.warning=None;self.warning_baseline=None;return;
        }
        let previous=self.previous.clone().filter(|p|p.symbol==bar.symbol&&p.timestamp<bar.timestamp&&p.adjustment_factor==bar.adjustment_factor);
        if self.previous.is_some()&&previous.is_none(){self.last_bearish=None;self.warning=None;self.warning_baseline=None;}
        if let Some(previous)=previous {
            if let (Some(warning),Some(baseline))=(&self.warning,&self.warning_baseline){
                if bar.close<warning.close&&is_volume_bearish(bar,Some(&previous),self.last_bearish.as_ref()){
                    let reference=if previous.volume>Decimal::ZERO&&bar.volume>previous.volume{Some(&previous)}else{self.last_bearish.as_ref()};
                    if let Some(reference)=reference {
                        self.decision=Some(RuleDecision::new("volume_bearish_below_double_bullish_shadow_close_clear",index,Decimal::ONE)
                            .with("warning_date",&warning.timestamp).with("warning_open",warning.open).with("warning_high",warning.high)
                            .with("warning_low",warning.low).with("warning_close",warning.close).with("warning_volume",warning.volume)
                            .with("warning_baseline_volume",baseline.volume).with("warning_volume_multiple",warning.volume/baseline.volume)
                            .with("warning_upper_shadow",warning.high-warning.close).with("warning_body",warning.close-warning.open)
                            .with("volume_reference",reference.volume).with("volume_multiple",bar.volume/reference.volume)
                            .with("observed_open",bar.open).with("observed_close",bar.close).with("observed_volume",bar.volume));
                    }
                    self.warning=None;self.warning_baseline=None;
                }
            }
            if bar.close>bar.open&&previous.volume>Decimal::ZERO&&bar.high-bar.close>bar.close-bar.open&&bar.volume>=Decimal::TWO*previous.volume {
                self.warning=Some(bar.clone());self.warning_baseline=Some(previous);
            }
        }
        if bar.close<bar.open{self.last_bearish=Some(bar.clone());}
        self.previous=Some(bar.clone());
    }
}
