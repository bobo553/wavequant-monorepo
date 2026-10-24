//! 旧 `_analysis=None` 候选入口：只消费共享逐日结构，不重算或修改任何趋势。
//! 主 Analyzer 使用资格作用域内的折线 N；这里保留旧批入口的独立候选集合和 N 级别。
use super::{RuleCandle,Direction,NSetup,NOptions,NStatus,PivotRef,BoxAnchorMode,MilestoneBasis,observe_n,
    targets::{self,EligibleNSource,TradingNMeasurement},candidates::PreparedN,config::TradingDayConfig,entries};
use crate::analysis::{kernel::{KernelFrame,KernelPoint,PointKind},market_state::{
    control,regime::{self,RegimePolicy,WaveBoundary},price_action::ShadowPolicy}};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

pub type LegacyStructureHistory=BTreeMap<usize,Vec<KernelFrame>>;
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct LegacyCandidateSet{
    pub known_at:usize,pub points:Vec<KernelPoint>,pub n_level:u8,pub epoch:usize,pub blocked:bool,
}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct LegacyNPreparation{
    /// 与 Python 进入底部归属仲裁前的 candidates 对应；候选自身不授予交易资格。
    pub candidates:Vec<PreparedN>,pub events:Vec<Value>,pub counts:BTreeMap<String,usize>,
}
fn current_epoch(frame:&KernelFrame)->usize{frame.candidate.as_ref().or_else(||frame.points.last()).map_or(0,|p|p.path)}
fn snapshot<'a>(frames:&'a [KernelFrame],level:u8)->Result<&'a KernelFrame,String>{
    let mut matches=frames.iter().filter(|frame|frame.level==level);
    let frame=matches.next().ok_or_else(||format!("legacy snapshot lacks level {level}"))?;
    if matches.next().is_some(){return Err("legacy snapshot contains duplicate structural levels".into());}Ok(frame)
}
fn dated_points(points:&[KernelPoint],now:usize)->Result<(),String>{
    if points.iter().any(|p|p.index>p.known_index||p.known_index>now||!p.value.is_finite()){
        return Err("legacy candidate source contains unavailable points".into());
    }Ok(())
}

/// Python hierarchical_n_candidates：来源只能是各级独立许可发布点，不能拿结构标签补资格。
pub fn hierarchical_n_candidates(history:&LegacyStructureHistory)->Result<BTreeMap<usize,Vec<LegacyCandidateSet>>,String>{
    let mut result=BTreeMap::<usize,Vec<LegacyCandidateSet>>::new();let mut seen=BTreeSet::<[(usize,usize);3]>::new();
    for(&now,frames)in history{
        let polyline=snapshot(frames,0)?;let epoch=current_epoch(polyline);
        for level in [2,3]{
            let frame=snapshot(frames,level)?;dated_points(&frame.publication,now)?;
            let mut paths=BTreeMap::<usize,Vec<&KernelPoint>>::new();
            for point in frame.publication.iter().filter(|point|point.path==epoch){paths.entry(point.path).or_default().push(point);}
            for points in paths.values(){
                for offset in 1..points.len(){
                    let(a,b)=(points[offset-1],points[offset]);if a.kind!=PointKind::L||b.kind!=PointKind::H{continue;}
                    let mut c:Option<&KernelPoint>=None;
                    for &point in &points[offset+1..]{
                        if point.kind==PointKind::H&&point.value>=b.value{break;}
                        if point.kind==PointKind::L{
                            if point.value<=a.value{c=None;break;}
                            if c.is_none_or(|current|point.value<current.value){c=Some(point);}
                        }
                    }
                    let Some(c)=c else{continue;};let identity=[(a.index,a.known_index),(b.index,b.known_index),(c.index,c.known_index)];
                    if seen.insert(identity){result.entry(now).or_default().push(LegacyCandidateSet{known_at:now,
                        points:vec![a.clone(),b.clone(),c.clone()],n_level:level,epoch,blocked:polyline.blocked_at==Some(now)});}
                }
            }
        }
    }Ok(result)
}
pub fn folded_positive_n_candidates(points:&[KernelPoint],earliest:usize,allow_equal_origin:bool)->Vec<(Vec<KernelPoint>,u8)>{
    targets::folded_n_candidates(points,earliest,Direction::Up,allow_equal_origin).into_iter().map(|p|(Vec::from(p),1)).collect()
}
pub fn folded_inverse_n_candidates(points:&[KernelPoint],earliest:usize)->Vec<(Vec<KernelPoint>,u8)>{
    targets::folded_n_candidates(points,earliest,Direction::Down,false).into_iter().map(|p|(Vec::from(p),1)).collect()
}

/// 保留旧批顺序：全部每日折线/高层候选先入队，然后追加全部折叠候选。
pub fn legacy_candidate_sets(history:&LegacyStructureHistory,config:&TradingDayConfig)->Result<Vec<LegacyCandidateSet>,String>{
    config.validate()?;
    let higher=config.buy_point_definition=="whole_flip_wave_v3"&&config.pivot_mode=="lecture_causal";
    let larger=if higher{hierarchical_n_candidates(history)?}else{BTreeMap::new()};
    let mut result=vec![];let mut folded=vec![];
    for(&now,frames)in history{
        let polyline=snapshot(frames,0)?;let epoch=current_epoch(polyline);
        if epoch>now{return Err("legacy snapshot epoch is in the future".into());}
        let points:Vec<_>=polyline.points.iter().filter(|p|p.path==epoch&&p.is_closed()).cloned().collect();dated_points(&points,now)?;
        let blocked=polyline.blocked_at==Some(now);
        result.push(LegacyCandidateSet{known_at:now,points:points.clone(),n_level:0,epoch,blocked});
        result.extend(larger.get(&now).into_iter().flatten().cloned());
        if higher{
            let earliest=epoch.max(now.saturating_sub(config.structure_window));
            for(points,n_level)in folded_positive_n_candidates(&points,earliest,false).into_iter().chain(folded_inverse_n_candidates(&points,earliest)){
                folded.push(LegacyCandidateSet{known_at:now,points,n_level,epoch,blocked});
            }
        }
    }result.extend(folded);Ok(result)
}
fn count(counts:&mut BTreeMap<String,usize>,name:&str){*counts.entry(name.into()).or_default()+=1;}
fn event(bars:&[RuleCandle],now:usize,kind:&str,fields:Value)->Value{
    let mut result=json!({"event":kind,"bar_index":now,"timestamp":bars[now].timestamp,"symbol":bars[now].symbol});
    if let(Some(result),Some(fields))=(result.as_object_mut(),fields.as_object()){result.extend(fields.clone());}result
}
fn reference(point:&KernelPoint)->PivotRef{PivotRef{index:point.index,confirmed_index:point.known_index,ordinal:Some(point.ordinal)}}

/// 对应旧批策略的 N 准备段。调用方传入共享 frames 历史，再复用正式资格与统一仲裁；本函数不发订单。
pub fn prepare_legacy_candidates(bars:&[RuleCandle],history:&LegacyStructureHistory,config:&TradingDayConfig,timeframe:&str)->Result<LegacyNPreparation,String>{
    if timeframe!="1d"{return Err("legacy daily N preparation requires timeframe 1d".into());}
    if history.len()!=bars.len()||history.keys().copied().ne(0..bars.len()){
        return Err("legacy N preparation requires one shared frame snapshot per daily bar".into());
    }
    let mut result=LegacyNPreparation::default();if bars.is_empty(){return Ok(result);}super::validate_bars(bars)?;
    let sets=legacy_candidate_sets(history,config)?;let whole=config.buy_point_definition=="whole_flip_wave_v3";
    let blocked=history.iter().filter_map(|(&now,frames)|frames.iter().find(|f|f.level==0&&f.blocked_at==Some(now)).map(|_|now)).collect::<BTreeSet<_>>();
    result.counts.insert("rows".into(),bars.len());result.counts.insert("ambiguous_bars".into(),blocked.len());
    let mut seen=BTreeSet::<[(usize,usize);3]>::new();let mut attack_keys=BTreeSet::<(u8,usize)>::new();
    for set in sets{
        let now=set.known_at;if set.blocked{result.events.push(event(bars,now,"strict_structure_interrupted",json!({})));continue;}
        if set.points.len()<3{count(&mut result.counts,"insufficient_pivots_bars");continue;}
        let offset=set.points.len()-3;let mut a=&set.points[offset];let b=&set.points[offset+1];let c=&set.points[offset+2];
        if whole&&a.kind==PointKind::H&&a.index==b.index&&b.index<c.index{
            if let Some(prior)=set.points[..offset].iter().rev().find(|p|p.kind==PointKind::H&&p.index<b.index&&bars[p.index].high>bars[c.index].high){a=prior;}
        }
        if !seen.insert([(a.index,a.known_index),(b.index,b.known_index),(c.index,c.known_index)]){continue;}
        let mother=whole&&config.pivot_mode=="lecture_causal"&&a.kind==PointKind::L&&a.index==b.index&&b.index<c.index&&a.ordinal<b.ordinal;
        let same_bar=whole&&config.pivot_mode=="lecture_causal"&&a.kind==PointKind::L&&b.kind==PointKind::H&&c.kind==PointKind::L
            &&a.index<b.index&&b.index==c.index&&b.ordinal<c.ordinal;
        if !(a.index<b.index&&b.index<c.index||mother||same_bar){count(&mut result.counts,"same_bar_n_rejected");
            result.events.push(event(bars,now,"n_geometry_rejected",json!({"reason":"same_bar_vertices_require_lower_timeframe_n"})));continue;}
        if now.saturating_sub(a.index)>config.structure_window.saturating_mul(usize::from(set.n_level).max(1)){count(&mut result.counts,"expired_geometry");continue;}
        let inside=same_bar&&b.index>0&&super::teaching_inside(&bars[b.index-1],&bars[b.index]);
        let direction=if a.kind==PointKind::L{Direction::Up}else{Direction::Down};
        let setup=NSetup{symbol:bars[0].symbol.clone(),timeframe:timeframe.into(),direction,origin:reference(a),neckline:reference(b),pullback:reference(c),
            source:config.pivot_mode.clone(),box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,allow_confirmation_bar:whole,allow_outside_close:whole,
            allow_mother_impulse:mother,staged_defense:whole&&direction==Direction::Up,allow_mother_pullback:same_bar&&!inside,allow_inside_pullback:inside,polyline_structure:false};
        let limit=blocked.range(now..).next().and_then(|i|i.checked_sub(1)).unwrap_or(bars.len()-1);
        let finish=(bars.len()-1).min(limit).min(now.saturating_add(config.pattern_ttl));count(&mut result.counts,"n_candidates");
        let observation=match observe_n(bars,&setup,&NOptions{timeframe:timeframe.into(),milestone_basis:MilestoneBasis::Extreme,
            asof_index:Some(finish),measurement_start:None,measure_targets:true}){
            Ok(observation)=>observation,Err(reason)=>{count(&mut result.counts,"rejected_n_geometry");result.events.push(event(bars,now,"n_geometry_rejected",json!({"reason":reason})));continue;}
        };
        let Some(completion)=&observation.completion else{
            count(&mut result.counts,match observation.status{NStatus::AwaitAnchors=>"n_await_anchors",NStatus::Forming=>"n_forming",
                NStatus::Invalidated=>"n_origin_breached_before_completion",NStatus::PullbackExtended=>"n_pullback_extended_new_anchor_required",NStatus::Completed=>"n_completed"});continue;
        };
        let attack=completion.bar_index;let attack_key=(if direction==Direction::Up{0}else{1},attack);
        if !inside&&!attack_keys.insert(attack_key){continue;}
        let shadow=Some(ShadowPolicy{minimum_range_fraction:config.shadow_fraction});
        let control=control::observe_prepared_control(bars,&setup,config.volume_lookback,shadow,None,&observation)?;
        let warning=if whole&&direction==Direction::Up{entries::v3_positive_n_attack_rejection(&bars[attack],control.volume.as_ref().map(|v|v.increased_from_previous))}else{None};
        if config.strict_n_attack_quality{if let Some(reason)=warning{count(&mut result.counts,"rejected_v3_positive_n_attack");
            result.events.push(event(bars,attack,"n_attack_rejected",json!({"direction":"up","reason":reason,"known_at":now,
                "previous_volume":attack.checked_sub(1).map(|i|bars[i].volume),"attack_volume":bars[attack].volume,
                "open":bars[attack].open,"high":bars[attack].high,"low":bars[attack].low,"close":bars[attack].close})));continue;
        }}
        let regime=regime::observe_prepared_regime(bars,&setup,&RegimePolicy{shadow_policy:shadow,wave_boundary:WaveBoundary::NOrigin,
            local_resistance_failure:whole&&direction==Direction::Up},None,&observation)?;
        let target=observation.targets.as_ref().ok_or("completed legacy N targets missing")?;
        let identity=format!("legacy-n:{}:{}:{}:{attack}",a.index,b.index,c.index);
        count(&mut result.counts,if direction==Direction::Up{"completed_up_n"}else{"completed_down_n"});
        result.events.push(event(bars,attack,"n_completed",json!({"n_id":identity,"direction":direction,"origin":a.index,"neckline":b.index,"pullback":c.index,
            "known_at":now,"defense":completion.defense,"counter_ratio":observation.anchors.as_ref().map(|a|a.retracement_ratio),"n_level":set.n_level,
            "box_anchor":target.box_anchor,"equal_wave":target.equal_wave,"one_p":target.one_p,"two_t":target.two_t})));
        // 来源标签仅隔离旧输入时期，绝不建立 TradingInterval 或正式资格。
        let source=EligibleNSource{points:[a.clone(),b.clone(),c.clone()],known_at:now,source_path:format!("legacy-unscoped-{}",set.epoch),
            window_start:set.epoch,low_index:set.epoch,low_ordinal:0};
        let measurement=TradingNMeasurement{source,setup,observation,closed_at:(finish<bars.len()-1).then_some(finish)};
        result.candidates.push(PreparedN{identity,measurement,regime,control,epoch:set.epoch,n_level:set.n_level,attack_quality_warning:warning.map(str::to_string)});
    }Ok(result)
}
