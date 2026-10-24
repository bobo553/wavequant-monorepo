//! 原研究基线与 N 形策略。信号仅消费当时已收盘行情，配置复用执行事实源。
use std::collections::{BTreeMap,BTreeSet,VecDeque};
use chrono::{Datelike,NaiveDate};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use super::{profiles::ExecutionParameters,trading::{RuleCandle,validate_bars}};
type Result<T>=std::result::Result<T,String>;
pub type StrategyConfig=ExecutionParameters;
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct Feature {
    pub bar:RuleCandle,pub index:usize,pub previous_high:Option<Decimal>,pub previous_low:Option<Decimal>,
    pub atr:Option<Decimal>,pub rvol:Option<Decimal>,pub close_location:Decimal,pub efficiency:Option<Decimal>,
    pub move_in_atr:Option<Decimal>,pub regime:String,
}
#[derive(Debug,Clone,Serialize,Deserialize,PartialEq)]
pub struct Signal {
    pub timestamp:String,pub symbol:String,pub bar_index:usize,pub side:String,pub reference_price:Decimal,
    pub invalidation_price:Decimal,pub reason:String,pub trigger_timestamp:String,pub retracement:Decimal,
    pub rvol:Option<Decimal>,pub regime:String,pub target_price:Option<Decimal>,pub minimum_reward_risk:Decimal,
}
fn mean(values:impl Iterator<Item=Decimal>,len:usize)->Decimal{values.sum::<Decimal>()/Decimal::from(len as u64)}
fn median(values:impl Iterator<Item=Decimal>)->Option<Decimal>{let mut v:Vec<_>=values.collect();if v.is_empty(){return None;}v.sort();Some(if v.len()%2==0{(v[v.len()/2-1]+v[v.len()/2])/Decimal::TWO}else{v[v.len()/2]})}
fn slot(stamp:&str)->String{stamp.split_once('T').or_else(||stamp.split_once(' ')).map_or("00:00:00",|(_,time)|time.split(['+','Z']).next().unwrap_or(time)).to_string()}
pub fn compute_features(bars:&[RuleCandle],config:&StrategyConfig)->Result<Vec<Feature>>{
    validate_bars(bars)?;config.validate()?;let mut output=Vec::with_capacity(bars.len());let mut ranges=VecDeque::new();let mut volumes=BTreeMap::<String,VecDeque<Decimal>>::new();
    for(i,bar)in bars.iter().enumerate(){let previous=if i>0{bars[i-1].close}else{bar.close};let tr=(bar.high-bar.low).max((bar.high-previous).abs()).max((bar.low-previous).abs());
        ranges.push_back(tr);if ranges.len()>config.atr_period{ranges.pop_front();}let atr=(ranges.len()==config.atr_period).then(||mean(ranges.iter().copied(),ranges.len()));
        let history=volumes.entry(slot(&bar.timestamp)).or_default();let rvol=if history.len()==config.rvol_lookback{median(history.iter().copied()).filter(|v|*v>Decimal::ZERO).map(|v|bar.volume/v)}else{None};history.push_back(bar.volume);if history.len()>config.rvol_lookback{history.pop_front();}
        let prior=if i>=config.breakout_lookback{Some(&bars[i-config.breakout_lookback..i])}else{None};let span=bar.high-bar.low;
        let mut efficiency=None;let mut move_in_atr=None;let mut regime="unknown";
        if i>=config.regime_lookback{if let Some(atr)=atr.filter(|v|*v>Decimal::ZERO){let start=i-config.regime_lookback;let movement=bar.close-bars[start].close;let path=(start+1..=i).map(|j|(bars[j].close-bars[j-1].close).abs()).sum::<Decimal>();let er=if path>Decimal::ZERO{movement.abs()/path}else{Decimal::ZERO};let movement=movement/atr;efficiency=Some(er);move_in_atr=Some(movement);
            regime=if er<config.regime_min_efficiency{"range"}else if movement>=config.regime_strong_move_atr{"squeeze_up"}else if movement>Decimal::ZERO{"drift_up"}else if movement<=-config.regime_strong_move_atr{"chase_down"}else{"drift_down"};}}
        output.push(Feature{bar:bar.clone(),index:i,previous_high:prior.and_then(|v|v.iter().map(|b|b.high).max()),previous_low:prior.and_then(|v|v.iter().map(|b|b.low).min()),atr,rvol,close_location:if span>Decimal::ZERO{(bar.close-bar.low)/span}else{Decimal::new(5,1)},efficiency,move_in_atr,regime:regime.into()});
    }Ok(output)
}
#[derive(Clone)]
struct Pending{trigger:usize,breakout:Decimal,impulse:Decimal,extreme:Decimal,counter:Decimal,max_counter:Decimal,timestamp:String,rvol:Option<Decimal>,regime:String}
fn signal(feature:&Feature,side:&str,stop:Decimal,reason:&str,trigger:&str,retracement:Decimal,rvol:Option<Decimal>,regime:&str)->Signal{
    Signal{timestamp:feature.bar.timestamp.clone(),symbol:feature.bar.symbol.clone(),bar_index:feature.index,side:side.into(),reference_price:feature.bar.close,invalidation_price:stop,reason:reason.into(),trigger_timestamp:trigger.into(),retracement,rvol,regime:regime.into(),target_price:None,minimum_reward_risk:Decimal::ZERO}
}
pub fn generate_signals(features:&[Feature],config:&StrategyConfig)->Result<Vec<Signal>>{
    config.validate()?;let mut bullish:Option<Pending>=None;let mut bearish:Option<Pending>=None;let mut signals=Vec::new();let buffer=config.breakout_buffer_bps/Decimal::from(10000);
    for(i,f)in features.iter().enumerate(){if f.index!=i{return Err("feature indices must be contiguous".into());}let bar=&f.bar;
        if let Some(p)=bullish.as_mut(){let peak=p.extreme;p.max_counter=p.max_counter.max((p.extreme-bar.low).max(Decimal::ZERO)/p.impulse);p.counter=p.counter.min(bar.low);p.extreme=p.extreme.max(bar.high);let age=i-p.trigger;let expired=age>config.confirm_window||p.max_counter>config.max_retracement;let invalid=p.counter<=p.breakout;
            if age>=config.min_confirm_bars&&bar.close>peak*(Decimal::ONE+buffer)&&!expired&&!invalid{signals.push(signal(f,"LONG",p.counter-f.atr.unwrap_or_default()*config.stop_atr_buffer,"breakout_then_shallow_retrace_then_new_high",&p.timestamp,p.max_counter,p.rvol,&p.regime));bullish=None;}else if expired||invalid{bullish=None;}}
        if let Some(p)=bearish.as_mut(){let trough=p.extreme;p.max_counter=p.max_counter.max((bar.high-p.extreme).max(Decimal::ZERO)/p.impulse);p.counter=p.counter.max(bar.high);p.extreme=p.extreme.min(bar.low);let age=i-p.trigger;let expired=age>config.confirm_window||p.max_counter>config.max_retracement;let invalid=p.counter>=p.breakout;
            if age>=config.min_confirm_bars&&bar.close<trough*(Decimal::ONE-buffer)&&!expired&&!invalid{signals.push(signal(f,"EXIT",p.counter+f.atr.unwrap_or_default()*config.stop_atr_buffer,"breakdown_then_weak_rebound_then_new_low",&p.timestamp,p.max_counter,p.rvol,&p.regime));bearish=None;}else if expired||invalid{bearish=None;}}
        let(Some(high),Some(low),Some(_),Some(rvol))=(f.previous_high,f.previous_low,f.atr,f.rvol)else{continue;};if i<config.impulse_lookback||i<config.regime_lookback{continue;}let recent=&features[i-config.impulse_lookback..i];
        if bullish.is_none()&&(!config.require_non_bearish_regime||matches!(f.regime.as_str(),"range"|"drift_up"|"squeeze_up"))&&bar.close>high*(Decimal::ONE+buffer)&&f.close_location>=config.min_close_location&&rvol>=config.min_rvol{let anchor=recent.iter().map(|v|v.bar.low).min().unwrap_or(low);let impulse=bar.close-anchor;if impulse>Decimal::ZERO{bullish=Some(Pending{trigger:i,breakout:anchor,impulse,extreme:bar.high,counter:bar.low,max_counter:Decimal::ZERO,timestamp:bar.timestamp.clone(),rvol:f.rvol,regime:f.regime.clone()});}}
        if bearish.is_none()&&bar.close<low*(Decimal::ONE-buffer)&&f.close_location<=Decimal::ONE-config.min_close_location&&rvol>=config.min_rvol{let anchor=recent.iter().map(|v|v.bar.high).max().unwrap_or(high);let impulse=anchor-bar.close;if impulse>Decimal::ZERO{bearish=Some(Pending{trigger:i,breakout:anchor,impulse,extreme:bar.low,counter:bar.high,max_counter:Decimal::ZERO,timestamp:bar.timestamp.clone(),rvol:f.rvol,regime:f.regime.clone()});}}
    }Ok(signals)
}
pub fn breakout_signals(features:&[Feature],config:&StrategyConfig)->Result<Vec<Signal>>{
    config.validate()?;let warmup=config.breakout_lookback.max(config.impulse_lookback).max(config.regime_lookback);let buffer=config.breakout_buffer_bps/Decimal::from(10000);let mut out=Vec::new();
    for f in features{if f.index<warmup{continue;}let(Some(high),Some(low),Some(atr),Some(_))=(f.previous_high,f.previous_low,f.atr,f.rvol)else{continue;};
        let pair=if f.bar.close>high*(Decimal::ONE+buffer){Some(("LONG",f.bar.low-atr*config.stop_atr_buffer,"plain_breakout"))}else if f.bar.close<low*(Decimal::ONE-buffer){Some(("EXIT",f.bar.high+atr*config.stop_atr_buffer,"plain_breakdown"))}else{None};
        if let Some((side,stop,reason))=pair{out.push(signal(f,side,stop,reason,&f.bar.timestamp,Decimal::ZERO,f.rvol,&f.regime));}}Ok(out)
}
pub fn make_signals(grouped:&BTreeMap<String,Vec<RuleCandle>>,config:&StrategyConfig,plain:bool)->Result<Vec<Signal>>{let mut out=Vec::new();for bars in grouped.values(){let f=compute_features(bars,config)?;out.extend(if plain{breakout_signals(&f,config)?}else{generate_signals(&f,config)?});}out.sort_by(|a,b|(&a.timestamp,&a.symbol,&a.side).cmp(&(&b.timestamp,&b.symbol,&b.side)));Ok(out)}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(default,deny_unknown_fields)]
pub struct StructureConfig{pub pivot_width:usize,pub max_pattern_bars:usize,pub confirmation_bars:usize,pub max_retracement:Decimal,pub target_mode:String,pub volume_filter:bool,pub ma66_filter:bool,pub weekly_filter:bool,pub p_filter:bool,pub minimum_reward_risk:Decimal}
impl Default for StructureConfig{fn default()->Self{Self{pivot_width:2,max_pattern_bars:66,confirmation_bars:5,max_retracement:Decimal::new(5,1),target_mode:"risk".into(),volume_filter:false,ma66_filter:false,weekly_filter:false,p_filter:false,minimum_reward_risk:Decimal::ZERO}}}
impl StructureConfig{pub fn validate(&self)->Result<()>{if [self.pivot_width,self.max_pattern_bars,self.confirmation_bars].contains(&0)||self.max_retracement<=Decimal::ZERO||self.max_retracement>=Decimal::ONE||!matches!(self.target_mode.as_str(),"risk"|"tide"|"burst")||self.minimum_reward_risk<Decimal::ZERO||(self.target_mode=="risk"&&self.minimum_reward_risk>Decimal::ZERO){return Err("invalid structure configuration".into());}Ok(())}}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct CandleEvidence{pub close_location:Decimal,pub p_value:Option<Decimal>,pub p_unbounded:bool,pub burst:bool,pub burst_target:Decimal,pub body_red:bool,pub trend_red:bool}
pub fn candle_evidence(bar:&RuleCandle,previous:Option<&RuleCandle>)->CandleEvidence{let span=bar.high-bar.low;let denominator=bar.high-bar.close;let location=if span>Decimal::ZERO{(bar.close-bar.low)/span}else{Decimal::new(5,1)};CandleEvidence{close_location:location,p_value:(denominator>Decimal::ZERO).then(||(bar.close-bar.low)/denominator),p_unbounded:span>Decimal::ZERO&&denominator==Decimal::ZERO,burst:span>Decimal::ZERO&&location>Decimal::new(8,1),burst_target:Decimal::TWO*bar.close-bar.low,body_red:bar.close>bar.open,trend_red:previous.is_some_and(|b|bar.close>b.close)}}
pub fn volume_votes(bars:&[RuleCandle],index:usize)->usize{if index<22||index>=bars.len(){return 0;}let long=mean(bars[index-21..=index].iter().map(|b|b.volume),22);let short=mean(bars[index-4..=index].iter().map(|b|b.volume),5);let previous=mean(bars[index-22..index].iter().map(|b|b.volume),22);[bars[index].volume>long,short>long,long>previous].into_iter().filter(|v|*v).count()}
pub fn completed_weekly_context(bars:&[RuleCandle])->Result<Vec<bool>>{
    let mut weeks=Vec::new();let mut active=None;let mut initial=None;let mut last_close=Decimal::ZERO;let mut flags=Vec::new();
    for b in bars{let date=NaiveDate::parse_from_str(b.timestamp.get(..10).ok_or("timestamp too short")?,"%Y-%m-%d").map_err(|e|e.to_string())?;let iso=date.iso_week();let week=(iso.year(),iso.week());
        if active!=Some(week){if let Some(old)=active{if Some(old)!=initial{weeks.push(last_close);}}if initial.is_none(){initial=Some(week);}active=Some(week);}last_close=b.close;
        let len=weeks.len();flags.push(len>=27&&mean(weeks[len-13..].iter().copied(),13)>mean(weeks[len-26..].iter().copied(),26)&&mean(weeks[len-26..].iter().copied(),26)>=mean(weeks[len-27..len-1].iter().copied(),26));
    }Ok(flags)
}
#[derive(Clone)]struct StructurePending{trigger:usize,neckline:Decimal,peak:Decimal,resistance:bool,stop:Decimal,target:Option<Decimal>,q:Decimal,rvol:Option<Decimal>}
pub fn structural_signals(bars:&[RuleCandle],config:&StructureConfig)->Result<Vec<Signal>>{
    validate_bars(bars)?;config.validate()?;if bars.windows(2).any(|p|p[0].timestamp.get(..10)==p[1].timestamp.get(..10)){return Err("structural strategy requires daily bars".into());}
    let weekly=if config.weekly_filter{completed_weekly_context(bars)?}else{vec![true;bars.len()]};let mut pivots=Vec::<(bool,usize,Decimal)>::new();let mut used=BTreeSet::new();let mut pending:Option<StructurePending>=None;let mut out=Vec::new();
    for(i,b)in bars.iter().enumerate(){if let Some(p)=pending.as_mut(){if i-p.trigger>config.confirmation_bars||b.low<p.neckline{pending=None;}else{let old=p.peak;p.resistance|=i>0&&(b.close<=bars[i-1].close||b.low<bars[i-1].low);if p.resistance&&b.close>old{out.push(Signal{timestamp:b.timestamp.clone(),symbol:b.symbol.clone(),bar_index:i,side:"LONG".into(),reference_price:b.close,invalidation_price:p.stop,reason:"notion_N_breakout_resistance_failed".into(),trigger_timestamp:bars[p.trigger].timestamp.clone(),retracement:p.q,rvol:p.rvol,regime:"structural_support_held".into(),target_price:p.target,minimum_reward_risk:config.minimum_reward_risk});pending=None;}else{p.peak=p.peak.max(b.high);}}}
        let width=config.pivot_width;if width<=i/2{let j=i-width;let neighbors=(j-width..=j+width).filter(|k|*k!=j);let low=neighbors.clone().all(|k|bars[j].low<bars[k].low);let high=neighbors.clone().all(|k|bars[j].high>bars[k].high);if low!=high{let point=(low,j,if low{bars[j].low}else{bars[j].high});if let Some(last)=pivots.last_mut(){if last.0==low{if(low&&point.2<last.2)||(!low&&point.2>last.2){*last=point;}}else{pivots.push(point);}}else{pivots.push(point);}if pivots.len()>3{pivots.remove(0);}}}
        if pending.is_some()||pivots.len()!=3||!pivots[0].0||pivots[1].0||!pivots[2].0{continue;}let (origin,peak,pullback)=(pivots[0],pivots[1],pivots[2]);let key=(origin.1,peak.1,pullback.1);let amplitude=peak.2-origin.2;if amplitude<=Decimal::ZERO||i-origin.1>config.max_pattern_bars||used.contains(&key){continue;}let q=(peak.2-pullback.2)/amplitude;if q<=Decimal::ZERO||q>=config.max_retracement||i==0||bars[i-1].close>peak.2||b.close<=peak.2{continue;}used.insert(key);
        if i<22{continue;}let evidence=candle_evidence(b,i.checked_sub(1).map(|j|&bars[j]));if config.p_filter&&!evidence.burst||config.volume_filter&&volume_votes(bars,i)<2||config.ma66_filter&&(i<66||b.close<=mean(bars[i-65..=i].iter().map(|v|v.close),66)||b.close<bars[i-66].close)||!weekly[i]{continue;}
        let target=match config.target_mode.as_str(){"tide"=>Some(Decimal::TWO*peak.2-origin.2),"burst"=>Some(evidence.burst_target),_=>None};let stop=pullback.2-Decimal::new(1,2)*b.adjustment_factor;if stop<=Decimal::ZERO{continue;}let rvol=median(bars[i-20..i].iter().map(|v|v.volume)).filter(|v|*v>Decimal::ZERO).map(|v|b.volume/v);
        pending=Some(StructurePending{trigger:i,neckline:peak.2,peak:b.high,resistance:false,stop,target,q,rvol});
    }Ok(out)
}