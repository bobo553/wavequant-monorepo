//! Python candle_strength、volume_candle、attack_quality、gap_entry_gate、two_t_entry_gate。
use super::{RuleCandle, RuleDecision, ratio, teaching_inside};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

fn ordered(previous: &RuleCandle, bar: &RuleCandle) -> bool {
    previous.symbol == bar.symbol && previous.timestamp < bar.timestamp
}
pub fn medium_bullish_body(bar: &RuleCandle) -> bool {
    let body = bar.close - bar.open;
    body > Decimal::ZERO && body * Decimal::from(50) >= bar.open && body * Decimal::TWO >= bar.high - bar.low
}
pub fn medium_bearish_body(bar: &RuleCandle) -> bool {
    let body = bar.open - bar.close;
    body > Decimal::ZERO && body * Decimal::from(50) >= bar.open && body * Decimal::TWO >= bar.high - bar.low
}
pub fn strong_bullish_candle(bar: &RuleCandle) -> bool {
    let body = bar.close - bar.open;
    let span = bar.high - bar.low;
    bar.open > Decimal::ZERO && span > Decimal::ZERO && body > Decimal::ZERO
        && body >= bar.open * ratio(3, 100) && body >= span * ratio(3, 5)
        && bar.high - bar.close <= span / Decimal::from(5)
}
pub fn is_volume_bearish(bar: &RuleCandle, previous: Option<&RuleCandle>, last_bearish: Option<&RuleCandle>) -> bool {
    bar.close < bar.open && [previous, last_bearish.filter(|b| b.close < b.open)].into_iter().flatten()
        .any(|reference| ordered(reference, bar) && reference.volume > Decimal::ZERO && bar.volume > reference.volume)
}
pub fn is_bearish_inside_child(mother: &RuleCandle, child: &RuleCandle) -> bool {
    ordered(mother, child) && mother.close != mother.open && child.close < child.open && teaching_inside(mother, child)
}
pub fn bearish_body_engulfs(bar: &RuleCandle, previous: &RuleCandle) -> bool {
    ordered(previous, bar) && bar.close < bar.open && previous.open != previous.close
        && bar.open >= previous.open.max(previous.close) && bar.close <= previous.open.min(previous.close)
}
pub fn volume_bearish_buy_rejection(bar: &RuleCandle, previous: Option<&RuleCandle>, last_bearish: Option<&RuleCandle>) -> Option<&'static str> {
    if is_volume_bearish(bar, previous, last_bearish) {
        return Some(if previous.is_some_and(|p| bearish_body_engulfs(bar, p)) {
            "volume_bearish_body_engulfing_buy_blocked"
        } else { "volume_bearish_buy_blocked" });
    }
    if previous.is_some_and(|p| ordered(p, bar) && bar.open > p.high && bar.close < bar.open) {
        return Some("gap_open_bearish_buy_blocked");
    }
    None
}
pub fn volume_bearish_entry_rejection(bars: &[RuleCandle], index: usize) -> Option<&'static str> {
    let bar = bars.get(index)?;
    if index == 0 || bar.close >= bar.open { return None; }
    let previous = &bars[index - 1];
    let bearish = bars[..index].iter().rev().find(|p| p.symbol == bar.symbol && p.close < p.open);
    volume_bearish_buy_rejection(bar, Some(previous), bearish)
}
pub fn is_long_upper_shadow(bar: &RuleCandle) -> bool {
    let upper = bar.high - bar.open.max(bar.close);
    bar.high > bar.low && Decimal::TWO * upper >= bar.high - bar.low && upper >= (bar.close - bar.open).abs()
}
pub fn volume_upper_shadow_rejection(bar: &RuleCandle, previous: Option<&RuleCandle>) -> Option<&'static str> {
    previous.filter(|p| ordered(p, bar) && p.volume > Decimal::ZERO && bar.volume > p.volume && is_long_upper_shadow(bar))
        .map(|_| "volume_long_upper_shadow_buy_blocked")
}
pub fn bullish_breakout_rejection(bar: &RuleCandle, previous: Option<&RuleCandle>, breakout_level: Option<Decimal>) -> Option<&'static str> {
    let Some(previous) = previous.filter(|p| p.timestamp < bar.timestamp) else { return Some("breakout_previous_price_unavailable"); };
    if let Some(reason) = volume_bearish_buy_rejection(bar, Some(previous), None) { return Some(reason); }
    if bar.open < previous.close {
        let resistance = previous.high.max(breakout_level.unwrap_or(previous.high));
        if previous.volume > Decimal::ZERO && bar.volume >= Decimal::TWO * previous.volume
            && medium_bullish_body(bar) && bar.close > resistance { return None; }
        return Some("breakout_low_open");
    }
    if bar.open > previous.high || medium_bullish_body(bar) { None }
    else { Some("breakout_requires_gap_or_medium_bullish_body") }
}
pub fn volume_high_open_rising(bar: &RuleCandle, previous: &RuleCandle) -> bool {
    ordered(previous, bar) && previous.volume > Decimal::ZERO && bar.volume > previous.volume
        && bar.open > previous.close && bar.low >= bar.open && strong_bullish_candle(bar)
}
pub fn volume_gap_follow_through(bar: &RuleCandle, previous: &RuleCandle) -> bool {
    ordered(previous, bar) && previous.volume > Decimal::ZERO && bar.volume > previous.volume
        && bar.open > previous.high && bar.low > previous.high && bar.close >= bar.open
        && bar.high > bar.low && (bar.high - bar.close) * Decimal::from(5) <= bar.high - bar.low
}
pub fn double_volume_bullish_breakout(bar: &RuleCandle, previous: &RuleCandle) -> bool {
    ordered(previous, bar) && previous.volume > Decimal::ZERO && medium_bullish_body(bar)
        && bar.volume >= Decimal::TWO * previous.volume && bar.high > bar.low
        && (bar.high - bar.close) * Decimal::from(5) <= bar.high - bar.low && bar.close > previous.high
}
pub fn squeeze_volume_pass(bar: &RuleCandle, previous: Option<&RuleCandle>, before_attack: Option<&RuleCandle>) -> bool {
    [previous, before_attack].into_iter().flatten()
        .any(|p| p.timestamp < bar.timestamp && p.volume > Decimal::ZERO && bar.volume > p.volume)
}
pub fn squeeze_bullish_body(bar: &RuleCandle, previous: Option<&RuleCandle>) -> bool {
    if medium_bullish_body(bar) { return true; }
    let body = bar.close - bar.open;
    previous.is_some_and(|p| ordered(p, bar) && p.volume > Decimal::ZERO && bar.volume >= Decimal::TWO * p.volume)
        && bar.open > Decimal::ZERO && bar.high > bar.low && body > Decimal::ZERO
        && body * Decimal::from(50) >= bar.open && body >= (bar.high - bar.low) * ratio(45, 100)
}
pub fn squeeze_buy_rejection(bar: &RuleCandle, previous: Option<&RuleCandle>, before_attack: Option<&RuleCandle>) -> Option<&'static str> {
    if !squeeze_volume_pass(bar, previous, before_attack) { return Some("squeeze_buy_volume_not_above_previous_or_pre_n"); }
    let Some(previous) = previous else { return Some("squeeze_buy_previous_price_unavailable"); };
    let mut rejection = bullish_breakout_rejection(bar, Some(previous), None);
    if matches!(rejection, Some("breakout_low_open" | "breakout_requires_gap_or_medium_bullish_body"))
        && squeeze_bullish_body(bar, Some(previous)) && bar.close > previous.high { rejection = None; }
    if rejection.is_some() { return rejection; }
    let body = bar.close - bar.open;
    if bar.open > previous.high || previous.close > Decimal::ZERO && bar.close > previous.close * ratio(102, 100)
        && body > Decimal::ZERO && body >= bar.high - bar.close { None }
    else { Some("squeeze_buy_requires_gain_body_or_gap") }
}
pub fn v3_positive_n_attack_rejection(bar: &RuleCandle, increased_from_previous: Option<bool>) -> Option<&'static str> {
    if increased_from_previous != Some(true) { Some("v3_attack_volume_not_above_previous") }
    else if bar.close <= bar.open { Some("v3_attack_not_bullish") }
    else if !medium_bullish_body(bar) { Some("v3_attack_body_too_small") }
    else { None }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Gap { index: usize, lower: Decimal, upper: Decimal }
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct GapEntryGate {
    pub index: Option<usize>, gaps: Vec<Gap>, pub restrictions: BTreeMap<usize, RuleDecision>,
}
impl GapEntryGate {
    pub fn advance(&mut self, bars: &[RuleCandle], index: usize) -> Result<Option<RuleDecision>, String> {
        if index >= bars.len() { return Err("gap entry index unavailable".into()); }
        if self.index.is_some_and(|last| index <= last) { return Ok(self.restrictions.get(&index).cloned()); }
        for day in self.index.map_or(0, |i| i + 1)..=index {
            if day == 0 { continue; }
            let (bar, previous) = (&bars[day], &bars[day - 1]);
            if !ordered(previous, bar) { return Err("gap gate requires ordered single-symbol bars".into()); }
            while self.gaps.last().is_some_and(|g| bar.open >= g.upper) { self.gaps.pop(); }
            if let Some(gap) = self.gaps.last() {
                let width = bar.open - previous.high;
                if bar.open > previous.high && gap.lower <= bar.open && bar.open < gap.upper && width < gap.upper - gap.lower {
                    self.restrictions.insert(day, RuleDecision::new("up_gap_smaller_than_adjacent_down_gap", day, Decimal::ZERO)
                        .with("down_gap_index", gap.index).with("down_gap_lower", gap.lower).with("down_gap_upper", gap.upper)
                        .with("down_gap_width", gap.upper - gap.lower).with("up_gap_width", width).with("observed_open", bar.open)
                        .with("previous_high", previous.high));
                }
            }
            while self.gaps.last().is_some_and(|g| bar.high >= g.upper) { self.gaps.pop(); }
            if bar.open < previous.low && bar.close < bar.open && bar.high < previous.low {
                self.gaps.push(Gap { index: day, lower: bar.open, upper: previous.low });
            }
        }
        self.index = Some(index);
        Ok(self.restrictions.get(&index).cloned())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TwoTEntryTarget { pub attack: usize, pub known_at: usize, pub one_p: Decimal, pub two_t: Decimal, pub source_id: Option<String> }
pub fn entry_targets_from_events(events:&[crate::indexed_json::Value],index:usize,at_close:bool)->Vec<TwoTEntryTarget> {
    use super::qualification::{index as idx,price};
    let Some(cutoff)= (if at_close {Some(index)}else{index.checked_sub(1)}) else{return vec![];};
    let mut sources=vec![];let mut retired_ids=std::collections::BTreeSet::new();let mut retired_attacks=std::collections::BTreeSet::new();
    for event in events {
        let Some(day)=idx(event,"bar_index") else{continue;};let known=idx(event,"known_at").unwrap_or(day);
        if day.max(known)>cutoff {continue;}
        let kind=event.get("event").and_then(crate::indexed_json::Value::as_str).unwrap_or("");
        let identity=event.get("n_id").and_then(crate::indexed_json::Value::as_str);
        if matches!(kind,"n_target_source_retired"|"n_invalidated") {
            if kind=="n_target_source_retired" && event.get("reason").and_then(crate::indexed_json::Value::as_str)==Some("new_confirmed_decline"){continue;}
            if let Some(id)=identity {retired_ids.insert(id.to_owned());}else if let Some(attack)=idx(event,"attack"){retired_attacks.insert(attack);}
        } else if kind=="n_completed" && event.get("direction").and_then(crate::indexed_json::Value::as_str)==Some("up")
            && event.get("target_eligible").and_then(crate::indexed_json::Value::as_bool)==Some(true)
            && event.get("target_primary").and_then(crate::indexed_json::Value::as_bool)!=Some(false) {
            let (Some(one_p),Some(two_t))=(price(event,"one_p"),price(event,"two_t"))else{continue;};
            let available=day.max(known).max(idx(event,"window_start").unwrap_or(0));
            if available<=cutoff {sources.push(TwoTEntryTarget{attack:day,known_at:available,one_p,two_t,source_id:identity.map(str::to_string)});}
        }
    }
    sources.retain(|v|!v.source_id.as_ref().is_some_and(|id|retired_ids.contains(id)) && !retired_attacks.contains(&v.attack));
    sources.sort_by_key(|v|std::cmp::Reverse((v.known_at,v.attack)));sources
}
pub fn two_t_entry_rejection(bars: &[RuleCandle], index: usize, sources: &[TwoTEntryTarget],
    observed_high: Decimal, observed_close: Decimal, at_close: bool) -> Option<RuleDecision> {
    for source in sources {
        if source.attack > source.known_at || source.known_at > index || index >= bars.len()
            || source.one_p <= Decimal::ZERO || source.one_p >= source.two_t || !at_close && source.known_at == index { continue; }
        let observed = if source.known_at == index { observed_close } else { observed_high };
        if observed < source.two_t || (source.known_at..index).any(|i| {
            (if i == source.known_at { bars[i].close } else { bars[i].high }) >= source.two_t
        }) { continue; }
        return Some(RuleDecision::new("positive_n_two_t_reached_no_entry", index, Decimal::ZERO)
            .with("target_source_attack", source.attack).with("target_source_id", &source.source_id)
            .with("target_known_at", source.known_at).with("one_p", source.one_p).with("two_t", source.two_t)
            .with("target_observed_price", observed).with("observed_high", observed_high).with("observed_close", observed_close)
            .with("target_observation_basis", if source.known_at == index { "first_known_close" } else { "observed_session_high" }));
    }
    None
}

/// 资格、N 及渠道由调用方先确定；此函数仅返回所有渠道共用的独立量价禁买原因。
pub fn common_entry_rejection(bars: &[RuleCandle], index: usize) -> Option<&'static str> {
    let bar = bars.get(index)?;
    volume_bearish_entry_rejection(bars, index).or_else(|| volume_upper_shadow_rejection(bar, index.checked_sub(1).and_then(|i| bars.get(i))))
}

pub fn inverse_n_low_entry_risk(bars:&[RuleCandle],now:usize,points:&[super::super::kernel::KernelPoint])->Option<RuleDecision> {
    use super::super::kernel::PointKind;
    if now==0 || now>=bars.len() || points.len()<3 {return None;}
    let [a,b,c]=[&points[points.len()-3],&points[points.len()-2],&points[points.len()-1]];
    let (ap,bp,cp)=(a.value.to_string().parse::<Decimal>().ok()?,b.value.to_string().parse::<Decimal>().ok()?,c.value.to_string().parse::<Decimal>().ok()?);
    let (bar,previous)=(&bars[now],&bars[now-1]);
    if bar.low>=previous.low || (a.kind,b.kind,c.kind)!=(PointKind::H,PointKind::L,PointKind::H)
        || !(a.index<b.index && b.index<c.index && c.index<now && a.known_index<=b.known_index && b.known_index<=c.known_index && c.known_index<=now)
        || !(ap>cp && cp>bp && ap==bars[a.index].high && bp==bars[b.index].low && cp==bars[c.index].high && bar.low<bp && bp<=previous.low) {return None;}
    if bars[a.index+1..=now].iter().any(|v|v.high>=ap)
        || bars[a.index+1..=c.index].iter().map(|v|v.low).min()!=Some(bp)
        || bars[b.index+1..now].iter().any(|v|v.low<bp)
        || bars[b.index+1..now].iter().map(|v|v.high).max()!=Some(cp) {return None;}
    Some(RuleDecision::new("inverse_n_new_low_requires_observation",now,Decimal::ZERO)
        .with("inverse_origin_high",ap).with("inverse_neckline_low",bp).with("inverse_neckline_close",bars[b.index].close)
        .with("inverse_rebound_high",cp).with("inverse_rebound_known_index",c.known_index)
        .with("inverse_observed_low",bar.low).with("inverse_close_break",bar.close<bars[b.index].close))
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct PolylineBuyGate {
    pub index:Option<usize>,epoch:Option<usize>,previous_key:Option<super::super::kernel::KernelPoint>,
    previous_anchor:Option<super::super::kernel::KernelPoint>,pending:Option<(super::super::kernel::KernelPoint,usize)>,
    released:std::collections::BTreeSet<(usize,usize,Decimal,usize)>,
    pub restrictions:BTreeMap<usize,RuleDecision>,pub breakouts:BTreeMap<usize,RuleDecision>,pub source_epochs:BTreeMap<usize,usize>,
}
impl PolylineBuyGate {
    pub fn advance(&mut self,bars:&[RuleCandle],index:usize,frame:&super::super::kernel::KernelFrame)->Result<(),String> {
        use super::super::kernel::Direction;
        if self.index==Some(index){return Ok(());}
        if index!=self.index.map_or(0,|i|i+1) || index>=bars.len() || frame.level!=0 {return Err("polyline gate requires consecutive daily level-zero frames".into());}
        let decimal=|p:&super::super::kernel::KernelPoint|p.value.to_string().parse::<Decimal>().map_err(|e|e.to_string());
        let bar=&bars[index];let previous=index.checked_sub(1).and_then(|i|bars.get(i));
        let epoch=frame.points.first().map_or(0,|p|p.path);
        if self.epoch!=Some(epoch){self.released.clear();self.pending=None;self.previous_key=None;self.previous_anchor=None;self.epoch=Some(epoch);}
        self.index=Some(index);self.source_epochs.insert(index,epoch);
        if self.pending.as_ref().is_some_and(|(p,_)|!frame.points.iter().any(|v|v.is_closed()&&v.index==p.index&&v.ordinal==p.ordinal&&v.value==p.value)){self.pending=None;}
        if let Some(key)=&self.previous_key {
            if frame.structure_direction==Some(Direction::Up){
                let known=key.known_index.max(self.previous_anchor.as_ref().map_or(key.known_index,|p|p.known_index));
                if !self.released.contains(&(key.index,key.ordinal,decimal(key)?,known))
                    && self.pending.as_ref().is_none_or(|(p,_)|key.value>p.value){self.pending=Some((key.clone(),known));}
            }
        }
        let current_key=if frame.structure_direction==Some(Direction::Down){frame.key.clone()}else{None};
        let current_anchor=frame.anchor.clone();
        if self.pending.as_ref().is_some_and(|(p,_)|current_key.as_ref().is_some_and(|key|key.order()>p.order())){self.pending=None;}
        let mut candidates=Vec::new();
        if let Some(value)=&self.pending{candidates.push(value.clone());}
        if let Some(key)=&current_key{candidates.push((key.clone(),key.known_index.max(current_anchor.as_ref().map_or(key.known_index,|p|p.known_index))));}
        self.previous_key=current_key;self.previous_anchor=current_anchor;
        let mut retained=Vec::new();
        for (point,known) in candidates {if !self.released.contains(&(point.index,point.ordinal,decimal(&point)?,known)){retained.push((point,known));}}
        let Some((key,known))=retained.iter().max_by(|a,b|a.0.value.total_cmp(&b.0.value).then(a.1.cmp(&b.1))) else{return Ok(());};
        let key_price=decimal(key)?;
        let rejection=if *known<index && bar.high>key_price {
            match bullish_breakout_rejection(bar,previous,Some(key_price)) {
                None=>{
                    for (point,clock) in &retained{let value=decimal(point)?;if *clock<index&&bar.high>value{self.released.insert((point.index,point.ordinal,value,*clock));}}
                    self.pending=None;self.breakouts.insert(index,RuleDecision::new("polyline_strong_last_fall_high_breakout",index,Decimal::ZERO)
                        .with("polyline_key_index",key.index).with("polyline_key_price",key_price).with("polyline_key_known_index",known));return Ok(());
                },Some(reason)=>reason,
            }
        }else{"polyline_last_fall_high_not_broken"};
        self.restrictions.insert(index,RuleDecision::new(rejection,index,Decimal::ZERO).with("polyline_key_index",key.index)
            .with("polyline_key_price",key_price).with("polyline_key_known_index",known).with("observed_high",bar.high));Ok(())
    }
}

pub fn qualified_close_entry(bars:&[RuleCandle],now:usize,eligibility:&super::qualification::TradingEligibility,
    gate:&PolylineBuyGate,events:&[crate::indexed_json::Value])->Option<RuleDecision> {
    if now==0||now>=bars.len()||gate.index!=Some(now)||gate.restrictions.contains_key(&now){return None;}
    let (bar,previous)=(&bars[now],&bars[now-1]);
    let following=gate.breakouts.contains_key(&(now-1))&&gate.source_epochs.get(&now)==gate.source_epochs.get(&(now-1))&&volume_gap_follow_through(bar,previous);
    let breakout=double_volume_bullish_breakout(bar,previous);
    if bar.open>previous.high&&!gate.breakouts.contains_key(&now)&&!following&&!breakout{return None;}
    if !breakout&&!following&&!volume_high_open_rising(bar,previous){return None;}
    let window=eligibility.intervals.iter().filter(|w|w.trend_level==eligibility.level&&w.start_index<=now&&w.end_index.is_none_or(|end|now<end))
        .max_by_key(|w|w.start_index)?;
    let stop=window.start_low_price?;
    let confirmation=events.iter().rev().find(|e|e.get("event").and_then(crate::indexed_json::Value::as_str)==Some("trend_lifecycle_state")
        &&e.get("state").and_then(crate::indexed_json::Value::as_str)==Some("空多交替")
        &&super::qualification::index(e,"trend_level")==Some(usize::from(eligibility.level))
        &&super::qualification::index(e,"bar_index")==Some(window.start_index)
        &&e.get("source_path").and_then(crate::indexed_json::Value::as_str)==Some(window.source_path.as_str()))?;
    if one_p_breakout_entry_rejection(events,now,bar.high).is_some()
        || two_t_entry_rejection(bars,now,&entry_targets_from_events(events,now,true),bar.high,bar.close,true).is_some(){return None;}
    let reason=if following{"qualified_volume_gap_follow_through_close"}else if breakout{"qualified_double_volume_breakout_close"}else{"qualified_volume_high_open_rising_close"};
    Some(RuleDecision::new(reason,now,Decimal::ONE).with("price",bar.close).with("stop",stop).with("qualification_index",window.start_index)
        .with("trigger_index",now).with("trigger_timestamp",format!("{}T15:00:00+08:00",super::secondary::session_date(bar)))
        .with("counter_ratio",Decimal::ZERO).with("rvol",if breakout&&previous.volume>Decimal::ZERO{Some(bar.volume/previous.volume)}else{None})
        .with("regime",if following{"突破次日放量跳空守稳"}else if breakout{"倍量中大阳高位收盘突破"}else{"放量高开高走"})
        .with("qualification_breakout_level",confirmation.get("breakout_level")).with("execution_model","same_day_close"))
}

pub fn one_p_breakout_entry_rejection(events:&[crate::indexed_json::Value],now:usize,high:Decimal)->Option<RuleDecision> {
    use super::qualification::{index,price};
    for e in events {
        if e.get("event").and_then(crate::indexed_json::Value::as_str)!=Some("one_p_breakout_watch")||index(e,"bar_index")!=Some(now){continue;}
        let (Some(reached),Some(key_known),Some(source_known),Some(breakout),Some(key))=(index(e,"one_p_reached_index"),index(e,"key_known_index"),
            index(e,"one_p_known_index"),index(e,"breakout_index"),price(e,"key_price"))else{continue;};
        if index(e,"known_at").unwrap_or(now)>now||reached>=breakout||source_known>reached||key_known>=breakout
            ||breakout>now||now-breakout>2||key<=Decimal::ZERO{continue;}
        let prior=e.get("prior_breakout").and_then(crate::indexed_json::Value::as_bool)==Some(true);
        if (prior&&breakout>=now)||(!prior&&(breakout!=now||high<=key)){continue;}
        return Some(RuleDecision::new(e.get("reason").and_then(crate::indexed_json::Value::as_str).unwrap_or("one_p_breakout_requires_observation"),now,Decimal::ZERO)
            .with("source_event",e).with("observed_high",high));
    }None
}
