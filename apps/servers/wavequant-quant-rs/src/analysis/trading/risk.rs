//! 收盘风险观察器消费真实的行情前缀与统一结构结果；持仓归属和实际成交由执行层管理。
use super::{RuleCandle, RuleDecision, ratio, teaching_inside, teaching_outside};
use super::entries::is_volume_bearish;
use super::exits::{self, DoubleVolumeBullishShadowExit, StagedExitState};
use super::super::kernel::{KernelFrame, PointKind};
use rust_decimal::{Decimal, RoundingStrategy};
use serde::{Deserialize, Serialize};
use crate::indexed_json::Value;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RiskConfig {
    pub pressure_lookback: usize, pub pressure_body_min_fraction: Decimal, pub pressure_volume_ratio: Decimal,
    pub wave_exhaustion_reduction: Decimal, pub wave_exhaustion_min_range: Decimal,
    pub wave_exhaustion_min_shadow: Decimal, pub wave_engulf_min_body: Decimal,
}
impl Default for RiskConfig {
    fn default() -> Self { Self { pressure_lookback: 120, pressure_body_min_fraction: ratio(5,100), pressure_volume_ratio: Decimal::TWO,
        wave_exhaustion_reduction: ratio(8,10), wave_exhaustion_min_range: ratio(8,100),
        wave_exhaustion_min_shadow: ratio(2,10), wave_engulf_min_body: ratio(5,100) } }
}
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
struct PressureState {
    supplies: Vec<usize>, armed: Option<(usize,usize)>, breakout: Option<(usize,usize,usize)>,
    massive_candidate: Option<(usize,usize,Decimal)>, massive_warning: Option<(usize,RuleDecision)>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct KnownHigh { level: u8, index: usize, price: Decimal, known: usize }
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
struct BreakoutState {
    candidate: Option<(usize,KnownHigh)>, child_candidate: Option<(usize,KnownHigh)>, warning: Option<(usize,RuleDecision)>,
}
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct VolumeRiskState {
    pub pattern_warning: Option<usize>, pub child_mother_warning: Option<usize>, pub mother_child_warning: Option<usize>,
    pub child_warning: Option<usize>, pub trigger: Option<usize>, pub support: Option<usize>, pub reduction_target: Decimal,
    shadow: DoubleVolumeBullishShadowExit,
}
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct RiskState {
    pub config: RiskConfig,
    /// 当前时点真实有效的正 N；None 表示没有有效正 N，而非自动推断一个信号。
    pub positive_n_index: Option<usize>,
    /// 仅能注入该笔持仓所属 N 的有日期测幅事件。
    pub projection_events: Vec<Value>, pub entry_index: Option<usize>, pub signal_index: Option<usize>, pub wave_reduced: bool,
    pub staged: StagedExitState, pub volume: VolumeRiskState,
    last: Option<usize>, pressure: PressureState, breakout: BreakoutState,
}
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct RiskEvaluation { pub decisions: Vec<RuleDecision>, pub unavailable_rules: Vec<String> }

fn body(b: &RuleCandle) -> Decimal { (b.open-b.close).abs() }
fn upper(b: &RuleCandle) -> Decimal { b.high-b.open.max(b.close) }
fn bearish_child(m: &RuleCandle,c: &RuleCandle) -> bool { m.close!=m.open&&c.close<c.open&&teaching_inside(m,c) }
fn last_bear(bars: &[RuleCandle], end: usize) -> Option<usize> { (0..end).rev().find(|&i|bars[i].close<bars[i].open) }
fn mean_volume(bars: &[RuleCandle]) -> Decimal { if bars.is_empty(){Decimal::ZERO}else{bars.iter().map(|b|b.volume).sum::<Decimal>()/Decimal::from(bars.len() as u64)} }
fn resistance(p:&RuleCandle,b:&RuleCandle,shadow:Decimal,up:bool)->Vec<&'static str> {
    let mut reasons=vec![];let span=b.high-b.low;
    if up {
        if b.open>p.close&&b.close<b.open{reasons.push("higher_open_bearish_body");}
        if b.open<p.close{reasons.push("direct_lower_open");}
        if span>Decimal::ZERO&&upper(b)>=span*shadow{reasons.push("long_upper_shadow");}
    }else{
        if b.open<p.close&&b.close>b.open{reasons.push("lower_open_bullish_body");}
        if b.open>p.close{reasons.push("direct_higher_open");}
        if span>Decimal::ZERO&&b.open.min(b.close)-b.low>=span*shadow{reasons.push("long_lower_shadow");}
    }
    reasons
}
fn adverse(p:&RuleCandle,b:&RuleCandle,include_body:bool)->Vec<&'static str>{
    let mut out=vec![];if include_body&&b.close<b.open{out.push("bearish_body");}
    if b.close<p.close{out.push("close_below_previous");}if b.low<p.low{out.push("low_below_previous");}
    if b.high>b.low&&upper(b)*Decimal::TWO>=b.high-b.low{out.push("long_upper_shadow");}out
}
fn observed(mut d:RuleDecision,bars:&[RuleCandle],i:usize)->RuleDecision{
    let b=&bars[i];d=d.with("execution_model","same_day_close").with("observed_open",b.open).with("observed_high",b.high)
        .with("observed_low",b.low).with("observed_close",b.close).with("observed_volume",b.volume);
    if i>0{d=d.with("previous_close",bars[i-1].close).with("previous_high",bars[i-1].high).with("previous_low",bars[i-1].low).with("previous_volume",bars[i-1].volume);}d
}
fn decision(bars:&[RuleCandle],i:usize,reason:&str,fraction:Decimal,cumulative:bool)->RuleDecision{
    let mut d=observed(RuleDecision::new(reason,i,fraction),bars,i);d.cumulative=cumulative;d
}
fn decimal(v:&Value,key:&str)->Option<Decimal>{let p=v.get(key)?;match (p).kind() {Some(crate::indexed_json::Kind::String(s))=>s.parse().ok(),Some(crate::indexed_json::Kind::Number(n))=>n.to_string().parse().ok(),_=>None}}
fn integer(v:&Value,key:&str)->Option<usize>{v.get(key)?.as_u64().and_then(|i|usize::try_from(i).ok())}
fn proof_price(d:&RuleDecision,key:&str)->Option<Decimal>{d.evidence.get(key).and_then(|v|match (v).kind() {Some(crate::indexed_json::Kind::String(s))=>s.parse().ok(),Some(crate::indexed_json::Kind::Number(n))=>n.to_string().parse().ok(),_=>None})}

fn supply_proof(d:RuleDecision,bars:&[RuleCandle],s:usize)->RuleDecision{
    d.with("pressure_date",&bars[s].timestamp).with("pressure_low",(bars[s].open+bars[s].close)/Decimal::TWO)
        .with("pressure_high",bars[s].high).with("pressure_volume",bars[s].volume)
        .with("pressure_volume_multiple",bars[s].volume/mean_volume(&bars[s-20..s]))
}
fn pressure_risks(bars:&[RuleCandle],i:usize,n:Option<usize>,state:&mut PressureState,cfg:&RiskConfig)->Vec<RuleDecision>{
    let mut out=vec![];let b=&bars[i];
    if i>=20{
        let p=&bars[i-1];let start=i.saturating_sub(cfg.pressure_lookback);
        if let Some(source)=(start..i).max_by(|&a,&b|bars[a].high.cmp(&bars[b].high).then_with(||b.cmp(&a))){
            let r=&bars[source];let patterns=resistance(p,b,ratio(1,2),true);let bad=adverse(p,b,true);
            if i-source>=20&&source>0&&r.close>r.open&&p.close<=r.high&&r.high<b.high&&b.close<=r.high
                &&resistance(&bars[source-1],r,ratio(1,2),true).is_empty()&&!patterns.is_empty()&&!bad.is_empty(){
                out.push(decision(bars,i,"record_high_resistance_reduce",ratio(1,2),false).with("record_high_date",&r.timestamp)
                    .with("record_high",r.high).with("record_high_age",i-source).with("record_breakout_index",i)
                    .with("record_resistance_patterns",patterns).with("record_adverse_patterns",bad));
            }
            let mut cleared=false;
            if let Some((warn,proof))=state.massive_warning.take(){
                if i==warn+1&&b.close<b.open&&proof_price(&proof,"record_warning_close").is_some_and(|v|b.close<v)
                    &&proof_price(&proof,"record_volume_threshold").is_some_and(|v|b.volume>=v){
                    let mut d=observed(proof,bars,i);d.reason="record_high_massive_followthrough_clear".into();d.bar_index=i;d.fraction=Decimal::ONE;d.cumulative=false;
                    out.push(d);cleared=true;
                }
            }
            if !cleared{
                if state.massive_candidate.is_some_and(|(_,attack,_)|i>attack+1){state.massive_candidate=None;}
                if state.massive_candidate.is_none()&&i-source>=20&&p.close<=r.high&&r.high<b.high{
                    let mean=mean_volume(&bars[i-20..i]);if mean>Decimal::ZERO{state.massive_candidate=Some((source,i,mean));}
                }
                if let Some((s,attack,mean))=state.massive_candidate{
                    if b.close<b.open&&!resistance(p,b,ratio(1,2),true).is_empty()&&b.volume>=mean*cfg.pressure_volume_ratio{
                        let d=decision(bars,i,"record_high_massive_resistance_reduce_30",ratio(3,10),true)
                            .with("record_high_date",&bars[s].timestamp).with("record_high",bars[s].high).with("record_high_age",attack-s)
                            .with("record_breakout_index",attack).with("record_breakout_date",&bars[attack].timestamp)
                            .with("record_warning_index",i).with("record_warning_close",b.close).with("record_warning_low",b.low)
                            .with("record_volume_mean",mean).with("record_volume_threshold",mean*cfg.pressure_volume_ratio)
                            .with("record_volume_ratio",b.volume/mean).with("record_resistance_patterns",resistance(p,b,ratio(1,2),true));
                        state.massive_warning=Some((i,d.clone()));state.massive_candidate=None;out.push(d);
                    }
                }
            }
        }
    }
    let mut supply_decision=None;
    if let Some((s,attack,low))=state.breakout{
        if i>attack{let bad=adverse(&bars[i-1],b,true);if !bad.is_empty(){
            supply_decision=Some(supply_proof(decision(bars,i,"pressure_breakout_adverse_clear",Decimal::ONE,false),bars,s)
                .with("pressure_breakout_index",attack).with("pressure_rally_low_date",&bars[low].timestamp)
                .with("pressure_rally_low",bars[low].low).with("pressure_rally_fraction",bars[attack].high/bars[low].low-Decimal::ONE)
                .with("pressure_adverse_patterns",bad));state.breakout=None;
        }}
    }
    if i>0&&state.breakout.is_none()&&!resistance(&bars[i-1],b,ratio(1,4),true).is_empty(){
        let mut qualified=vec![];
        for &s in &state.supplies{if s<i-1&&i-s<=cfg.pressure_lookback&&bars[i-1].close<=bars[s].high&&bars[s].high<b.high{
            if let Some(low)=(s+1..i).min_by_key(|&j|(bars[j].low,j)){if b.high>=bars[low].low*ratio(6,5){qualified.push((s,low));}}
        }}
        if let Some((s,low))=qualified.into_iter().min_by(|(a,_),(b,_)|bars[*a].high.cmp(&bars[*b].high).then_with(||b.cmp(a))){state.breakout=Some((s,i,low));}
    }
    state.supplies.retain(|&s|i-s<=cfg.pressure_lookback&&b.close<=bars[s].high);
    if state.armed.is_some_and(|(s,a)|!state.supplies.contains(&s)||n.is_some_and(|n|n!=a)){state.armed=None;}
    if state.armed.is_none(){if let Some(n)=n{
        if let Some(s)=state.supplies.iter().copied().filter(|&s|s<n&&b.high>=(bars[s].open+bars[s].close)/Decimal::TWO&&b.low<=bars[s].high)
            .min_by(|&a,&b|(bars[a].open+bars[a].close).cmp(&(bars[b].open+bars[b].close)).then_with(||b.cmp(&a))){state.armed=Some((s,n));}
    }}
    if let Some((s,attack))=state.armed{if i>attack{
        let p=&bars[i-1];let bad=adverse(p,b,false);if !bad.is_empty(){let gap=b.low>p.high&&b.close>p.close;
            supply_decision=Some(supply_proof(decision(bars,i,if gap{"pressure_gap_adverse_reduce"}else{"pressure_adverse_clear"},if gap{ratio(1,2)}else{Decimal::ONE},false),bars,s)
                .with("pressure_n_date",&bars[attack].timestamp).with("pressure_gap_unfilled",gap).with("pressure_adverse_patterns",bad));
        }
    }}
    if n.is_none(){state.armed=None;}
    if i>=20{let mean=mean_volume(&bars[i-20..i]);let span=b.high-b.low;let body=b.open-b.close;
        if body>=cfg.pressure_body_min_fraction*b.open&&body*Decimal::TWO>=span&&bars[i-20..i].iter().all(|p|b.high>=p.high)
            &&mean>Decimal::ZERO&&b.volume>=mean*cfg.pressure_volume_ratio{state.supplies.push(i);}}
    out.extend(supply_decision);out
}

fn breakout_limit(bar:&RuleCandle,previous:&RuleCandle,key:&RuleCandle,bear:Option<&RuleCandle>)->Option<BTreeMap<String,Value>>{
    if bar.close>=bar.open||bar.raw_is_st==Some(true)||bar.raw_trading_active==Some(false)||bar.adjustment_factor!=previous.adjustment_factor||bar.volume<=Decimal::ZERO{return None;}
    let date=chrono::NaiveDate::parse_from_str(bar.timestamp.get(..10)?,"%Y-%m-%d").ok()?;
    let rate=crate::execution::a_share_spec(&bar.symbol,date).ok()?.price_limit_rate;
    let raw=|p:Decimal|(p/bar.adjustment_factor).round_dp_with_strategy(2,RoundingStrategy::MidpointAwayFromZero);
    let limit=(raw(previous.close)*(Decimal::ONE+rate)).round_dp_with_strategy(2,RoundingStrategy::MidpointAwayFromZero);
    if raw(bar.high)!=limit||raw(bar.close)>=limit{return None;}
    for (name,reference) in [("breakout_key",Some(key)),("previous",Some(previous)),("last_bearish",bear)]{
        if let Some(r)=reference{if r.timestamp<bar.timestamp&&r.volume>Decimal::ZERO&&bar.volume>=Decimal::TWO*r.volume{
            return Some(RuleDecision::new("",0,Decimal::ZERO).with("breakout_limit_up_raw",limit).with("breakout_limit_high_raw",raw(bar.high))
                .with("breakout_limit_close_raw",raw(bar.close)).with("breakout_limit_rate",rate).with("breakout_double_volume_source",name)
                .with("breakout_double_volume_date",&r.timestamp).with("breakout_double_volume_reference",r.volume)
                .with("breakout_double_volume_ratio",bar.volume/r.volume).evidence);
        }}
    }None
}
fn breakout_risk(bars:&[RuleCandle],frames:&[KernelFrame],i:usize,state:&mut BreakoutState)->Option<RuleDecision>{
    if i==0{return None;}let b=&bars[i];let p=&bars[i-1];let bear=last_bear(bars,i).map(|j|&bars[j]);
    let vol=is_volume_bearish(b,Some(p),bear);
    if let Some((warn,mut proof))=state.warning.take(){if i==warn+1&&b.open<bars[warn].close{
        proof.reason="breakout_volume_bearish_shadow_next_lower_open_clear".into();proof.bar_index=i;proof.fraction=Decimal::ONE;proof.cumulative=false;
        for key in ["observed_open","observed_high","observed_low","observed_close","observed_volume"]{proof.evidence.remove(key);}
        state.candidate=None;return Some(proof.with("execution_model","next_open").with("observed_open",b.open).with("previous_close",bars[warn].close));
    }}
    if state.candidate.as_ref().is_some_and(|(a,_)|i>a+1){state.candidate=None;}
    if state.child_candidate.as_ref().is_some_and(|(a,_)|i>a+1){state.child_candidate=None;}
    let highs=frames.iter().filter(|f|f.level<=3).filter_map(|f|{
        let point=f.points.iter().rev().find(|p|p.kind==PointKind::H&&p.is_closed()&&p.index<=p.known_index&&p.known_index<i&&p.index<i&&p.evidence.get("display_only").and_then(Value::as_bool)!=Some(true))?;
        let price=point.value.to_string().parse::<Decimal>().ok()?;(price>Decimal::ZERO).then_some(KnownHigh{level:f.level,index:point.index,price,known:point.known_index})
    });
    if let Some(h)=highs.filter(|h|p.high<=h.price&&h.price<b.high).max_by_key(|h|(h.price,h.index,h.level)){
        state.candidate=Some((i,h.clone()));state.child_candidate=Some((i,h));
    }
    let child=state.child_candidate.is_some()&&vol&&bearish_child(p,b);
    if b.close>=b.open||state.candidate.is_none()&&!child{return None;}
    let gap=if b.open>p.high{Some("up")}else if b.open<p.low{Some("down")}else{None};
    let gap_clear=vol&&gap.is_some()&&body(b)>=b.open/Decimal::from(20)&&body(b)*Decimal::TWO>=b.high-b.low;
    let limit=state.candidate.as_ref().and_then(|(_,h)|breakout_limit(b,p,&bars[h.index],bear));
    if !gap_clear&&!child&&limit.is_none()&&(!vol||upper(b)<=body(b)){return None;}
    let (attack,h)=if child{state.child_candidate.clone()?}else{state.candidate.clone()?};
    let mut d=decision(bars,i,"breakout_volume_bearish_shadow_reduce_90",ratio(9,10),true)
        .with("trend_level",h.level).with("breakout_key_index",h.index).with("breakout_key_date",&bars[h.index].timestamp)
        .with("breakout_key_high",h.price).with("breakout_key_known_index",h.known).with("breakout_attack_index",attack)
        .with("breakout_attack_date",&bars[attack].timestamp).with("breakout_warning_index",i).with("breakout_warning_close",b.close)
        .with("breakout_upper_shadow",upper(b)).with("breakout_body",body(b)).with("breakout_resistance_phase",if attack==i{"attack"}else{"response"});
    if child{d.reason="breakout_volume_bearish_inside_child_clear".into();d.fraction=Decimal::ONE;d.cumulative=false;state.child_candidate=None;}
    else if gap_clear{d.reason="breakout_gap_volume_large_bearish_clear".into();d.fraction=Decimal::ONE;d.cumulative=false;d=d.with("breakout_gap_direction",gap);}
    else if let Some(limit)=limit{d.reason="breakout_double_volume_bearish_limit_open_reduce_90".into();d.evidence.extend(limit);}
    if !child&&!gap_clear{state.warning=Some((i,d.clone()));}state.candidate=None;Some(d)
}

pub fn observe_bearish_child_pattern(bars:&[RuleCandle],i:usize,state:&mut VolumeRiskState)->Option<RuleDecision>{
    if i==0||i>=bars.len(){return None;}let b=&bars[i];
    if let Some(w)=state.pattern_warning{if i>w{
        let mut r=i-1;while r>w&&bars[r].close>=bars[r].open{r-=1;}
        if b.low<bars[w].low&&b.close<bars[w].close&&((bars[w].volume>Decimal::ZERO&&b.volume>bars[w].volume)||(bars[r].volume>Decimal::ZERO&&b.volume>bars[r].volume)){
            return Some(decision(bars,i,"bearish_mother_child_break_clear",Decimal::ONE,false).with("child_date",&bars[w].timestamp)
                .with("child_low",bars[w].low).with("child_close",bars[w].close).with("child_volume",bars[w].volume)
                .with("bearish_reference_date",&bars[r].timestamp).with("bearish_reference_volume",bars[r].volume));
        }
    }}
    if bearish_child(&bars[i-1],b){state.pattern_warning=Some(i);}None
}
fn volume_core(bars:&[RuleCandle],i:usize,n:Option<usize>,state:&mut VolumeRiskState,small_cap:Decimal,small_window:usize)->Option<RuleDecision>{
    if i==0{return None;}let b=&bars[i];let p=&bars[i-1];let bear=last_bear(bars,i);let vol=is_volume_bearish(b,Some(p),bear.map(|j|&bars[j]));
    if let Some(d)=exits::observe_limit_up_distribution_exit(bars,i){return Some(d);}
    for (w,reason,only_next,close_break) in [
        (state.child_mother_warning,"volume_bearish_child_mother_break_clear",true,true),
        (state.mother_child_warning,"volume_bearish_mother_child_low_clear",false,false),
        (state.child_warning,"volume_bearish_child_break_clear",true,true)]{
        if let Some(w)=w{if i>w&&(!only_next||i==w+1)&&b.low<bars[w].low&&(!close_break||b.close<bars[w].close){
            return Some(decision(bars,i,reason,Decimal::ONE,false).with("warning_index",w).with("warning_low",bars[w].low).with("warning_close",bars[w].close));
        }}
    }
    if state.child_mother_warning.is_some_and(|w|i>w+1){state.child_mother_warning=None;}
    if i>=2&&bars[i-2].volume>Decimal::ZERO&&p.volume>Decimal::from(3)*bars[i-2].volume&&b.open<p.close&&b.close<p.low
        &&b.open-b.close>=b.open/Decimal::from(20)&&vol{return Some(decision(bars,i,"volume_previous_triple_gap_bearish_clear",Decimal::ONE,false));}
    if i>=10{
        let mean=mean_volume(&bars[i-10..i]);if mean>Decimal::ZERO&&bars[i-10..i].iter().all(|p|b.volume>p.volume)
            &&b.volume>=Decimal::TWO*mean&&b.open>p.high&&b.close<p.low&&b.open-b.close>=b.open/Decimal::from(20){
            return Some(decision(bars,i,"volume_massive_gap_reversal_clear",Decimal::ONE,false).with("massive_volume_mean",mean).with("massive_volume_multiple",b.volume/mean));
        }
    }
    if i>=2&&p.close-p.open>=p.open/Decimal::from(20)&&bars[i-2].volume>Decimal::ZERO&&b.open<(p.open+p.close)/Decimal::TWO&&b.close<b.open{
        let regular=p.volume>bars[i-2].volume;
        let exceptional=i>=3&&bars[i-2].close>bars[i-2].open&&bars[i-3].close>bars[i-3].open&&bars[i-3].volume>Decimal::ZERO
            &&bars[i-2].volume>=Decimal::TWO*bars[i-3].volume&&p.volume>bars[i-3].volume;
        if (regular||exceptional)&&last_bear(bars,i-1).is_some_and(|r|bars[r].volume>Decimal::ZERO&&b.volume>bars[r].volume){
            return Some(decision(bars,i,"volume_bullish_gap_bearish_clear",Decimal::ONE,false)
                .with("bullish_body_midpoint",(p.open+p.close)/Decimal::TWO).with("bullish_volume_basis",if regular{"previous_session"}else{"bullish_before_exceptional_volume"}));
        }
    }
    if i>=2&&b.close<b.open&&p.close>p.open&&b.high>=p.high&&b.close<p.low{
        if let Some(r)=(0..i-1).rev().find(|&j|bars[j].close<=bars[j].open){if bars[r].close<bars[r].open&&b.volume>bars[r].volume{
            return Some(decision(bars,i,"volume_bearish_outside_clear",Decimal::ONE,false).with("bearish_reference_date",&bars[r].timestamp).with("bearish_reference_volume",bars[r].volume));
        }}
    }
    if b.close<b.open&&teaching_outside(p,b)&&bear.is_some_and(|r|bars[r].volume>Decimal::ZERO&&b.volume>bars[r].volume){
        state.child_mother_warning=Some(i);if state.reduction_target<ratio(7,10){state.reduction_target=ratio(7,10);
            return Some(decision(bars,i,"volume_bearish_child_mother_reduce_70",ratio(7,10),true).with("child_date",&p.timestamp).with("mother_date",&b.timestamp));}
    }
    let mother_bearish=p.close<p.open;
    let contained=if mother_bearish{teaching_inside(p,b)}else{b.high<p.high&&b.low>p.low};
    let child=i>=2&&p.close!=p.open&&b.close<b.open&&contained&&last_bear(bars,i-1).is_some_and(|r|
        bars[r].volume>Decimal::ZERO&&b.volume>bars[r].volume&&(mother_bearish||p.close>bars[r].high));
    if state.child_warning.is_some_and(|w|i==w+1){if child&&mother_bearish{state.mother_child_warning=Some(i);}return None;}
    if child{
        if mother_bearish{state.mother_child_warning=Some(i);}else{state.child_warning=Some(i);}
        if state.reduction_target<ratio(7,10){state.trigger=Some(i);state.support=nearest_support(bars,i);state.reduction_target=ratio(7,10);
            return Some(decision(bars,i,if mother_bearish{"volume_bearish_mother_child_reduce_70"}else{"volume_bearish_child_reduce_70"},ratio(7,10),true)
                .with("mother_date",&p.timestamp).with("child_date",&b.timestamp));}
    }
    let small=small_window>0&&i>=small_window&&n.filter(|&n|n<i).is_some_and(|n|{
        let start=i-small_window;let mean=bars[start..i].iter().map(body).sum::<Decimal>()/Decimal::from(small_window as u64);
        body(b)<=b.open*small_cap&&body(b)<mean&&b.high<=bars[n].high&&b.low>=bars[n].low
    });
    let eligible=b.volume>p.volume&&b.close<p.close&&(b.close<b.open||small);
    if state.trigger.is_none(){if !eligible{return None;}state.trigger=Some(i);state.support=nearest_support(bars,i);}
    if state.support.is_some_and(|s|b.low<bars[s].low){return Some(decision(bars,i,"volume_down_support_break_clear",Decimal::ONE,false).with("support_index",state.support));}
    if let Some(trigger)=state.trigger{if i==trigger+1{
        if b.open<p.close&&b.close<b.open{return Some(decision(bars,i,"volume_down_next_gap_fade_clear",Decimal::ONE,false));}
        if state.reduction_target>=ratio(7,10)&&b.close<b.open&&b.close<bars[trigger].low{return Some(decision(bars,i,"volume_down_next_followthrough_clear",Decimal::ONE,false));}
    }}
    let target=if small{ratio(3,10)}else{ratio(7,10)};
    if eligible&&target>state.reduction_target{state.reduction_target=target;
        return Some(decision(bars,i,if small{"volume_down_small_n_reduce_30"}else{"volume_down_reduce_70"},target,true)
            .with("execution_model",if small{"next_open"}else{"same_day_close"}).with("volume_trigger_index",state.trigger).with("support_index",state.support));}
    None
}
fn nearest_support(bars:&[RuleCandle],i:usize)->Option<usize>{(1..i.saturating_sub(1)).rev().find(|&j|bars[j].low<bars[j-1].low&&bars[j].low<bars[j+1].low)}
pub fn observe_volume_risk(bars:&[RuleCandle],i:usize,positive_n:Option<usize>,state:&mut VolumeRiskState)->Option<RuleDecision>{
    observe_volume_risk_with_policy(bars,i,positive_n,state,ratio(1,100),10)
}
pub fn observe_volume_risk_with_policy(bars:&[RuleCandle],i:usize,positive_n:Option<usize>,state:&mut VolumeRiskState,small_cap:Decimal,small_window:usize)->Option<RuleDecision>{
    if i>=bars.len(){return None;}let shadow=state.shadow.advance(bars,i);let core=volume_core(bars,i,positive_n,state,small_cap,small_window);
    if i==0||core.as_ref().is_some_and(|d|d.fraction==Decimal::ONE){return core;}
    if let Some(d)=shadow.or_else(||exits::observe_gap_bearish_exit(bars,i)).or_else(||exits::observe_double_volume_lower_close_exit(bars,i)){return Some(d);}
    let b=&bars[i];let p=&bars[i-1];
    if i>=2&&bars[i-2].volume>Decimal::ZERO&&p.volume>Decimal::from(10)*bars[i-2].volume&&p.open==p.close
        &&exits::is_sealed_limit_up(&bars[i-2],p)&&b.adjustment_factor==p.adjustment_factor&&b.close<p.close{
        return Some(decision(bars,i,"volume_over_tenfold_limit_next_lower_clear",Decimal::ONE,false));}
    if p.volume<=Decimal::ZERO{return core;}
    if b.volume>Decimal::from(10)*p.volume&&!(b.open==b.close&&exits::is_sealed_limit_up(p,b)){
        return Some(decision(bars,i,"volume_over_tenfold_clear",Decimal::ONE,false));}
    if b.volume>=Decimal::TWO*p.volume&&b.close<b.open&&b.high>b.low&&upper(b)*Decimal::TWO>=b.high-b.low&&core.as_ref().is_none_or(|d|d.fraction<=ratio(7,10)){
        return Some(decision(bars,i,"volume_double_bearish_long_upper_shadow_reduce_70",ratio(7,10),true));}
    core
}

fn wave_stage_rank(stage:&str)->u8{match stage{"one_p"=>1,"two_t"=>2,"five_top"=>3,"ten_full"=>4,_=>0}}
fn active_wave(events:&[Value],i:usize,five_only:bool)->Option<(&Value,String)>{
    let mut sorted:Vec<_>=events.iter().filter(|e|integer(e,"bar_index").is_some_and(|j|j<=i)&&integer(e,"attack").is_some_and(|j|j<=i)).collect();
    sorted.sort_by_key(|e|integer(e,"bar_index"));let mut active=BTreeMap::new();let mut invalidated=BTreeSet::new();
    for e in sorted{
        let key=(integer(e,"attack")?,integer(e,"origin_index"));let kind=e.get("event").and_then(Value::as_str).unwrap_or("");
        if kind=="wave_projection_invalidated"{invalidated.insert(key);active.remove(&key);continue;}
        if invalidated.contains(&key){continue;}
        let stage=e.get("reached_stage").and_then(Value::as_str).unwrap_or("");
        if five_only{
            if kind=="wave_projection_target_reached"&&integer(e,"bar_index").is_some_and(|j|j<i)&&["five_top","ten_full"].contains(&stage){active.insert(key,(e,stage.to_string()));}
        }else if kind=="wave_projection_ready"{active.insert(key,(e,"two_t".to_string()));}
        else if ["wave_projection_target_reached","wave_n_target_reached"].contains(&kind){active.insert(key,(e,stage.to_string()));}
    }
    active.into_values().max_by_key(|(e,s)|(wave_stage_rank(s),integer(e,"bar_index"),integer(e,"attack")))
}
fn wave_proof(mut d:RuleDecision,bars:&[RuleCandle],e:&Value,stage:&str)->Option<RuleDecision>{
    let attack=integer(e,"attack")?;let reached=integer(e,"bar_index")?;
    d=d.with("wave_n_date",&bars.get(attack)?.timestamp).with("wave_reached_date",&bars.get(reached)?.timestamp)
        .with("wave_reached_stage",stage).with("wave_reached_price",if stage=="two_t"{decimal(e,"two_t").or_else(||decimal(e,"reached_target"))}else{decimal(e,"reached_target")});
    if let Some(origin)=integer(e,"origin_index"){d=d.with("wave_n_origin_date",&bars.get(origin)?.timestamp).with("wave_n_origin_price",bars[origin].low);}
    Some(d)
}
fn five_child(bars:&[RuleCandle],i:usize)->bool{
    if i<3{return false;}let(m,c,p,b)=(&bars[i-3],&bars[i-2],&bars[i-1],&bars[i]);
    m.close!=m.open&&teaching_inside(m,c)&&b.close<b.open&&b.low<p.low&&b.close<p.close&&b.low<c.low
        &&last_bear(bars,i).is_some_and(|r|bars[r].volume>Decimal::ZERO&&b.volume>bars[r].volume)
}
pub fn observe_five_top_risks(bars:&[RuleCandle],i:usize,events:&[Value])->Vec<RuleDecision>{
    if i==0||i>=bars.len(){return vec![];}let Some((e,stage))=active_wave(events,i,true)else{return vec![];};
    let b=&bars[i];let p=&bars[i-1];let mut reasons=vec![];
    if five_child(bars,i){reasons.push("wave_five_top_child_volume_clear");}
    if b.open<p.close&&b.close<p.open&&last_bear(bars,i).is_some_and(|r|bars[r].volume>Decimal::ZERO&&b.volume>bars[r].volume){reasons.push("wave_five_top_gap_volume_clear");}
    if b.high>b.low&&upper(b)*Decimal::TWO>=b.high-b.low{
        let (lo,hi)=(b.open.min(b.close),b.open.max(b.close));let(pl,ph)=(p.open.min(p.close),p.open.max(p.close));
        if lo>=pl&&hi<=ph&&(lo!=pl||hi!=ph){reasons.push("wave_five_top_body_upper_shadow_clear");}
        else if b.open<p.close&&b.close<=b.open{reasons.push("wave_five_top_gap_upper_shadow_clear");}
    }
    reasons.into_iter().filter_map(|r|wave_proof(decision(bars,i,r,Decimal::ONE,false),bars,e,&stage)).collect()
}
pub fn observe_c_equal_near_risk(bars:&[RuleCandle],i:usize,events:&[Value],reduction:Decimal)->Option<RuleDecision>{
    if i==0||i>=bars.len(){return None;}let b=&bars[i];let p=&bars[i-1];let tick=ratio(1,100);
    let clear_possible=b.low<p.low&&b.close<p.close&&last_bear(bars,i).is_some_and(|r|bars[r].volume>Decimal::ZERO&&b.volume>bars[r].volume);
    let mut sorted:Vec<_>=events.iter().collect();sorted.sort_by_key(|e|std::cmp::Reverse(integer(e,"bar_index")));
    for e in sorted{
        if e.get("event").and_then(Value::as_str)!=Some("wave_c_equal_target"){continue;}
        let (Some(entry),Some(attack),Some(target),Some(defense))=(integer(e,"bar_index"),integer(e,"attack"),decimal(e,"target"),decimal(e,"defense"))else{continue;};
        if attack>entry||entry>=i||bars[entry+1..i].iter().any(|b|b.low<defense)||!clear_possible&&(target-b.high<Decimal::ZERO||target-b.high>tick){continue;}
        let Some(w)=(entry+1..=i).find(|&j|bars[j].high>=target-tick)else{continue;};let gap=target-bars[w].high;
        if gap<Decimal::ZERO||gap>tick||resistance(&bars[w-1],&bars[w],ratio(1,2),true).is_empty(){continue;}
        let proof=decision(bars,i,"wave_c_equal_near_resistance_reduce",reduction,true).with("wave_n_date",&bars[attack].timestamp)
            .with("wave_c_equal_target",target).with("wave_target_gap",gap).with("target_warning_date",&bars[w].timestamp)
            .with("target_warning_high",bars[w].high).with("target_warning_low",bars[w].low).with("target_warning_close",bars[w].close)
            .with("target_resistance_patterns",resistance(&bars[w-1],&bars[w],ratio(1,2),true));
        if w==i{return Some(proof);}let mut reference=last_bear(bars,w+1);
        for day in w+1..=i{
            if let Some(r)=reference{if bars[r].volume>Decimal::ZERO&&bars[day].low<bars[day-1].low&&bars[day].close<bars[day-1].close&&bars[day].volume>bars[r].volume{
                if day==i{let mut d=proof;d.reason="wave_c_equal_near_volume_clear".into();d.fraction=Decimal::ONE;d.cumulative=false;
                    return Some(d.with("bearish_reference_date",&bars[r].timestamp).with("bearish_reference_volume",bars[r].volume));}break;
            }}
            if bars[day].close<bars[day].open{reference=Some(day);}
        }
    }None
}
fn target_candle(bars:&[RuleCandle],i:usize,events:&[Value],cfg:&RiskConfig,reduced:bool)->Option<RuleDecision>{
    if i==0||i>=bars.len(){return None;}let (e,stage)=active_wave(events,i,false)?;let b=&bars[i];let p=&bars[i-1];
    let span=b.high-b.low;if span<=Decimal::ZERO{return None;}let upper=upper(b);let lower=b.open.min(b.close)-b.low;let body=b.open-b.close;
    let proof=wave_proof(decision(bars,i,"",Decimal::ONE,false),bars,e,&stage)?.with("wave_range_fraction",span/p.close)
        .with("wave_upper_shadow_fraction",upper/span).with("wave_lower_shadow_fraction",lower/span).with("wave_body_fraction",body/b.open)
        .with("wave_body_range_fraction",body/span).with("wave_gap_fraction",(b.open-p.high)/p.high);
    let make=|reason:&str,fraction:Decimal,cumulative:bool|{let mut d=proof.clone();d.reason=reason.into();d.fraction=fraction;d.cumulative=cumulative;d};
    if ["five_top","ten_full"].contains(&stage.as_str())&&integer(e,"bar_index").is_some_and(|j|j<i)&&five_child(bars,i){return Some(make("wave_five_top_child_volume_clear",Decimal::ONE,false));}
    if p.close>p.open&&body>=cfg.wave_engulf_min_body*b.open&&body>=span*ratio(6,10)&&b.open>=p.close&&b.close<=p.open{return Some(make("wave_bearish_engulf_clear",Decimal::ONE,false));}
    if i>=2&&p.close>p.open&&!resistance(&bars[i-2],p,ratio(1,2),false).is_empty()&&body>=cfg.wave_engulf_min_body*b.open
        &&body>=span*ratio(6,10)&&b.close<p.low.min(bars[i-2].close){return Some(make("wave_bull_resistance_failed_clear",Decimal::ONE,false)
            .with("resistance_date",&p.timestamp).with("resistance_virtual_low",p.low.min(bars[i-2].close)));}
    let vol=is_volume_bearish(b,Some(p),last_bear(bars,i).map(|j|&bars[j]));
    if stage=="ten_full"&&body>Decimal::ZERO&&vol&&decimal(e,"reached_target").is_some_and(|v|b.high>=v){return Some(make("wave_ten_full_bearish_volume_clear",Decimal::ONE,false));}
    if reduced{return None;}
    if stage=="two_t"&&body>Decimal::ZERO&&vol{return Some(make("wave_two_t_volume_bearish_reduce",cfg.wave_exhaustion_reduction,true));}
    if ["five_top","ten_full"].contains(&stage.as_str())&&body>Decimal::ZERO{return Some(make("wave_target_bearish_reduce",ratio(7,10),true));}
    if stage=="ordinary_equal"&&b.volume>p.volume&&upper*Decimal::TWO>=span{return Some(make("wave_ordinary_equal_upper_shadow_reduce",cfg.wave_exhaustion_reduction,true));}
    if ["one_p","two_t","five_top","ten_full"].contains(&stage.as_str())&&upper*Decimal::TWO>=span{return Some(make("wave_target_upper_shadow_reduce",cfg.wave_exhaustion_reduction,true));}
    if b.volume>p.volume&&b.open>p.high&&(body>=cfg.wave_engulf_min_body*b.open||body*Decimal::TWO>=span)
        &&span>=p.close*cfg.wave_exhaustion_min_range&&upper<=span*ratio(2,10){return Some(make("wave_gap_reversal_reduce",cfg.wave_exhaustion_reduction,true));}
    if b.volume>p.volume&&b.high>p.high&&body>Decimal::ZERO&&span>=p.close*cfg.wave_exhaustion_min_range&&upper>=body&&lower<=span*cfg.wave_exhaustion_min_shadow{
        return Some(make("wave_upper_rejection_reduce",cfg.wave_exhaustion_reduction,true));}
    if b.volume>p.volume&&span>=p.close*cfg.wave_exhaustion_min_range&&upper>=span*cfg.wave_exhaustion_min_shadow&&lower>=span*cfg.wave_exhaustion_min_shadow&&body.abs()<=span*ratio(3,10){
        return Some(make("wave_volume_shadows_reduce",cfg.wave_exhaustion_reduction,true));}None
}
fn c_shadow(bars:&[RuleCandle],i:usize)->bool{i>0&&bars[i].close<bars[i-1].close&&bars[i].high>bars[i].low&&upper(&bars[i])*Decimal::TWO>=bars[i].high-bars[i].low}
fn ordinary_c(bars:&[RuleCandle],i:usize,e:&Value,cfg:&RiskConfig,reduced:bool,entry_index:Option<usize>)->Option<RuleDecision>{
    let held=entry_index.or_else(||integer(e,"bar_index"))?;let (one,a,two,target)=(decimal(e,"one_p")?,decimal(e,"a_high")?,decimal(e,"two_t")?,decimal(e,"target")?);
    if i<=held||one>a||a>=two{return None;}let b=&bars[i];let mut partial=None;
    if let Some(t)=decimal(e,"target_0618"){
        if let Some(reached)=(held+1..=i).find(|&j|bars[j].high>=t){
            let base=decision(bars,i,"wave_c_0618_upper_shadow_reduce",cfg.wave_exhaustion_reduction,true).with("wave_a_class","ordinary")
                .with("wave_reached_date",&bars[reached].timestamp).with("wave_reached_stage","c_0618").with("wave_reached_price",t)
                .with("wave_a_high",a).with("wave_b_low",decimal(e,"b_low")).with("wave_c_0618_target",t);
            if let Some(w)=(reached..i).find(|&j|c_shadow(bars,j)){
                if b.close<bars[i-1].close&&b.low<bars[w].low&&b.close<bars[w].close{let mut d=base;d.reason="wave_c_0618_shadow_break_clear".into();d.fraction=Decimal::ONE;d.cumulative=false;
                    return Some(d.with("abnormal_date",&bars[w].timestamp).with("abnormal_low",bars[w].low).with("abnormal_close",bars[w].close));}
            }
            if !reduced&&c_shadow(bars,i){partial=Some(base);}
        }
    }
    let Some(reached)=(held+1..=i).find(|&j|bars[j].high>=target)else{return partial;};
    let mut event=e.clone();event["event"]=crate::indexed_json::json!("wave_projection_target_reached");event["bar_index"]=crate::indexed_json::json!(reached);
    event["reached_stage"]=crate::indexed_json::json!("ordinary_equal");event["reached_target"]=crate::indexed_json::json!(target);let events=[event];
    for j in reached..i{if let Some(w)=target_candle(bars,j,&events,cfg,false){if w.cumulative{
        if b.close<bars[i-1].close{let mut d=observed(w,bars,i);d.reason="wave_ordinary_equal_lower_close_clear".into();d.bar_index=i;d.fraction=Decimal::ONE;d.cumulative=false;
            return Some(d.with("abnormal_date",&bars[j].timestamp).with("abnormal_close",bars[j].close));}return partial;
    }}}
    let current=target_candle(bars,i,&events,cfg,reduced);if current.as_ref().is_some_and(|d|d.fraction==Decimal::ONE){current}else{partial.or(current)}
}
pub fn observe_wave_exhaustion(bars:&[RuleCandle],i:usize,events:&[Value],cfg:&RiskConfig,reduced:bool,entry_index:Option<usize>,signal_index:Option<usize>)->Option<RuleDecision>{
    if i==0||i>=bars.len()||events.is_empty(){return None;}
    let five=observe_five_top_risks(bars,i,events);let shadow=five.iter().find(|d|["wave_five_top_body_upper_shadow_clear","wave_five_top_gap_upper_shadow_clear"].contains(&d.reason.as_str())).cloned();
    let shadow=shadow.map(|d|five.iter().find(|d|d.reason=="wave_five_top_gap_volume_clear").cloned().unwrap_or(d));
    if let Some(e)=events.iter().find(|e|e.get("event").and_then(Value::as_str)==Some("wave_ordinary_entry")&&integer(e,"bar_index")==signal_index.or(entry_index)){
        let current=ordinary_c(bars,i,e,cfg,reduced,entry_index);if current.as_ref().is_some_and(|d|d.fraction==Decimal::ONE){return current;}return shadow.or(current);
    }
    let current=target_candle(bars,i,events,cfg,reduced);if current.as_ref().is_some_and(|d|d.fraction==Decimal::ONE){return current;}
    let held=entry_index.or(signal_index).unwrap_or(0);
    if i>held+1&&bars[i].close<bars[i-1].close{
        let mut active=None;
        for j in (held+1).max(1)..i{
            if active.is_some()&&bars[j].close<bars[j-1].close{active=None;}
            if let Some(w)=target_candle(bars,j,events,cfg,false){if w.cumulative&&(j==i-1||w.reason=="wave_target_upper_shadow_reduce"){active=Some((j,w));}}
        }
        if let Some((j,w))=active{let mut d=observed(w,bars,i);d.reason="wave_abnormal_followthrough_clear".into();d.bar_index=i;d.fraction=Decimal::ONE;d.cumulative=false;
            return Some(d.with("abnormal_date",&bars[j].timestamp).with("abnormal_close",bars[j].close));}
    }
    shadow.or(current)
}

/// 每个交易日仅推进一次。返回全部独立风险证据；规则优先级由真实策略消费方裁定。
pub fn evaluate_daily_risks(bars:&[RuleCandle],frames:&[KernelFrame],index:usize,state:&mut RiskState)->Result<RiskEvaluation,String>{
    if index>=bars.len()||state.last.map_or(0,|i|i+1)!=index{return Err("daily risks require consecutive available bars".into());}
    let cfg=&state.config;
    if cfg.pressure_lookback==0||cfg.pressure_volume_ratio<Decimal::ONE||[cfg.pressure_body_min_fraction,cfg.wave_exhaustion_reduction,
        cfg.wave_exhaustion_min_range,cfg.wave_exhaustion_min_shadow,cfg.wave_engulf_min_body].iter().any(|&v|v<=Decimal::ZERO||v>=Decimal::ONE){return Err("invalid risk policy".into());}
    super::validate_bars(&bars[index.saturating_sub(1)..=index])?;
    if state.positive_n_index.is_some_and(|n|n>index)||state.entry_index.is_some_and(|n|n>index)||state.signal_index.is_some_and(|n|n>index){return Err("risk context cannot refer to a future bar".into());}
    for e in &state.projection_events{
        if let Some(at)=integer(e,"bar_index"){if at>index{return Err("risk projection events must already be known".into());}}
        if integer(e,"attack").is_some_and(|at|at>index)||integer(e,"origin_index").is_some_and(|at|at>index){return Err("risk projection source must already be known".into());}
        if integer(e,"known_at").or_else(||integer(e,"available_at")).is_some_and(|at|at>index){return Err("risk projection publication is in the future".into());}
    }
    let bars=&bars[..=index];let mut result=RiskEvaluation::default();
    result.decisions.extend(pressure_risks(bars,index,state.positive_n_index,&mut state.pressure,cfg));
    result.decisions.extend(breakout_risk(bars,frames,index,&mut state.breakout));
    result.decisions.extend(observe_bearish_child_pattern(bars,index,&mut state.volume));
    result.decisions.extend(observe_volume_risk(bars,index,state.positive_n_index,&mut state.volume));
    result.decisions.extend(exits::observe_double_volume_fade(bars,index));
    result.decisions.extend(exits::volume_shadow_next_decline(bars,index,None));
    result.decisions.extend(observe_c_equal_near_risk(bars,index,&state.projection_events,cfg.wave_exhaustion_reduction));
    result.decisions.extend(observe_five_top_risks(bars,index,&state.projection_events));
    if state.entry_index.is_some()||state.signal_index.is_some(){
        result.decisions.extend(exits::observe_staged_exit(bars,index,&mut state.staged,ratio(65,100),true)?);
        result.decisions.extend(exits::observe_inverse_resistance_exit(bars,index,&mut state.staged));
        result.decisions.extend(observe_wave_exhaustion(bars,index,&state.projection_events,cfg,state.wave_reduced,state.entry_index,state.signal_index));
    }
    let mut seen=BTreeSet::new();result.decisions.retain(|d|seen.insert((d.reason.clone(),d.bar_index)));
    state.last=Some(index);Ok(result)
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct HoldingRiskPolicy {
    pub risk:RiskConfig,pub volume_down_exit:bool,pub small_n_reduction:bool,pub small_body_max_fraction:Decimal,pub small_body_lookback:usize,
    pub inverse_n_close_reduce:bool,pub wave_exhaustion_exit:bool,pub pressure_adverse_exit:bool,pub trend_flip_adverse_exit:bool,
    pub staged_exit_enabled:bool,pub staged_exit_same_day:bool,pub staged_exit_intraday:bool,pub initial_reduction_fraction:Decimal,
}
impl Default for HoldingRiskPolicy {fn default()->Self{Self{risk:RiskConfig::default(),volume_down_exit:false,small_n_reduction:false,
    small_body_max_fraction:ratio(1,100),small_body_lookback:10,inverse_n_close_reduce:false,wave_exhaustion_exit:false,
    pressure_adverse_exit:false,trend_flip_adverse_exit:false,staged_exit_enabled:false,staged_exit_same_day:false,
    staged_exit_intraday:false,initial_reduction_fraction:ratio(1,2)}}}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct HoldingRiskState {pub staged:StagedExitState,pub volume:VolumeRiskState,pub position_key:Option<String>,last:Option<usize>}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct HoldingRiskContext {
    pub position_key:String,pub entry_index:usize,pub signal_index:usize,pub wave_reduced:bool,pub trend_last_fall_high_reduced:bool,
    pub positive_n_index:Option<usize>,pub wave_events:Vec<Value>,pub c_equal_events:Vec<Value>,
    pub pressure_warning:Option<BTreeMap<String,Value>>,pub record_high_warning:Option<BTreeMap<String,Value>>,
    pub inverse_index:Option<usize>,pub trading_eligible:bool,pub daily_fallback:bool,
    /// 必须由实际信号/硬风控提供；不从趋势标签替调用者生成。
    pub hard_exit:Option<RuleDecision>,pub volume_inverse:Option<RuleDecision>,pub mother_child_inverse:Option<RuleDecision>,pub target_risk:Option<RuleDecision>,
    /// 已有全清或部分成交剩余量，后续收盘观察不得重置其数量。
    pub pending_full_exit:bool,
}
impl HoldingRiskContext {
    pub fn from_position(position:&crate::execution::BacktestPosition)->Self {
        let signal_index=position.entry_evidence.get("signal_index").and_then(Value::as_u64).and_then(|v|usize::try_from(v).ok()).unwrap_or(position.entry_index);
        Self{position_key:format!("{}:{}",position.symbol,position.entry_time),entry_index:position.entry_index,signal_index,
            wave_reduced:position.wave_reduced,trend_last_fall_high_reduced:position.trend_last_fall_high_reduced,
            positive_n_index:None,wave_events:vec![],c_equal_events:vec![],pressure_warning:position.pressure_warning.clone(),record_high_warning:position.record_high_warning.clone(),
            inverse_index:position.inverse_index,trading_eligible:false,daily_fallback:false,hard_exit:None,volume_inverse:None,mother_child_inverse:None,target_risk:None,pending_full_exit:false}
    }
}
fn family(mut d:RuleDecision,name:&str)->RuleDecision{d.evidence.insert("risk_family".into(),Value::String(name.into()));d}
fn marked(decisions:&[RuleDecision],name:&str)->Option<RuleDecision>{decisions.iter().find(|d|d.evidence.get("risk_family").and_then(Value::as_str)==Some(name)).cloned()}
fn five_shadow(d:&RuleDecision)->bool{["wave_five_top_body_upper_shadow_clear","wave_five_top_gap_upper_shadow_clear"].contains(&d.reason.as_str())}
fn pressure_allowed(d:&RuleDecision,ctx:&HoldingRiskContext)->bool{
    let value=|key:&str|d.evidence.get(key).and_then(Value::as_u64).and_then(|v|usize::try_from(v).ok());
    if d.reason=="breakout_volume_bearish_shadow_next_lower_open_clear"{return false;}
    if d.reason.starts_with("trend_last_fall_high_")&&value("trend_warning_index").is_some_and(|w|ctx.entry_index>w){return false;}
    if d.reason=="trend_last_fall_high_upper_shadow_reduce"&&ctx.trend_last_fall_high_reduced{return false;}
    if d.reason=="pressure_breakout_adverse_clear"&&value("pressure_breakout_index").is_some_and(|w|ctx.entry_index>w){return false;}
    if d.reason=="record_high_resistance_reduce"&&value("record_breakout_index").is_some_and(|w|ctx.entry_index>=w){return false;}
    if d.reason.starts_with("record_high_massive_")&&value("record_warning_index").is_some_and(|w|ctx.entry_index>w){return false;}true
}
/// Python analytics/backtest.py 的收盘优先级；不能以简单的最大退出比例替代。
pub fn resolve_exit_precedence(decisions:&[RuleDecision],ctx:&HoldingRiskContext)->Result<Option<RuleDecision>,String>{
    if ctx.pending_full_exit{return Ok(None);}
    if decisions.iter().any(|d|d.fraction<=Decimal::ZERO||d.fraction>Decimal::ONE){return Err("invalid exit fraction".into());}
    let mut pressure=marked(decisions,"pressure");let mut wave=marked(decisions,"wave");let volume=marked(decisions,"volume");
    let inverse=marked(decisions,"inverse");let hard=ctx.hard_exit.clone();let vinverse=ctx.volume_inverse.clone();
    if pressure.as_ref().is_some_and(|d|["record_high_resistance_reduce","record_high_massive_resistance_reduce_30",
        "breakout_volume_bearish_shadow_reduce_90","breakout_double_volume_bearish_limit_open_reduce_90"].contains(&d.reason.as_str()))
        &&(hard.is_some()||vinverse.is_some()||inverse.is_some()||volume.as_ref().is_some_and(|d|d.fraction==Decimal::ONE)){pressure=None;}
    if pressure.as_ref().is_some_and(|d|d.fraction<Decimal::ONE)&&volume.as_ref().is_some_and(|d|["limit_up_distribution_midpoint_clear","volume_bullish_gap_bearish_clear"].contains(&d.reason.as_str())){pressure=None;}
    if wave.as_ref().is_some_and(five_shadow)&&(ctx.mother_child_inverse.is_some()||hard.is_some()||vinverse.is_some()||inverse.is_some()
        ||pressure.as_ref().is_some_and(|d|d.fraction==Decimal::ONE)||volume.as_ref().is_some_and(|d|d.fraction==Decimal::ONE)){wave=None;}
    if let Some(v)=&volume{if ["volume_over_tenfold_clear","volume_double_bearish_long_upper_shadow_reduce_70","volume_over_tenfold_limit_next_lower_clear",
        "double_volume_bullish_breakout_next_bearish_lower_close_reduce_90","triple_volume_bullish_breakout_next_double_volume_bearish_clear",
        "volume_gap_medium_bearish_clear","volume_bearish_below_double_bullish_shadow_close_clear","volume_after_double_lower_close_clear"].contains(&v.reason.as_str()){
        if pressure.as_ref().is_some_and(|d|d.fraction<v.fraction){pressure=None;}if wave.as_ref().is_some_and(|d|d.fraction<v.fraction){wave=None;}
    }}
    if let Some(d)=&ctx.mother_child_inverse{return Ok(Some(d.clone()));}
    if wave.as_ref().is_some_and(|d|d.fraction==Decimal::ONE){return Ok(wave);}
    if pressure.is_some(){return Ok(pressure);}if vinverse.is_some(){return Ok(vinverse);}
    if volume.as_ref().is_some_and(|d|d.fraction==Decimal::ONE&&(inverse.is_none()||d.reason=="volume_upper_shadow_next_open_decline_clear")){return Ok(volume);}
    if hard.is_some(){return Ok(hard);}if inverse.is_some(){return Ok(inverse);}if wave.is_some(){return Ok(wave);}if volume.is_some(){return Ok(volume);}
    Ok(marked(decisions,"staged"))
}
/// 持仓专用观察状态按实际成交身份重置；全局压力历史只消费既有证据。
pub fn evaluate_holding_risks(bars:&[RuleCandle],index:usize,global_decisions:&[RuleDecision],ctx:&HoldingRiskContext,
    state:&mut HoldingRiskState,policy:&HoldingRiskPolicy)->Result<RiskEvaluation,String>{
    if index>=bars.len()||ctx.entry_index>index||ctx.signal_index>index||ctx.position_key.is_empty(){return Err("holding risk requires an actual known position".into());}
    if state.position_key.as_deref()!=Some(ctx.position_key.as_str()){*state=HoldingRiskState{position_key:Some(ctx.position_key.clone()),..Default::default()};}
    if state.last.is_some_and(|last|index<=last){return Err("holding close risk must advance once per date".into());}
    if ctx.pending_full_exit{state.last=Some(index);return Ok(RiskEvaluation::default());}
    let bars=&bars[..=index];let mut result=RiskEvaluation::default();state.staged.inverse_index=ctx.inverse_index;
    let inverse=if policy.inverse_n_close_reduce{exits::observe_inverse_resistance_exit(bars,index,&mut state.staged)}else{None};
    let mut volume=None;
    if policy.volume_down_exit{
        volume=observe_volume_risk_with_policy(bars,index,if policy.small_n_reduction{ctx.positive_n_index}else{None},&mut state.volume,policy.small_body_max_fraction,policy.small_body_lookback);
        if let Some(pattern)=observe_bearish_child_pattern(bars,index,&mut state.volume){if volume.as_ref().is_none_or(|d|pattern.fraction>d.fraction){volume=Some(pattern);}}
    }
    if ctx.entry_index<index{if let Some(shadow)=exits::volume_shadow_next_decline(bars,index,None){volume=Some(shadow);}}
    if policy.volume_down_exit{if let Some(fade)=exits::observe_double_volume_fade(bars,index){if volume.as_ref().is_none_or(|d|fade.fraction>d.fraction){volume=Some(fade);}}}
    let mut wave=if policy.wave_exhaustion_exit&&ctx.trading_eligible{observe_wave_exhaustion(bars,index,&ctx.wave_events,&policy.risk,ctx.wave_reduced,Some(ctx.entry_index),Some(ctx.signal_index))}else{None};
    let c_equal=if policy.wave_exhaustion_exit&&ctx.trading_eligible{observe_c_equal_near_risk(bars,index,&ctx.c_equal_events,policy.risk.wave_exhaustion_reduction)}else{None};
    for target in [ctx.target_risk.clone(),c_equal].into_iter().flatten(){
        if target.fraction==Decimal::ONE&&wave.as_ref().is_none_or(|w|w.fraction<Decimal::ONE||five_shadow(w))||wave.is_none()&&!ctx.wave_reduced{wave=Some(target);}
    }
    let permitted:Vec<_>=global_decisions.iter().filter(|d|pressure_allowed(d,ctx)).collect();
    let trend=if policy.trend_flip_adverse_exit{permitted.iter().find(|d|d.reason.starts_with("trend_")||d.reason.starts_with("breakout_")).map(|d|(**d).clone())}else{None};
    let supply=if policy.pressure_adverse_exit{permitted.iter().find(|d|d.reason.starts_with("pressure_")).map(|d|(**d).clone())}else{None};
    let record=if policy.pressure_adverse_exit{permitted.iter().find(|d|d.reason=="record_high_resistance_reduce").map(|d|(**d).clone())}else{None};
    let mut pressure=trend.or(supply).or(record);
    if policy.pressure_adverse_exit{if let Some(massive)=permitted.iter().find(|d|d.reason.starts_with("record_high_massive_")){if pressure.as_ref().is_none_or(|d|massive.fraction>d.fraction){pressure=Some((**massive).clone());}}}
    if index>0&&bars[index].close<bars[index-1].close{
        if let Some(proof)=&ctx.pressure_warning{let mut d=decision(bars,index,"pressure_reduced_lower_close_clear",Decimal::ONE,false);d.evidence.extend(proof.clone());pressure=Some(d);}
        else if let Some(proof)=&ctx.record_high_warning{let mut d=decision(bars,index,"record_high_lower_close_clear",Decimal::ONE,false);d.evidence.extend(proof.clone());
            let start=proof.get("record_breakout_index").and_then(Value::as_u64).and_then(|v|usize::try_from(v).ok()).unwrap_or(index);
            let dates:Vec<_>=(start.max(1)..index).filter(|&j|!resistance(&bars[j-1],&bars[j],ratio(1,2),true).is_empty()).map(|j|&bars[j].timestamp).collect();
            pressure=Some(d.with("record_resistance_dates",dates));}
    }else if (ctx.pressure_warning.is_some()||ctx.record_high_warning.is_some())&&pressure.as_ref().is_some_and(|d|d.fraction<Decimal::ONE){pressure=None;}
    result.decisions.extend(pressure.map(|d|family(d,"pressure")));result.decisions.extend(wave.map(|d|family(d,"wave")));
    result.decisions.extend(volume.map(|d|family(d,"volume")));result.decisions.extend(inverse.map(|d|family(d,"inverse")));
    if resolve_exit_precedence(&result.decisions,ctx)?.is_none()&&policy.staged_exit_enabled
        &&(ctx.daily_fallback||!policy.staged_exit_intraday||state.staged.breakdown_index.is_some()){
        let tiered=ctx.daily_fallback||(policy.staged_exit_same_day&&!policy.staged_exit_intraday);
        if let Some(d)=exits::observe_staged_exit(bars,index,&mut state.staged,policy.initial_reduction_fraction,tiered)?{
            result.decisions.push(family(d.with("execution_model",if policy.staged_exit_same_day||ctx.daily_fallback{"same_day_close"}else{"next_open"}),"staged"));}
    }
    state.last=Some(index);Ok(result)
}

/// 日开入口仅消费昨日已发布的突破警告和今日开盘；不读取日内高低/收盘/成交量。
pub fn observe_open_breakout_risk(state:&RiskState,index:usize,open:Decimal,entry_index:usize)->Option<RuleDecision>{
    let (warn,proof)=state.breakout.warning.as_ref()?;if index!=warn+1||entry_index>*warn||open>=proof_price(proof,"breakout_warning_close")?{return None;}
    let mut d=proof.clone();d.bar_index=index;d.reason="breakout_volume_bearish_shadow_next_lower_open_clear".into();d.fraction=Decimal::ONE;d.cumulative=false;
    for key in ["observed_open","observed_high","observed_low","observed_close","observed_volume"]{d.evidence.remove(key);}
    Some(d.with("execution_model","next_open").with("observed_open",open).with("previous_close",proof_price(proof,"breakout_warning_close")))
}
