//! integrated_strategy 的四拐点转势候选。冻结原波结束日的背景与关键位，再消费显式次级折线。
use super::{RuleCandle,Direction,candidates::PreparedN,config::TradingDayConfig};
use crate::analysis::{kernel::{KernelFrame,KernelPoint,PointKind},market_state::{
    regime::MarketRegime,strength::{ConfirmedPivot,PivotKind,StrengthScale},price_action::{KeyLevel,LevelKind,AttackBasis},
    turn::{self,TurnSetup,TurnPolicy,TurnThreshold,MinorLine,RegimeContext}}};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},str::FromStr};

#[derive(Debug,Clone,Serialize,Deserialize)]
struct FrozenKeys{epoch:usize,support:Option<KeyLevel>,resistance:Option<KeyLevel>}
#[derive(Debug,Clone,Serialize,Deserialize)]
struct PendingTurn{setup:TurnSetup,line:MinorLine,epoch:usize,expires:usize,selected:usize,suspicion_reported:bool}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct TurnState{
    last:Option<usize>,seen:BTreeSet<[usize;4]>,keys:BTreeMap<usize,FrozenKeys>,
    backgrounds:BTreeMap<(usize,usize),MarketRegime>,pending:Vec<PendingTurn>,
}
fn pivot(point:&KernelPoint)->Option<ConfirmedPivot>{Some(ConfirmedPivot{index:point.index,confirmed_index:point.known_index,
    price:Decimal::from_str(&point.value.to_string()).ok()?,kind:if point.kind==PointKind::H{PivotKind::High}else{PivotKind::Low}})}
fn window_key(points:&[&KernelPoint],kind:PointKind,symbol:&str,start:usize,now:usize)->Option<KeyLevel>{
    let mut extreme:Option<usize>=None;
    for(i,p)in points.iter().enumerate().filter(|(_,p)|p.kind==kind){
        if extreme.is_none_or(|j|if kind==PointKind::H{p.value>points[j].value}else{p.value<points[j].value}){extreme=Some(i);}
    }
    let position=extreme?;let preceding=points[..position].iter().rev().find(|p|p.kind!=kind)?;
    Some(KeyLevel{symbol:symbol.into(),timeframe:"1d".into(),kind:if kind==PointKind::H{LevelKind::Support}else{LevelKind::Resistance},
        price:Decimal::from_str(&preceding.value.to_string()).ok()?,source_index:preceding.index,confirmed_index:now,
        source:format!("window_{start}_through_{now}_preceding_extreme")})
}
fn policy()->TurnPolicy{TurnPolicy{strength_scale:StrengthScale::ExactFractions,first_threshold:TurnThreshold::TwoThirds,
    second_threshold:TurnThreshold::TwoThirds,key_basis:AttackBasis::Close,line_basis:AttackBasis::Close}}
pub fn washout_reentry_events(bars:&[RuleCandle],now:usize,prepared:&[PreparedN],config:&TradingDayConfig,seen:&mut BTreeSet<String>)->Result<Vec<Value>,String>{
    use crate::analysis::market_state::washout::{observe_washout,WashoutStage};
    if now>=bars.len(){return Err("washout pairing requires a visible prefix".into());}
    let mut events=vec![];
    let mut candidates=prepared.iter().filter(|n|n.known_at()<=now).collect::<Vec<_>>();candidates.sort_by_key(|n|n.attack());
    for new in &candidates{
        if !seen.insert(new.identity.clone()){continue;}
        let mut prior=candidates.iter().copied().filter(|old|old.epoch==new.epoch&&old.measurement.setup.direction==new.measurement.setup.direction
            &&old.attack()>=new.attack().saturating_sub(config.structure_window)&&old.attack()<new.attack().min(new.measurement.setup.origin.index)).collect::<Vec<_>>();
        prior.sort_by_key(|old|std::cmp::Reverse(old.attack()));
        for old in prior{
            let observed=observe_washout(bars,&old.measurement.setup,"1d",super::MilestoneBasis::Close,Some(&new.measurement.setup),new.attack());
            match observed{
                Err(error)=>events.push(json!({"event":"washout_pair_rejected","bar_index":now,"attack":new.attack(),"initial_attack":old.attack(),"reason":error})),
                Ok(observation)=>{if observation.frames.last().is_some_and(|f|f.stage==WashoutStage::Confirmed){
                    events.push(json!({"event":"washout_reattack","bar_index":now,"attack":new.attack(),"known_at":now,
                        "initial_attack":old.attack(),"direction":new.measurement.setup.direction,"n_id":new.identity,"observation":observation}));break;
                }}
            }
        }
    }Ok(events)
}
impl TurnState{
    pub fn advance(&mut self,bars:&[RuleCandle],now:usize,polyline:&KernelFrame,prepared:&[PreparedN],config:&TradingDayConfig,
        minor_points:Option<&[ConfirmedPivot]>)->Result<Vec<Value>,String>{
        if now>=bars.len()||self.last.map_or(0,|v|v+1)!=now{return Err("turn candidates require consecutive closed prefixes".into());}
        self.last=Some(now);let symbol=&bars[now].symbol;
        let epoch=polyline.candidate.as_ref().or_else(||polyline.points.last()).map_or(0,|p|p.path);
        let start=now.saturating_sub(config.structure_window);
        let points=polyline.points.iter().filter(|p|p.path==epoch&&p.is_closed()&&p.known_index<=now&&p.index>=start).collect::<Vec<_>>();
        self.keys.insert(now,FrozenKeys{epoch,support:window_key(&points,PointKind::H,symbol,start,now),resistance:window_key(&points,PointKind::L,symbol,start,now)});
        if let Some(regime)=prepared.iter().filter(|n|n.epoch==epoch).flat_map(|n|n.regime.frames.iter())
            .filter(|f|f.bar_index==now).find_map(|f|f.regime){self.backgrounds.entry((epoch,now)).or_insert(regime);}
        let mut output=vec![];let mut remaining=vec![];
        for mut pending in std::mem::take(&mut self.pending){
            if pending.epoch!=epoch||polyline.blocked_at==Some(now)||now>pending.expires{continue;}
            let result=match turn::observe_market_turn(bars,&pending.setup,&policy(),Some(&pending.line),now){Ok(result)=>result,Err(error)=>{
                output.push(json!({"event":"turn_setup_rejected","bar_index":now,"reason":error,"selected_at":pending.selected}));continue;
            }};
            if result.suspicion_index.is_some()&&!pending.suspicion_reported{
                output.push(json!({"event":"turn_observation","bar_index":now,"direction":result.direction,"stage":result.stage,
                    "suspicion_index":result.suspicion_index,"minor_line_available":true,"selected_at":pending.selected}));pending.suspicion_reported=true;
            }
            if let Some(line_break)=result.line_break{if line_break.bar_index==now{
                output.push(json!({"event":"turn_confirmed","bar_index":now,"direction":result.direction,"line_break":line_break,
                    "selected_at":pending.selected,"setup":pending.setup}));}continue;}
            if result.invalidated_index.is_none(){remaining.push(pending);}
        }
        self.pending=remaining;
        if polyline.blocked_at==Some(now)||points.len()<4{return Ok(output);}
        let quartet=&points[points.len()-4..];let identity=[quartet[0].index,quartet[1].index,quartet[2].index,quartet[3].index];
        if !self.seen.insert(identity){return Ok(output);}
        let direction=if quartet[0].kind==PointKind::H{Direction::Up}else{Direction::Down};
        let Some(((_,background_at),background))=self.backgrounds.range((epoch,0)..=(epoch,quartet[1].known_index)).next_back()else{
            output.push(json!({"event":"turn_background_unavailable","bar_index":now}));return Ok(output);
        };
        if matches!(background,MarketRegime::Bear|MarketRegime::StrongBear|MarketRegime::GrindDown)!=(direction==Direction::Up){
            output.push(json!({"event":"turn_background_opposite","bar_index":now}));return Ok(output);
        }
        let Some(keys)=self.keys.get(&quartet[1].known_index).filter(|keys|keys.epoch==epoch)else{return Ok(output);};
        let key=if direction==Direction::Up{&keys.resistance}else{&keys.support};
        let Some(key)=key else{output.push(json!({"event":"turn_key_unavailable","bar_index":now}));return Ok(output);};
        let converted=quartet.iter().map(|point|pivot(point).ok_or("turn pivot exceeds decimal representation")).collect::<Result<Vec<_>,_>>()?;
        let setup=TurnSetup{symbol:symbol.clone(),timeframe:"1d".into(),direction,origin:converted[0].clone(),impulse_end:converted[1].clone(),
            first_counter:converted[2].clone(),second_counter:converted[3].clone(),background:RegimeContext{symbol:symbol.clone(),timeframe:"1d".into(),
                regime:*background,asof_index:*background_at,source:"dated_six_regime_event".into()},trend_key:key.clone()};
        let line=match minor_points.map(|points|turn::freeze_minor_line(points,symbol,"1d",direction,now,"caller_supplied_minor_structure")).transpose(){
            Ok(line)=>line.flatten(),Err(error)=>{output.push(json!({"event":"turn_setup_rejected","bar_index":now,"reason":error}));return Ok(output);}
        };
        let result=match turn::observe_market_turn(bars,&setup,&policy(),line.as_ref(),now){Ok(result)=>result,Err(error)=>{
            output.push(json!({"event":"turn_setup_rejected","bar_index":now,"reason":error}));return Ok(output);
        }};
        if result.suspicion_index.is_some(){output.push(json!({"event":"turn_observation","bar_index":now,"direction":direction,
            "stage":result.stage,"suspicion_index":result.suspicion_index,"minor_line_available":line.is_some()}));}
        if let Some(line)=line{if result.invalidated_index.is_none(){self.pending.push(PendingTurn{setup,line,epoch,expires:now.saturating_add(config.pattern_ttl),selected:now,suspicion_reported:result.suspicion_index.is_some()});}}
        Ok(output)
    }
}
