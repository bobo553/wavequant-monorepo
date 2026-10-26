//! 结构函数的资格消费者：等待空翻多 → 确认空多交替结束 → 开启资格并判断资格结束。
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use crate::indexed_json::Value;
use super::{KernelBar, KernelFrame, KernelPoint, KernelPointData, KernelEvent, PointKind, TrendState,
    arena::{PointArena, PointId, RefFrame, EventProof, Fact}};

use crate::analysis::trading::{RuleCandle, config::TradingDayConfig, confirmation::{self, AlternationProof}, n_alternation};

/// 所有坐标属于独立行情数组。结束日不具备资格，同日确认/结束可有零长度区间。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all="camelCase")]
pub struct QualificationInterval {
    pub path: usize,
    pub start_index: usize,
    pub low_index: usize,
    pub end_index: Option<usize>,
    pub confirmation_rule: String,
    pub end_reason: Option<String>,
}
#[derive(Clone)]
struct ActiveQualification {
    interval: usize,
    low: KernelPoint,
    // 只有依赖缺口的资格才需要维护替换证据，不重复展开历史证明。
    gap: Option<Decimal>,
}
#[derive(Clone)]
enum QualificationPhase {
    // 空多交替的结束需独立确认；六态进入交替或多头本身不授予交易资格。
    AwaitingAlternationEnd { after: Option<usize> },
    Active(ActiveQualification),
}
#[derive(Clone)]
struct Search {
    origin: KernelPoint,
    high: KernelPoint,
    key: KernelPoint,
    started: usize,
    phase: QualificationPhase,
}
fn price(p: &KernelPoint) -> Result<Decimal, String> {
    p.value.to_string().parse().map_err(|_|"qualification price exceeds decimal range".into())
}
fn scalar(pool: &PointArena, bars: &[KernelBar], id: PointId) -> KernelPoint {
    let p=pool.view(id,bars);
    KernelPoint::new(KernelPointData{index:p.index,ordinal:p.ordinal,kind:p.kind,value:p.value,
        time:bars[p.index].time.clone(),known_index:p.known_index,level:p.level,path:p.path,state:p.state,evidence:Value::Null})
}
/// 为既有量价/N确认函数一次性提供标量点；不展开来源、证书或任何递归证明。
pub(crate) fn scalar_frame(pool:&PointArena,bars:&[KernelBar],frame:&RefFrame)->KernelFrame {
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("scalar_frame");

    let mut out=KernelFrame::empty(frame.level);
    out.points=frame.points.iter().map(|id|scalar(pool,bars,*id)).collect();
    out.segment_states=frame.segment_states.clone();out.candidate_trend_state=frame.candidate_trend_state;out.candidate_state_known=frame.candidate_state_known;
    let events=if frame.observed_events.is_empty(){&frame.events}else{&frame.observed_events};
    for event in events {
        if !matches!(event.title,"翻空为多"|"翻多为空"|"多空交替"){continue;}
        let facts=match &event.proof{EventProof::Shared(i)=>&pool.proofs[*i],EventProof::Inline(f)=>f};
        let anchor=facts.iter().find_map(|(key,fact)|{
            if !matches!(*key,"confirmed_extreme"|"anchor"){return None;}
            if let Fact::Point(id)=fact{Some(scalar(pool,bars,*id))}else{None}
        });
        let mut point=scalar(pool,bars,event.point);
        let path=out.points.iter().find(|p|p.order()==point.order()&&p.kind==point.kind&&p.value==point.value).map(|p|p.path).or_else(||frame.observations.iter().rev().find(|o|o.known_index<=event.known_index&&pool.view(o.extreme,bars).path==point.path).map(|o|o.path)).unwrap_or(point.path);
        point.path=path;
        let mut key=event.key.map(|id|scalar(pool,bars,id));if let Some(key)=&mut key{key.path=path;}
        let mut anchor=anchor;if let Some(anchor)=&mut anchor{anchor.path=path;}
        out.events.push(KernelEvent{title:event.title.into(),known_index:event.known_index,point,key,origin:anchor,evidence:Value::Null});
    }
    out
}
fn seed(event:&KernelEvent,up:bool)->Option<Search> {
    let (Some(origin),Some(key))=(&event.origin,&event.key)else{return None;};
    let high=&event.point;
    let target=if up{PointKind::H}else{PointKind::L};
    let sign=target.sign();
    if origin.kind==target||key.kind!=target||high.kind!=target||key.path!=origin.path||high.path!=origin.path
        ||!origin.is_closed()||!high.is_closed()||!key.is_closed()
        ||key.level!=origin.level||high.level!=origin.level
        ||!(key.order()<origin.order()&&origin.order()<high.order())
        ||sign*(high.value-key.value)<=0.0||sign*(key.value-origin.value)<=0.0
        ||key.known_index>=high.index
        ||[origin,key,high].iter().any(|p|p.known_index>event.known_index){return None;}
    Some(Search{origin:origin.clone(),high:high.clone(),key:key.clone(),started:event.known_index,
        phase:QualificationPhase::AwaitingAlternationEnd{after:None}})
}
fn path_points(frame:&KernelFrame)->BTreeMap<usize,Vec<KernelPoint>> {
    let mut paths:BTreeMap<usize,Vec<KernelPoint>>=BTreeMap::new();
    for p in frame.points.iter().filter(|p|p.is_closed()){paths.entry(p.path).or_default().push(p.clone());}
    paths
}
fn visible(points:&[KernelPoint],now:usize)->&[KernelPoint] {
    &points[..points.partition_point(|p|p.known_index<=now)]
}
pub(crate) struct PairContext {pub known:usize,pub path:usize,pub pair:Option<(KernelPoint,KernelPoint)>}
pub(crate) fn pair_contexts(pool:&PointArena,bars:&[KernelBar],frame:&RefFrame)->Vec<PairContext>{
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("pair_contexts");

    frame.contexts.iter().map(|context|PairContext{known:context.known_index,path:context.path,
        pair:if context.direction==Some(super::Direction::Down){context.key.zip(context.anchor).map(|(key,low)|(scalar(pool,bars,key),scalar(pool,bars,low)))}else{None}}).collect()
}
/// 直接使用同次趋势函数返回的六态时钟；资格层不按高低点重新识别趋势。
#[derive(Clone, Copy)]
pub(crate) struct StateContext {pub known:usize,pub path:usize,pub state:Option<TrendState>}
pub(crate) fn state_contexts(frame:&RefFrame)->Vec<StateContext>{
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("state_contexts");

    if frame.observations.is_empty(){
        frame.contexts.iter().map(|context|StateContext{known:context.known_index,path:context.path,state:context.state}).collect()
    }else{frame.observations.iter().map(|context|StateContext{known:context.known_index,path:context.path,state:context.state}).collect()}
}
/// 对应 Python confirm_bull_alternation：返回证明表示空多交替已结束，才能开启资格。
#[derive(Clone,Copy,PartialEq,Eq,PartialOrd,Ord)]
struct NOrder([(usize,usize);3]);
fn n_order(n:&crate::analysis::trading::single_n::SingleNData)->NOrder{NOrder([&n.a,&n.b,&n.c].map(|p|(p.bar_index,p.ordinal)))}
type AvailableNs=BTreeMap<NOrder,(super::n_memory::NIndex,super::n_memory::NConfirmationSnapshot)>;
fn confirm_bull_alternation_end(search:&Search,own:&[KernelPoint],polyline:&[KernelPoint],bars:&[RuleCandle],market:&[KernelBar],
    now:usize,level:u8,_config:&TradingDayConfig,ns:&AvailableNs,records:&[super::n_memory::SharedNRecord],pair:Option<(&KernelPoint,&KernelPoint)>,after:Option<usize>,allow_gap:bool)
    ->Result<Option<AlternationProof>,String> {
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("confirm_start");

    if now<search.started||after.is_some_and(|after|now<=after){return Ok(None);}
    let path=format!("lecture-{}",search.origin.path);
    for (index,snapshot) in ns.values(){
        let n=&records.get(index.0).ok_or("qualification N index outside shared array")?.data;
        if let Some(proof)=n_alternation::confirm_single_n_snapshot(bars,now,&search.origin,&search.high,&search.key,
            polyline,n,*snapshot,level,&path,after)? {return Ok(Some(proof));}
    }
    if let Some(proof)=confirmation::confirm_volume_breakout_alternation(bars,now,&search.origin,&search.high,pair,false)?{
        return Ok(Some(proof));
    }
    if allow_gap{
        if let Some(proof)=confirmation::confirm_body_gap_alternation(bars,now,&search.origin,&search.high,polyline,pair)?{
            return Ok(Some(proof));
        }
    }
    // Python 在关闭后先排除旧低点再选最低点，旧低点不能阻挡新的独立确认。
    if let Some((low,rule))=super::confirmation::confirm_ordinary_bull_counter_after(&search.origin,&search.high,own,market,now,after)? {
        return Ok(Some(AlternationProof{low,rule:rule.into(),evidence:Value::Null}));
    }
    Ok(None)
}
fn gap_lower(proof:&AlternationProof)->Option<Decimal> {
    let key=match proof.rule.as_str(){"body_gap_volume_bullish_breakout_after_defended_low"=>"gap_previous_body_high",
        "positive_n_consolidation_gap_after_qualified_b"=>"gap_previous_high",_=>return None};
    crate::analysis::trading::qualification::price(&proof.evidence,key)
}
fn last_rise_broken(points:&[KernelPoint],bars:&[RuleCandle],now:usize)->Result<bool,String> {
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("last_rise_broken");

    let Some(position)=points.iter().rposition(|p|p.kind==PointKind::H)else{return Ok(false);};
    let Some(low)=position.checked_sub(1).and_then(|i|points.get(i))else{return Ok(false);};
    let high=&points[position];
    if low.kind!=PointKind::L||low.known_index>=now||high.index>=now{return Ok(false);}
    let ceiling=price(high)?;let support=price(low)?;
    if bars[now].low>=support||bars[high.index+1..=now].iter().any(|bar|bar.high>ceiling){return Ok(false);}
    let Some(offset)=bars[high.index+1..=now].iter().position(|bar|bar.low<support)else{return Ok(false);};
    // 破位是一次性因果事实；较早跌破的旧支撑不能逐日关闭后来新确认的资格。
    let first_known=(high.index+1+offset).max(high.known_index).max(low.known_index+1);
    Ok(first_known==now)
}
fn close(active:&ActiveQualification,intervals:&mut[QualificationInterval],now:usize,reason:&str) {
    intervals[active.interval].end_index=Some(now);intervals[active.interval].end_reason=Some(reason.into());
}
fn source_path(paths:&BTreeMap<(usize,usize),(usize,usize)>,path:usize,index:usize)->(usize,usize){
    paths.range((path,0)..=(path,index)).next_back().map_or((path,path),|(_,source)|*source)
}
/// 每级一次按日期推进；空翻多只创建 AwaitingAlternationEnd，只有 Active 才调用结束条件。
/// source_paths 显式绑定直接下级路径，不能把其他来源的翻空拿来关闭本级资格。
pub(crate) fn calculate(frame:&KernelFrame,lower:&KernelFrame,polyline:&KernelFrame,source_paths:&BTreeMap<(usize,usize),(usize,usize)>,
    bars:&[RuleCandle],market:&[KernelBar],config:&TradingDayConfig,pairs:&[PairContext],states:&[StateContext],enabled:bool,cancel:Option<&AtomicBool>,n_facts:&[super::n_memory::NFact],records:&[super::n_memory::SharedNRecord])
    ->Result<Vec<QualificationInterval>,String> {
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("daily_eligibility");

    if !enabled||bars.is_empty(){return Ok(Vec::new());}
    let own=path_points(frame);let lower_points=path_points(lower);let line=path_points(polyline);
    let mut events:Vec<_>=frame.events.iter().collect();events.sort_by_key(|e|(e.known_index,e.point.order()));
    let mut lower_events:Vec<_>=lower.events.iter().filter(|e|e.title=="翻多为空"&&seed(e,false).is_some()).collect();
    lower_events.sort_by_key(|e|e.known_index);
    let mut states:Vec<_>=states.iter().collect();states.sort_by_key(|state|state.known);
    let mut current_states=BTreeMap::new();
    let path_starts:BTreeMap<_,_>=line.iter().filter_map(|(path,p)|p.first().map(|p|(*path,p.known_index))).collect();
    let mut searches:BTreeMap<usize,Search>=BTreeMap::new();let mut intervals=Vec::new();
    let(mut event_index,mut lower_index,mut state_index,mut n_index)=(0,0,0,0);
    let mut current_ns:BTreeMap<usize,AvailableNs>=BTreeMap::new();
    let empty_ns=BTreeMap::new();
    for now in 0..bars.len(){
        if cancel.is_some_and(|flag|flag.load(Ordering::Relaxed)){return Err("qualification calculation cancelled".into());}
        while n_index<n_facts.len()&&n_facts[n_index].known<=now{
            let fact=&n_facts[n_index];let ns=current_ns.entry(fact.path).or_default();
            match &fact.change{super::n_memory::NChange::Put(index,snapshot)=>{let data=&records.get(index.0).ok_or("qualification N fact outside shared table")?.data;ns.insert(n_order(data),(*index,*snapshot));},super::n_memory::NChange::Remove(index)=>{let data=&records.get(index.0).ok_or("qualification removed N outside shared table")?.data;ns.remove(&n_order(data));}}
            n_index+=1;
        }
        let first=event_index;while event_index<events.len()&&events[event_index].known_index<=now{event_index+=1;}
        let lower_first=lower_index;while lower_index<lower_events.len()&&lower_events[lower_index].known_index<=now{lower_index+=1;}
        let state_first=state_index;
        while state_index<states.len()&&states[state_index].known<=now{
            let state=states[state_index];current_states.insert(state.path,state.state);state_index+=1;
        }
        for event in &events[first..event_index]{
            if event.title!="翻空为多"{continue;}
            let path=event.point.path;
            // 同一确认日可能推进多个点；不丢掉当日先空翻多、后交替的合法入口。
            if current_states.get(&path)!=Some(&Some(TrendState::BearToBull))
                &&!states[state_first..state_index].iter().any(|s|s.path==path&&s.state==Some(TrendState::BearToBull)){continue;}
            if let Some(next)=seed(event,true){
                let origin=price(&next.origin)?;
                if bars[next.origin.index+1..=now].iter().any(|bar|bar.low<origin){continue;}
                if let Some(previous)=searches.get_mut(&path){
                    if matches!(previous.phase,QualificationPhase::Active(_)){continue;}
                    // 延续同一轮空翻多只提高攻击高点，保留首次起点、关键位及重开门槛。
                    if current_ns.get(&path).is_none_or(|ns|!ns.values().any(|(index,_)|records.get(index.0).is_some_and(|n|n.data.a.bar_index>previous.high.index)))&&next.high.order()>previous.high.order()&&next.high.value>previous.high.value{
                        previous.high=next.high;
                    }
                }else{searches.insert(path,next);}
            }
        }
        let mut retired=Vec::new();
        for (&path,search) in &mut searches{
            let own=visible(own.get(&path).map(Vec::as_slice).unwrap_or(&[]),now);
            let (direct,base)=source_path(source_paths,path,search.high.index);
            let lower=visible(lower_points.get(&direct).map(Vec::as_slice).unwrap_or(&[]),now);
            let polyline=visible(line.get(&base).map(Vec::as_slice).unwrap_or(&[]),now);
            let path_ended=path_starts.iter().any(|(other,&start)|*other!=base&&start>search.started&&start<=now);
            let bear_state=current_states.get(&path)==Some(&Some(TrendState::Bear));
            let bear_flip=events[first..event_index].iter().any(|e|e.point.path==path&&e.point.order()>search.high.order()&&e.title=="翻多为空"&&seed(e,false).is_some());
            let bear_alternation=events[first..event_index].iter().any(|e|e.point.path==path&&e.point.order()>search.high.order()&&e.title=="多空交替");
            let origin_broken=bars[now].low<price(&search.origin)?;
            let invalid_candidate=path_ended||bear_state||bear_flip||bear_alternation||origin_broken;
            let pair=pairs.iter().rev().find(|p|p.path==base&&p.known<=now).and_then(|p|p.pair.as_ref()).map(|(key,low)|(key,low));
            // N 已由本级记忆簿推进；资格消费者只读当日已知记录，不再搜索或清空 N。
            let ns=current_ns.get(&path).unwrap_or(&empty_ns);
            if let QualificationPhase::AwaitingAlternationEnd{after}=search.phase{
                // 没有有效资格可关闭；仅撤销已经失效的翻多候选，不生成结束区间。
                if invalid_candidate{retired.push(path);continue;}
                let Some(proof)=confirm_bull_alternation_end(search,own,polyline,bars,market,now,frame.level,config,ns,records,pair,after,true)? else{continue;};
                let active=ActiveQualification{interval:intervals.len(),low:proof.low.clone(),gap:gap_lower(&proof)};
                // 空多交替结束的真实确认日就是交易资格开始日，不回填翻多或结构低点日期。
                intervals.push(QualificationInterval{path,start_index:now,low_index:proof.low.index,end_index:None,
                    confirmation_rule:proof.rule,end_reason:None});
                search.phase=QualificationPhase::Active(active);
            }
            let QualificationPhase::Active(active)=&search.phase else{continue;};
            let mut active=active.clone();
            let start=intervals[active.interval].start_index;
            // Python 的独立确认可解除缺口依赖；这里只更新现有窗口，不重复开启资格。
            if active.gap.is_some()&&now>start&&!invalid_candidate
                &&confirm_bull_alternation_end(search,own,polyline,bars,market,now,frame.level,config,ns,records,pair,None,false)?.is_some(){
                active.gap=None;
            }
            // 以下结束规则仅 Active 可达；确认当日也执行，空头具有强制关闭优先级。
            let source_bear=lower_events[lower_first..lower_index].iter().any(|e|e.point.path==direct);
            let latest_low=own.iter().rev().find(|p|p.kind==PointKind::L&&p.known_index<now&&p.order()>=active.low.order());
            let low_broken=latest_low.map(price).transpose()?.is_some_and(|p|bars[now].low<p);
            let reason=if bear_state{Some("same_level_bear_trend_confirmed")}
                else if path_ended{Some("source_path_ended")}
                else if bear_flip{Some("same_level_last_rise_low_broken")}
                else if bear_alternation{Some("bear_alternation_completed")}
                else if source_bear||frame.level>0&&last_rise_broken(lower,bars,now)?{Some("source_bear_trend_confirmed")}
                else if last_rise_broken(own,bars,now)?{Some("same_level_last_rise_low_broken")}
                else if origin_broken{Some("special_bull_origin_broken")}
                else if now>start&&active.gap.is_some_and(|gap|bars[now].low<=gap){Some("confirmation_gap_filled")}
                else if low_broken{Some("reversal_low_broken")}else{None};
            if let Some(reason)=reason{
                close(&active,&mut intervals,now,reason);
                // 沿用 Python resume_qualification_search：本级翻多仍成立才等待新证据。
                // 本级空头/反向事实/起点或路径失效必须回到等待新的空翻多。
                if invalid_candidate{retired.push(path);}else{
                    search.phase=QualificationPhase::AwaitingAlternationEnd{after:Some(now)};
                }
            }else{search.phase=QualificationPhase::Active(active);}
        }
        for path in retired{searches.remove(&path);}
    }
    Ok(intervals)
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::PointState;
    fn point(level:u8,index:usize,known:usize,kind:PointKind,value:f64)->KernelPoint {
        KernelPoint::new(KernelPointData{index,ordinal:0,time:format!("2020-01-{:02}",index+1),kind,value,
            known_index:known,level,path:0,state:PointState::Confirmed,evidence:Value::Null})
    }
    fn fixture(level:u8)->(KernelFrame,Vec<RuleCandle>,Vec<KernelBar>){
        let mut market:Vec<_>=(0..10).map(|index|KernelBar{index,time:format!("2020-01-{:02}",index+1),
            open:25.0,close:25.0,high:26.0,low:24.0}).collect();
        for(index,open,high,low,close)in [(0,19.0,20.0,18.0,19.0),(2,11.0,12.0,10.0,11.0),
            (4,29.0,30.0,28.0,29.0),(6,23.0,24.0,22.0,23.0)]{
            market[index].open=open;market[index].high=high;market[index].low=low;market[index].close=close;
        }
        let bars=market.iter().map(|b|RuleCandle{symbol:"fixture".into(),timestamp:b.time.clone(),
            open:b.open.to_string().parse().unwrap(),high:b.high.to_string().parse().unwrap(),low:b.low.to_string().parse().unwrap(),close:b.close.to_string().parse().unwrap(),
            volume:Decimal::from(100),adjustment_factor:Decimal::ONE,raw_trading_active:None,raw_is_st:None}).collect();
        let key=point(level,0,0,PointKind::H,20.0);let origin=point(level,2,2,PointKind::L,10.0);let high=point(level,4,4,PointKind::H,30.0);
        let mut frame=KernelFrame::empty(level);frame.points=vec![key.clone(),origin.clone(),high.clone(),point(level,6,6,PointKind::L,22.0)];
        frame.events.push(KernelEvent{title:"翻空为多".into(),known_index:4,point:high,key:Some(key),origin:Some(origin),evidence:Value::Null});
        (frame,bars,market)
    }
    fn run(frame:&KernelFrame,lower:&KernelFrame,bars:&[RuleCandle],market:&[KernelBar],enabled:bool)->Vec<QualificationInterval>{
        let states=frame.events.iter().filter_map(|event|Some(StateContext{known:event.known_index,path:event.point.path,state:Some(match event.title.as_str(){
            "翻空为多"=>TrendState::BearToBull,"翻多为空"=>TrendState::BullToBear,"多空交替"=>TrendState::BullBearAlternation,_=>return None})})).collect::<Vec<_>>();
        run_with_states(frame,lower,bars,market,&states,enabled)
    }
    fn run_with_states(frame:&KernelFrame,lower:&KernelFrame,bars:&[RuleCandle],market:&[KernelBar],states:&[StateContext],enabled:bool)->Vec<QualificationInterval>{
        calculate(frame,lower,&KernelFrame::empty(0),&BTreeMap::new(),bars,market,&TradingDayConfig::lecture(),&[],states,enabled,None,&[],&[]).unwrap()
    }
    #[test]
    fn enabled_and_bear_to_bull_are_both_required_at_each_level(){
        for level in 1..=3{
            let(frame,bars,market)=fixture(level);let lower=KernelFrame::empty(level-1);
            assert!(run(&frame,&lower,&bars,&market,false).is_empty());
            let mut no_flip=frame.clone();no_flip.events.clear();
            assert!(run(&no_flip,&lower,&bars,&market,true).is_empty());
            let windows=run(&frame,&lower,&bars,&market,true);
            assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,6);assert_eq!(windows[0].end_index,None);
        }
    }
    #[test]
    fn a_future_confirmation_never_leaks_into_an_earlier_prefix(){
        let(mut frame,bars,market)=fixture(2);frame.points[3].known_index=8;
        assert!(run(&frame,&KernelFrame::empty(1),&bars[..8],&market[..8],true).is_empty());
        assert_eq!(run(&frame,&KernelFrame::empty(1),&bars,&market,true)[0].start_index,8);
    }
    #[test]
    fn confirmation_day_can_close_and_old_evidence_cannot_reopen(){
        for level in 1..=3{
            let(frame,bars,market)=fixture(level);let mut lower=KernelFrame::empty(level-1);
            lower.events.push(KernelEvent{title:"翻多为空".into(),known_index:6,
                point:point(level-1,5,6,PointKind::L,24.0),key:Some(point(level-1,1,1,PointKind::L,26.0)),
                origin:Some(point(level-1,3,3,PointKind::H,32.0)),evidence:Value::Null});
            let windows=run(&frame,&lower,&bars,&market,true);
            assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,6);assert_eq!(windows[0].end_index,Some(6));
            assert_eq!(windows[0].end_reason.as_deref(),Some("source_bear_trend_confirmed"));
        }
    }
    #[test]
    fn reversal_low_break_closes_only_after_confirmation(){
        let(frame,mut bars,mut market)=fixture(2);bars[8].low=Decimal::from(21);market[8].low=21.0;
        let windows=run(&frame,&KernelFrame::empty(1),&bars,&market,true);
        assert_eq!(windows.len(),1);assert_eq!(windows[0].end_index,Some(8));
        assert_eq!(windows[0].end_reason.as_deref(),Some("reversal_low_broken"));
    }
    #[test]
    fn an_old_source_support_break_cannot_close_a_later_qualification(){
        for level in 1..=3{
            let(frame,mut bars,mut market)=fixture(level);let mut lower=KernelFrame::empty(level-1);
            bars[3].high=Decimal::from(32);market[3].high=32.0;
            bars[5].low=Decimal::from(23);market[5].low=23.0;
            lower.points=vec![point(level-1,1,1,PointKind::L,24.0),point(level-1,3,3,PointKind::H,32.0)];
            assert!(last_rise_broken(&lower.points,&bars,5).unwrap());
            assert!(!last_rise_broken(&lower.points,&bars,6).unwrap());
            let windows=run(&frame,&lower,&bars,&market,true);
            assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,6);assert_eq!(windows[0].end_index,None);
            bars[8].low=Decimal::from(21);market[8].low=21.0;
            let windows=run(&frame,&lower,&bars,&market,true);
            assert_eq!(windows[0].end_index,Some(8));assert_eq!(windows[0].end_reason.as_deref(),Some("reversal_low_broken"));
        }
    }
    #[test]
    fn a_support_break_is_consumed_once_when_its_source_becomes_known(){
        let(_,mut bars,_)=fixture(2);bars[3].high=Decimal::from(32);bars[5].low=Decimal::from(23);
        let points=[point(1,1,1,PointKind::L,24.0),point(1,3,6,PointKind::H,32.0)];
        assert!(last_rise_broken(&points,&bars,6).unwrap());
        bars[7].low=Decimal::from(23);assert!(!last_rise_broken(&points,&bars,7).unwrap());
        bars[8].low=Decimal::from(25);bars[9].low=Decimal::from(23);
        assert!(!last_rise_broken(&points,&bars,9).unwrap());
    }
    #[test]
    fn a_failed_bear_flip_reaches_qualification_through_the_shared_six_state_events(){
        for level in 1..=3{
            let values=[10.0,5.0,12.0,7.0,14.0,6.0,15.0,10.0];
            let mut pool=PointArena::default();let mut market=Vec::new();let mut ids=Vec::new();
            for(index,value)in values.iter().copied().enumerate(){
                let kind=if index%2==0{PointKind::H}else{PointKind::L};
                let close=if kind==PointKind::H{value-1.0}else{value+1.0};
                market.push(KernelBar{index,time:format!("2020-01-{:02}",index+1),open:close,close,
                    high:if kind==PointKind::H{value}else{value+2.0},low:if kind==PointKind::L{value}else{value-2.0}});
                ids.push(pool.import(&point(level,index,index,kind,value)));
            }
            let frame=super::super::structure::six_state_indices(&mut pool,&market,&ids,level).unwrap();
            let frame=scalar_frame(&pool,&market,&frame);
            let bars=market.iter().map(|bar|RuleCandle{symbol:"fixture".into(),timestamp:bar.time.clone(),
                open:bar.open.to_string().parse().unwrap(),high:bar.high.to_string().parse().unwrap(),
                low:bar.low.to_string().parse().unwrap(),close:bar.close.to_string().parse().unwrap(),
                volume:Decimal::from(100),adjustment_factor:Decimal::ONE,raw_trading_active:None,raw_is_st:None}).collect::<Vec<_>>();
            let windows=run(&frame,&KernelFrame::empty(level-1),&bars,&market,true);
            assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,7);assert_eq!(windows[0].end_index,None);
        }
    }
    #[test]
    fn a_deep_counter_waits_for_a_known_rebreak(){
        let(mut frame,mut bars,mut market)=fixture(1);
        frame.points[3].value=16.0;bars[6].low=Decimal::from(16);market[6].low=16.0;
        assert!(run(&frame,&KernelFrame::empty(0),&bars,&market,true).is_empty());
        frame.points.push(point(1,8,9,PointKind::H,31.0));bars[8].high=Decimal::from(31);market[8].high=31.0;
        let windows=run(&frame,&KernelFrame::empty(0),&bars,&market,true);
        assert_eq!(windows[0].start_index,9);assert_eq!(windows[0].confirmation_rule,"confirmed_higher_pullback_then_confirmed_flip_high_rebreak");
    }

    #[test]
    fn flip_waits_for_alternation_end_even_after_the_trend_state_changes(){
        for level in 1..=3{
            let(mut frame,bars,market)=fixture(level);frame.points[3].known_index=8;
            let states=[
                StateContext{known:4,path:0,state:Some(TrendState::BearToBull)},
                StateContext{known:6,path:0,state:Some(TrendState::BearBullAlternation)},
                StateContext{known:7,path:0,state:Some(TrendState::Bull)},
            ];
            assert!(run_with_states(&frame,&KernelFrame::empty(level-1),&bars[..8],&market[..8],&states,true).is_empty());
            let windows=run_with_states(&frame,&KernelFrame::empty(level-1),&bars,&market,&states,true);
            assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,8);
        }
    }
    #[test]
    fn alternation_state_alone_does_not_confirm_its_end(){
        let(mut frame,mut bars,mut market)=fixture(2);
        frame.points[3].value=16.0;bars[6].low=Decimal::from(16);market[6].low=16.0;
        let states=[
            StateContext{known:4,path:0,state:Some(TrendState::BearToBull)},
            StateContext{known:6,path:0,state:Some(TrendState::BearBullAlternation)},
            StateContext{known:7,path:0,state:Some(TrendState::Bull)},
        ];
        assert!(run_with_states(&frame,&KernelFrame::empty(1),&bars,&market,&states,true).is_empty());
    }
    #[test]
    fn a_bull_state_without_returned_bear_to_bull_cannot_start_the_search(){
        let(frame,bars,market)=fixture(2);
        let states=[StateContext{known:4,path:0,state:Some(TrendState::Bull)}];
        assert!(run_with_states(&frame,&KernelFrame::empty(1),&bars,&market,&states,true).is_empty());
    }
    #[test]
    fn own_bear_state_must_close_without_a_new_flip_event_and_cannot_reopen(){
        for level in 1..=3{
            let(frame,bars,market)=fixture(level);
            let states=[
                StateContext{known:4,path:0,state:Some(TrendState::BearToBull)},
                StateContext{known:8,path:0,state:Some(TrendState::Bear)},
            ];
            let prefix=run_with_states(&frame,&KernelFrame::empty(level-1),&bars[..8],&market[..8],&states,true);
            assert_eq!(prefix[0].end_index,None);
            let windows=run_with_states(&frame,&KernelFrame::empty(level-1),&bars,&market,&states,true);
            assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,6);assert_eq!(windows[0].end_index,Some(8));
            assert_eq!(windows[0].end_reason.as_deref(),Some("same_level_bear_trend_confirmed"));
        }
    }
    #[test]
    fn bear_before_qualification_retires_the_candidate_without_an_interval(){
        let(mut frame,bars,market)=fixture(2);frame.points[3].known_index=8;
        let states=[
            StateContext{known:4,path:0,state:Some(TrendState::BearToBull)},
            StateContext{known:7,path:0,state:Some(TrendState::Bear)},
            StateContext{known:9,path:0,state:Some(TrendState::Bull)},
        ];
        assert!(run_with_states(&frame,&KernelFrame::empty(1),&bars,&market,&states,true).is_empty());
    }
    #[test]
    fn an_exit_before_start_does_not_close_a_future_qualification(){
        let(frame,bars,market)=fixture(2);let mut lower=KernelFrame::empty(1);
        lower.events.push(KernelEvent{title:"翻多为空".into(),known_index:5,
            point:point(1,5,5,PointKind::L,24.0),key:Some(point(1,1,1,PointKind::L,26.0)),
            origin:Some(point(1,3,3,PointKind::H,32.0)),evidence:Value::Null});
        let windows=run(&frame,&lower,&bars,&market,true);
        assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,6);assert_eq!(windows[0].end_index,None);
    }
    #[test]
    fn a_source_key_first_known_on_its_attack_day_is_not_formal_bear_evidence(){
        let(frame,bars,market)=fixture(2);let mut lower=KernelFrame::empty(1);
        lower.events.push(KernelEvent{title:"翻多为空".into(),known_index:6,
            point:point(1,5,6,PointKind::L,24.0),key:Some(point(1,1,5,PointKind::L,26.0)),
            origin:Some(point(1,3,3,PointKind::H,32.0)),evidence:Value::Null});
        let windows=run(&frame,&lower,&bars,&market,true);
        assert_eq!(windows.len(),1);assert_eq!(windows[0].end_index,None);
    }
    #[test]
    fn resuming_requires_a_new_counter_not_a_new_read_of_the_old_low(){
        let(mut frame,mut bars,mut market)=fixture(2);let mut lower=KernelFrame::empty(1);
        lower.events.push(KernelEvent{title:"翻多为空".into(),known_index:6,
            point:point(1,5,6,PointKind::L,24.0),key:Some(point(1,1,1,PointKind::L,26.0)),
            origin:Some(point(1,3,3,PointKind::H,32.0)),evidence:Value::Null});
        frame.points.push(point(2,7,7,PointKind::H,28.0));
        frame.points.push(point(2,8,8,PointKind::L,24.0));
        bars[7].high=Decimal::from(28);market[7].high=28.0;
        let windows=run(&frame,&lower,&bars,&market,true);
        assert_eq!(windows.len(),2);assert_eq!(windows[0].start_index,6);assert_eq!(windows[0].end_index,Some(6));
        assert_eq!(windows[1].start_index,8);assert_eq!(windows[1].end_index,None);
    }
    #[test]
    fn unanchored_history_reaches_qualification_through_real_state_contexts(){
        for level in 1..=3{
            let values=[13.0,4.6,6.4,4.7,9.0,3.8,5.3,4.0,7.2,4.4,11.0,7.0,12.0];
            let mut pool=PointArena::default();let mut market=Vec::new();let mut ids=Vec::new();
            for(index,value)in values.iter().copied().enumerate(){
                let kind=if index%2==0{PointKind::H}else{PointKind::L};
                let close=if kind==PointKind::H{value-0.1}else{value+0.1};
                market.push(KernelBar{index,time:format!("2020-01-{:02}",index+1),open:close,close,
                    high:if kind==PointKind::H{value}else{value+0.2},low:if kind==PointKind::L{value}else{value-0.2}});
                ids.push(pool.import(&point(level,index,index,kind,value)));
            }
            let native=super::super::structure::six_state_indices(&mut pool,&market,&ids,level).unwrap();
            let states=state_contexts(&native);let frame=scalar_frame(&pool,&market,&native);
            let bars=market.iter().map(|bar|RuleCandle{symbol:"fixture".into(),timestamp:bar.time.clone(),
                open:bar.open.to_string().parse().unwrap(),high:bar.high.to_string().parse().unwrap(),
                low:bar.low.to_string().parse().unwrap(),close:bar.close.to_string().parse().unwrap(),
                volume:Decimal::from(100),adjustment_factor:Decimal::ONE,raw_trading_active:None,raw_is_st:None}).collect::<Vec<_>>();
            let windows=run_with_states(&frame,&KernelFrame::empty(level-1),&bars,&market,&states,true);
            assert_eq!(windows.len(),1);assert_eq!(windows[0].start_index,12);assert_eq!(windows[0].end_index,None);
        }
    }}
