//! 空翻多回调中的单个正 N。只消费新增折线点和当日 K 线，不枚举窗口候选。
use std::collections::VecDeque;
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::analysis::{kernel::{KernelPoint,PointKind},market_state::{regime::MarketRegime,price_action::{ShadowPolicy,observe_resistance},strong_bullish_candle}};
use super::{RuleCandle,validate_bars,n_shape::{project_n_targets,ValueDomain},Direction};

#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct NPointRef {pub path:usize,pub source_index:usize,pub bar_index:usize,pub ordinal:usize,pub known_index:usize}
impl NPointRef {fn new(source_index:usize,p:&KernelPoint)->Self{Self{path:p.path,source_index,bar_index:p.index,ordinal:p.ordinal,known_index:p.known_index}}}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct SingleNData {
    pub a:NPointRef,pub b:NPointRef,pub c:NPointRef,pub origin:Decimal,pub neckline:Decimal,
    pub neckline_close:Decimal,pub pullback:Decimal,pub known_index:usize,
    pub attack_index:Option<usize>,pub defense:Option<Decimal>,pub box_anchor:Option<Decimal>,
    pub squeeze_index:Option<usize>,pub regime:Option<MarketRegime>,pub targets:Option<SingleNTargets>,
}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct SingleNTargets {pub one_p:Decimal,pub two_t:Decimal,pub five_top:Option<Decimal>,pub ten_full:Option<Decimal>,pub stage:String,pub next_target:Option<Decimal>,pub one_p_reached_at:Option<usize>,pub two_t_reached_at:Option<usize>}
#[derive(Debug,Clone,Default)]
pub struct SingleNState {
    start:usize,floor:usize,cursor:usize,tail:VecDeque<(usize,KernelPoint)>,last:Option<usize>,previous:Option<RuleCandle>,
    stage_defense:Option<Decimal>,pub candidate:Option<SingleNData>,pub evaluated_days:usize,pub formations:usize,pub invalidations:usize,
}
impl SingleNState {
    pub fn new(start:usize)->Self{Self{start,floor:start.saturating_add(1),..Self::default()}}
    pub fn restart(&mut self,start:usize){*self=Self::new(start);}
    pub fn retire(&mut self,now:usize){self.candidate=None;self.stage_defense=None;self.tail.clear();self.floor=now;self.invalidations+=1;}
}
#[derive(Debug,Clone)]
pub struct SingleNStep {pub data:Option<SingleNData>,pub invalidated:bool,pub formed:bool}
fn price(p:&KernelPoint)->Result<Decimal,String>{p.value.to_string().parse().map_err(|_|"N point price outside decimal range".into())}
/// source_close 只在新候选冻结颈线时按下标读取一次；不接受或复制历史行情数组。
pub fn advance_n(state:&mut SingleNState,points:&[KernelPoint],bar:&RuleCandle,now:usize,previous_bar:Option<&RuleCandle>,
    source_close:impl Fn(usize)->Option<Decimal>)->Result<SingleNStep,String>{
    #[cfg(feature="qualification-timing")]
    let _profile=crate::analysis::kernel::qualification_profile::enter("single_n_step");
    if state.last.is_some_and(|last|now<=last){return Err("single N requires increasing observation dates".into());}
    validate_bars(std::slice::from_ref(bar))?;
    if state.previous.is_none(){state.previous=previous_bar.cloned();}
    if state.last.is_none(){state.cursor=points.partition_point(|p|p.index<=state.start);}
    state.evaluated_days+=1;state.last=Some(now);
    let mut invalidated=false;let mut formed=false;
    if let Some(n)=&state.candidate {
        let unchanged=[(&n.a,n.origin),(&n.b,n.neckline),(&n.c,n.pullback)].iter().all(|(r,value)|points.get(r.source_index).is_some_and(|p|
            p.path==r.path&&p.index==r.bar_index&&p.ordinal==r.ordinal&&p.known_index==r.known_index&&price(p).ok()==Some(*value)));
        if !unchanged{state.retire(now);state.cursor=0;invalidated=true;}
    }
    if state.tail.iter().any(|(index,old)|points.get(*index).is_none_or(|p|
        p.order()!=old.order()||p.kind!=old.kind||p.value!=old.value||p.known_index!=old.known_index||p.path!=old.path)){
        state.tail.clear();state.cursor=0;state.floor=now;
    }
    if state.cursor>points.len(){state.cursor=0;state.tail.clear();state.floor=now;}
    if let Some(n)=&state.candidate {
        if bar.low<n.defense.unwrap_or(n.pullback){state.retire(now);invalidated=true;}
    }
    if !invalidated&&state.candidate.is_none(){
        while state.cursor<points.len(){
            let p=&points[state.cursor];if p.known_index>now{break;}state.cursor+=1;
            if p.index<=state.start||p.index<state.floor||!p.is_closed(){continue;}
            state.tail.push_back((state.cursor-1,p.clone()));while state.tail.len()>3{state.tail.pop_front();}
            if state.tail.len()!=3{continue;}
            let (a,b,c)=(&state.tail[0].1,&state.tail[1].1,&state.tail[2].1);
            if [a.kind,b.kind,c.kind]!=[PointKind::L,PointKind::H,PointKind::L]
                ||!(a.order()<b.order()&&b.order()<c.order())||a.path!=b.path||b.path!=c.path{continue;}
            let (low,high,pullback)=(price(a)?,price(b)?,price(c)?);
            if !(low<pullback&&pullback<high){continue;}
            let close=source_close(b.index).ok_or("N neckline bar unavailable")?;
            if close<=Decimal::ZERO||close>high{return Err("N neckline close outside bar range".into());}
            state.candidate=Some(SingleNData{a:NPointRef::new(state.tail[0].0,a),b:NPointRef::new(state.tail[1].0,b),c:NPointRef::new(state.tail[2].0,c),origin:low,neckline:high,neckline_close:close,
                pullback,known_index:a.known_index.max(b.known_index).max(c.known_index),attack_index:None,defense:None,box_anchor:None,
                squeeze_index:None,regime:None,targets:None});state.tail.clear();break;
        }
    }
    if let Some(n)=&mut state.candidate{
        if n.attack_index.is_none(){
            if bar.low<n.pullback{state.retire(now);invalidated=true;}
            else if let Some(previous)=&state.previous{
                let first=bar.high>=n.neckline||bar.close>n.neckline_close;
                if state.stage_defense.is_none()&&first{state.stage_defense=Some(previous.close.min(bar.low));}
                else if let Some(defense)=&mut state.stage_defense{*defense=(*defense).min(bar.low);}
                let jointly_above=bar.high>n.neckline&&bar.close>n.neckline_close;
                let previous_joint=previous.high>n.neckline&&previous.close>n.neckline_close;
                if jointly_above&&!previous_joint&&now>=n.known_index{
                    n.attack_index=Some(now);n.defense=Some(state.stage_defense.unwrap_or(previous.close.min(bar.low)));
                    n.box_anchor=Some(previous.close.max(bar.high));formed=true;state.formations+=1;
                }
            }
        }
    }
    state.previous=Some(bar.clone());Ok(SingleNStep{data:state.candidate.clone(),invalidated,formed})
}

#[derive(Debug,Clone,Default)]
pub struct SqueezeState {
    attack:Option<usize>,attack_bar:Option<RuleCandle>,previous:Option<RuleCandle>,before:Option<RuleCandle>,
    last:Option<usize>,record:Option<Decimal>,record_index:usize,first_resistance:Option<usize>,unknown:bool,
    uninterrupted:bool,rolling_all:bool,prior_resistance:bool,prior_index:Option<usize>,projection:Option<super::waves::IncrementalProjection>,
}
/// 使用冻结 N 和当日行情推进轧空；严格跌破轧空低时调用方撤销候选，不能放量挽回旧 N。
pub fn advance_squeeze(state:&mut SqueezeState,n:&mut SingleNData,bar:&RuleCandle,previous_bar:&RuleCandle,now:usize,shadow_fraction:Decimal)->Result<bool,String>{
    #[cfg(feature="qualification-timing")]
    let _profile=crate::analysis::kernel::qualification_profile::enter("single_squeeze_step");
    let (Some(attack),Some(defense))=(n.attack_index,n.defense)else{return Ok(false);};
    if state.attack!=Some(attack){
        let resistance=observe_resistance(previous_bar,bar,Direction::Up,Some(ShadowPolicy{minimum_range_fraction:shadow_fraction}))?;
        *state=SelfSqueeze::initial(attack,bar,previous_bar,resistance.detected);
        return Ok(false);
    }
    if state.last.is_some_and(|last|now<=last){return Err("squeeze requires increasing market dates".into());}
    if bar.low<defense{return Ok(false);}
    let previous=state.previous.as_ref().ok_or("squeeze previous bar missing")?;
    let before=state.before.as_ref().unwrap_or(previous);
    let attack_bar=state.attack_bar.as_ref().ok_or("squeeze attack missing")?;
    let resistance=observe_resistance(previous,bar,Direction::Up,Some(ShadowPolicy{minimum_range_fraction:shadow_fraction}))?;
    let eligible=now==attack+1;
    if eligible{if resistance.detected==Some(true)&&state.first_resistance.is_none(){state.first_resistance=Some(now);}state.unknown|=resistance.detected.is_none();}
    let rolling=previous.low.min(before.close);let held=bar.low>=rolling;
    state.rolling_all&=held;state.uninterrupted&=bar.close>previous.close;
    let record=state.record.unwrap_or(attack_bar.high);let mut continuation=bar.close>record;
    if now>=attack+2&&state.first_resistance.is_some(){
        let attack_break=bar.high>attack_bar.high&&bar.close>attack_bar.close&&strong_bullish_candle(bar);
        let response=state.rolling_all&&held&&state.prior_index.is_some_and(|i|i<=attack+1)&&state.prior_resistance;
        continuation=(continuation||attack_break||response)&&bar.close>previous.close&&bar.close>attack_bar.close&&bar.close>bar.open&&(!eligible||resistance.detected==Some(false));
    }
    let regime=if now>=attack+2&&continuation {
        if state.first_resistance.is_none()&&!state.unknown&&state.uninterrupted&&state.rolling_all{Some(MarketRegime::StrongBull)}
        else if state.first_resistance.is_some_and(|i|i<now){Some(MarketRegime::Bull)}else{None}
    }else{None};
    let newly_confirmed=n.squeeze_index.is_none()&&regime.is_some();
    if let Some(regime)=regime{
        n.regime=Some(regime);
        if n.squeeze_index.is_none(){
            n.squeeze_index=Some(now);
            let box_anchor=n.box_anchor.ok_or("N box anchor missing")?;
            let targets=project_n_targets(n.origin,n.neckline,n.pullback,box_anchor,Direction::Up,ValueDomain::Price,false)?;
            let (one_p,two_t)=(targets.one_p.ok_or("one P unavailable")?,targets.two_t.ok_or("two T unavailable")?);
            n.targets=Some(SingleNTargets{one_p,two_t,five_top:None,ten_full:None,stage:"await_one_p".into(),next_target:Some(one_p),one_p_reached_at:None,two_t_reached_at:None});
            state.projection=Some(super::waves::IncrementalProjection::new(super::waves::WaveProjectionSetup{
                origin_index:n.a.bar_index,attack_index:attack,squeeze_index:now,origin:n.origin,box_anchor,two_t,defense},bar,now,record.max(bar.high),if bar.high>record{now}else{state.record_index})?);
        }
    }
    if let (Some(projection),Some(targets))=(&mut state.projection,&mut n.targets){
        let update=projection.advance(bar,previous,now)?;
        let observed=if n.squeeze_index==Some(now){bar.close}else{bar.high};
        if targets.one_p_reached_at.is_none()&&observed>=targets.one_p{targets.one_p_reached_at=Some(now);}
        if targets.two_t_reached_at.is_none()&&observed>=targets.two_t{targets.two_t_reached_at=Some(now);}
        targets.stage=update.stage;targets.next_target=update.target;
        if targets.one_p_reached_at.is_none(){targets.stage="await_one_p".into();targets.next_target=Some(targets.one_p);}
        else if targets.two_t_reached_at.is_none(){targets.stage="await_two_t".into();targets.next_target=Some(targets.two_t);}
        if let Some(price)=update.five_top{targets.five_top=Some(price);}if let Some(price)=update.ten_full{targets.ten_full=Some(price);}
    }
    if bar.high>record{state.record_index=now;}state.record=Some(record.max(bar.high));state.prior_resistance=resistance.detected==Some(true);state.prior_index=Some(now);
    state.before=state.previous.take();state.previous=Some(bar.clone());state.last=Some(now);Ok(newly_confirmed)
}
// 单独构造初始状态，避免向外暴露历史行情容器。
struct SelfSqueeze;
impl SelfSqueeze {fn initial(attack:usize,bar:&RuleCandle,previous:&RuleCandle,detected:Option<bool>)->SqueezeState{
    SqueezeState{attack:Some(attack),attack_bar:Some(bar.clone()),previous:Some(bar.clone()),before:Some(previous.clone()),last:Some(attack),record:Some(bar.high),record_index:attack,
        first_resistance:(detected==Some(true)).then_some(attack),unknown:detected.is_none(),uninterrupted:true,rolling_all:true,
        prior_resistance:detected==Some(true),prior_index:Some(attack),projection:None}
}}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::kernel::{KernelPointData,PointState};
    use crate::indexed_json::Value;
    fn bar(day:usize,open:&str,high:&str,low:&str,close:&str)->RuleCandle{
        RuleCandle{symbol:"test".into(),timestamp:format!("2026-01-{day:02}"),open:open.parse().unwrap(),high:high.parse().unwrap(),
            low:low.parse().unwrap(),close:close.parse().unwrap(),volume:Decimal::from(100),adjustment_factor:Decimal::ONE,raw_is_st:None,raw_trading_active:None}
    }
    fn points()->Vec<KernelPoint>{[(1,PointKind::L,10.0),(2,PointKind::H,15.0),(3,PointKind::L,12.0)].into_iter().map(|(index,kind,value)|
        KernelPoint::new(KernelPointData{index,kind,value,ordinal:0,known_index:index,level:0,path:0,state:PointState::Confirmed,
            time:format!("2026-01-{index:02}"),evidence:Value::Null})).collect()}
    fn formed()->(SingleNState,Vec<KernelPoint>){
        let points=points();let mut state=SingleNState::new(0);
        let prev=bar(2,"14","15","12","14.5");let current=bar(3,"13","14","12","13");
        advance_n(&mut state,&points,&current,3,Some(&prev),|_|Some(Decimal::new(145,1))).unwrap();
        let attack=bar(4,"14","16","13.5","15.5");
        assert!(advance_n(&mut state,&points,&attack,4,Some(&current),|_|Some(Decimal::new(145,1))).unwrap().formed);
        (state,points)
    }
    #[test]
    fn frozen_defense_touch_survives_strict_break_retires(){
        let(mut state,points)=formed();let defense=state.candidate.as_ref().unwrap().defense.unwrap();
        assert_eq!(defense,Decimal::from(13));
        let touch=bar(5,"15","16","13","15");
        assert!(!advance_n(&mut state,&points,&touch,5,None,|_|None).unwrap().invalidated);
        let broken=bar(6,"14","15","12.9","14");
        assert!(advance_n(&mut state,&points,&broken,6,None,|_|None).unwrap().invalidated);
        assert!(state.candidate.is_none());
    }
    #[test]
    fn same_day_cannot_evaluate_a_second_n(){
        let(mut state,points)=formed();let current=bar(4,"14","16","13.5","15.5");
        assert!(advance_n(&mut state,&points,&current,4,None,|_|None).is_err());
        assert_eq!(state.evaluated_days,2);
    }
    #[test]
    fn source_revision_retires_without_reusing_old_candidate(){
        let(mut state,mut points)=formed();points[2].value=12.1;
        let current=bar(5,"15","16","13.5","15");
        assert!(advance_n(&mut state,&points,&current,5,None,|_|None).unwrap().invalidated);
        assert!(state.candidate.is_none());
    }
    #[test]
    fn new_points_do_not_replace_a_live_n(){
        let(mut state,mut points)=formed();let first=state.candidate.as_ref().unwrap().a.bar_index;
        let mut p=points[1].clone();p.index=4;p.known_index=4;p.value=16.0;points.push(p);
        let mut p=points[2].clone();p.index=5;p.known_index=5;p.value=14.0;points.push(p);
        let current=bar(5,"15","16","14","15");
        advance_n(&mut state,&points,&current,5,None,|_|None).unwrap();
        assert_eq!(state.candidate.as_ref().unwrap().a.bar_index,first);
        assert_eq!(state.formations,1);
    }
}
