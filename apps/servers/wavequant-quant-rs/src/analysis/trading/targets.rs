//! 持久化目标状态：底点归属、资格窗口来源、折叠N与冻结后的增量测幅。
use super::{Direction,RuleCandle,NObservation,NSetup,NStatus,NOptions,PivotRef,BoxAnchorMode,MilestoneBasis,Milestone,observe_n};
use super::super::kernel::{KernelPoint,PointKind};
use super::qualification::TradingInterval;
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet,VecDeque};

#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
pub struct DeclineStart{pub index:usize,pub known_at:usize}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct PositiveNCompletion{pub origin:usize,pub attack:usize,pub known_at:usize,pub defense:Option<Decimal>,pub reformed:bool}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TargetQualification{pub eligible:bool,pub source_attack:Option<usize>,pub decline_index:Option<usize>,pub bottom_index:Option<usize>,pub reason:String}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TargetRetirement{pub attack:usize,pub bar_index:usize,pub reason:String}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct BottomNTargetState{
    minimum:VecDeque<usize>,pub current:Option<DeclineStart>,pub active:Option<PositiveNCompletion>,used:BTreeSet<(usize,usize)>,
    pub decisions:BTreeMap<usize,TargetQualification>,pub selected:BTreeMap<usize,PositiveNCompletion>,
    pub sources:Vec<Option<usize>>,pub retirements:Vec<TargetRetirement>,
}
impl BottomNTargetState{
    pub fn new(bars:&[RuleCandle],start:usize)->Result<Self,String>{
        if start>bars.len(){return Err("target window start unavailable".into());}
        let mut state=Self{sources:vec![None;start],..Default::default()};
        for i in 0..start{while state.minimum.back().is_some_and(|&j|bars[j].low>bars[i].low){state.minimum.pop_back();}state.minimum.push_back(i);}
        Ok(state)
    }
    pub fn advance(&mut self,bars:&[RuleCandle],now:usize,declines:&[DeclineStart],completions:&[PositiveNCompletion])->Result<(),String>{
        if now!=self.sources.len()||now>=bars.len(){return Err("target days must be consecutive and available".into());}
        if declines.iter().any(|v|v.index>v.known_at||v.known_at!=now)||completions.iter().any(|v|v.origin>=v.attack||v.attack>v.known_at||v.known_at!=now){return Err("target arrivals must be known today".into());}
        while self.minimum.back().is_some_and(|&j|bars[j].low>bars[now].low){self.minimum.pop_back();}self.minimum.push_back(now);
        let mut arrivals=declines.to_vec();arrivals.sort_by_key(|v|v.index);
        for decline in arrivals{
            if self.current.is_some_and(|v|decline.index<=v.index)||self.active.as_ref().is_some_and(|v|decline.index<=v.attack){continue;}
            self.current=Some(decline);
            if let Some(active)=self.active.take(){self.retirements.push(TargetRetirement{attack:active.attack,bar_index:now,reason:"new_confirmed_decline".into()});}
        }
        if let Some(active)=self.active.clone(){
            let origin=now>active.attack&&bars[now].low<bars[active.origin].low;
            let defense=now>active.attack&&active.defense.is_some_and(|p|bars[now].low<p);
            if origin||defense{
                self.retirements.push(TargetRetirement{attack:active.attack,bar_index:now,reason:if origin{"launch_origin_broken"}else{"squeeze_defense_broken"}.into()});
                if let Some(current)=self.current{self.used.remove(&(current.index,active.origin));}self.active=None;
            }
        }
        if let Some(current)=self.current{while self.minimum.front().is_some_and(|&i|i<=current.index){self.minimum.pop_front();}}
        let bottom=if self.current.is_some(){self.minimum.front().copied()}else{None};
        let mut arrivals=completions.to_vec();arrivals.sort_by_key(|v|(v.attack,Some(v.origin)!=bottom));
        for n in arrivals{
            if self.selected.contains_key(&n.attack){continue;}self.selected.insert(n.attack,n.clone());
            let identity=self.current.map(|v|(v.index,n.origin));
            let reason=if self.active.is_some(){"existing_bottom_launch"}
                else if self.current.is_none()||bottom.is_none(){"confirmed_decline_unavailable"}
                else if Some(n.origin)!=bottom||self.current.is_some_and(|v|n.origin<=v.index){"origin_is_not_decline_floor"}
                else if identity.is_some_and(|v|self.used.contains(&v)){"bottom_already_launched"}
                else{self.active=Some(n.clone());if let Some(identity)=identity{self.used.insert(identity);} "first_n_at_decline_floor"};
            self.decisions.insert(n.attack,TargetQualification{eligible:reason=="first_n_at_decline_floor",source_attack:self.active.as_ref().map(|v|v.attack),
                decline_index:self.current.map(|v|v.index),bottom_index:bottom,reason:reason.into()});
        }
        self.sources.push(self.active.as_ref().map(|v|v.attack));Ok(())
    }
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct StructuralNSelection{pub selected:bool,pub owner:Option<String>,pub trend_id:Option<String>,pub decline_index:Option<usize>,pub bottom_index:Option<usize>,pub reason:String}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct StructuralNOwnership{declines:BTreeMap<usize,BTreeSet<usize>>,owners:BTreeMap<String,(String,usize)>,owner_starts:BTreeMap<usize,BTreeSet<usize>>,index:Option<usize>,epoch:usize}
impl StructuralNOwnership{
    pub fn observe(&mut self,bars:&[RuleCandle],index:usize,epoch:usize,declines:&[DeclineStart])->Result<(),String>{
        if index>=bars.len()||self.index.is_some_and(|i|index<i)||epoch<self.epoch||declines.iter().any(|d|d.index>d.known_at||d.known_at>index){return Err("structural ownership requires known current prefix".into());}
        self.index=Some(index);self.epoch=epoch;self.declines.entry(epoch).or_default().extend(declines.iter().map(|d|d.index));Ok(())
    }
    pub fn select(&mut self,bars:&[RuleCandle],epoch:usize,origin:usize,attack:usize,identity:&str)->Result<StructuralNSelection,String>{
        if epoch!=self.epoch||origin>=attack||self.index.is_none_or(|now|attack>now)||identity.is_empty(){return Err("structural selection requires a known completed N".into());}
        let starts=self.declines.get(&epoch);let mut decline=starts.and_then(|s|s.range(..origin).next_back()).copied();
        let mut result=StructuralNSelection{selected:false,owner:None,trend_id:None,decline_index:None,bottom_index:None,reason:"confirmed_decline_unavailable".into()};
        let Some(mut start)=decline.take()else{return Ok(result);};
        if let Some(prior)=self.owner_starts.get(&epoch).and_then(|s|s.range(..=start).next_back()){
            if self.owners.get(&format!("{epoch}:{prior}")).is_some_and(|(_,attack)|start<=*attack){start=*prior;}
        }
        result.trend_id=Some(format!("level-one:{epoch}:{start}"));result.decline_index=Some(start);
        if let Some((owner,_))=self.owners.get(&format!("{epoch}:{start}")){result.owner=Some(owner.clone());result.reason="trend_already_has_successful_n".into();return Ok(result);}
        if starts.and_then(|s|s.range((std::ops::Bound::Excluded(origin),std::ops::Bound::Unbounded)).next()).is_some_and(|&v|v<=attack){result.reason="crosses_confirmed_decline".into();return Ok(result);}
        let bottom=(start+1..=attack).min_by_key(|&i|(bars[i].low,i));result.bottom_index=bottom;
        if bottom!=Some(origin){result.reason="origin_is_not_decline_floor".into();return Ok(result);}
        self.owners.insert(format!("{epoch}:{start}"),(identity.into(),attack));self.owner_starts.entry(epoch).or_default().insert(start);
        result.selected=true;result.owner=Some(identity.into());result.reason="first_successful_floor_n".into();Ok(result)
    }
}

#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct EligibleNSource{pub points:[KernelPoint;3],pub known_at:usize,pub source_path:String,pub window_start:usize,pub low_index:usize,pub low_ordinal:usize}
impl EligibleNSource{
    pub fn scope(&self)->String{format!("{}:{}",self.source_path,self.window_start)}
    pub fn identity(&self)->String{format!("polyline:{}:{}:{}",self.source_path,self.window_start,self.points.iter().map(|p|format!("{}.{}.{}",p.index,p.ordinal,p.known_index)).collect::<Vec<_>>().join(":"))}
}
fn geometry(points:&[KernelPoint])->String{points.iter().map(|p|format!("{}.{}.{}.{}",p.index,p.ordinal,p.known_index,p.value)).collect::<Vec<_>>().join(":")}
pub fn folded_n_candidates(points:&[KernelPoint],earliest:usize,direction:Direction,allow_equal_origin:bool)->Vec<[KernelPoint;3]>{
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("folded_candidates");

    let high=if direction==Direction::Up{PointKind::H}else{PointKind::L};let low=high.opposite();let sign=if direction==Direction::Up{1.0}else{-1.0};
    let points:Vec<_>=points.iter().filter(|p|p.index>=earliest).collect();let mut result=vec![];
    for(position,b)in points.iter().enumerate(){
        if b.kind!=high{continue;}let mut left=vec![];
        for p in points[..position].iter().rev(){if p.kind==high&&sign*p.value>=sign*b.value{break;}if p.kind==low&&p.index<b.index{left.push(*p);}}
        let Some(a)=left.into_iter().min_by(|a,b|(sign*a.value).total_cmp(&(sign*b.value)).then_with(||b.index.cmp(&a.index)))else{continue;};
        let mut c:Option<&KernelPoint>=None;
        for p in &points[position+1..]{
            if p.kind==high&&sign*p.value>=sign*b.value{break;}if p.kind!=low||p.index<=b.index{continue;}
            if sign*p.value<sign*a.value||(p.value==a.value&&!allow_equal_origin){c=None;break;}
            if c.is_none_or(|c|sign*p.value<sign*c.value){c=Some(p);}
        }
        if let Some(c)=c{result.push([a.clone(),(*b).clone(),c.clone()]);}
    }result
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct EligiblePolylineN{pub folded_window:Option<usize>,pub structural:bool,previous:BTreeMap<String,Vec<KernelPoint>>,seen:BTreeMap<String,BTreeSet<String>>,last:Option<usize>}
impl EligiblePolylineN{
    /// 兼容策略的 level=0 表示不限制交易区间；它不建立任何正式趋势资格。
    pub fn advance_unrestricted(&mut self,now:usize,points:&[KernelPoint],epoch:usize)->Result<Vec<EligibleNSource>,String>{
        if now!=self.last.map_or(0,|i|i+1)||epoch>now||points.iter().any(|p|p.known_index>now){return Err("unrestricted N source requires consecutive known epochs".into());}
        let path=format!("unrestricted-{epoch}");let scope=format!("{path}:{epoch}");let suffix:Vec<_>=points.iter().filter(|p|p.path==epoch&&p.index>=epoch&&p.is_closed()).cloned().collect();
        let prior=self.previous.get(&scope).map(Vec::as_slice).unwrap_or(&[]);let common=prior.iter().zip(&suffix).take_while(|(a,b)|a==b).count();
        let mut triples:Vec<_>=(2.max(common)..suffix.len()).map(|end|[suffix[end-2].clone(),suffix[end-1].clone(),suffix[end].clone()]).collect();
        if let Some(window)=self.folded_window{if prior!=suffix.as_slice(){let earliest=epoch.max(now.saturating_sub(window));
            triples.extend(folded_n_candidates(&suffix,earliest,Direction::Up,self.structural));triples.extend(folded_n_candidates(&suffix,earliest,Direction::Down,false));}}
        let seen=self.seen.entry(scope.clone()).or_default();let mut result=vec![];
        for points in triples{if seen.insert(geometry(&points)){result.push(EligibleNSource{points,known_at:now,source_path:path.clone(),window_start:epoch,low_index:epoch,low_ordinal:0});}}
        self.previous.insert(scope.clone(),suffix);self.previous.retain(|key,_|key==&scope);self.seen.retain(|key,_|key==&scope);self.last=Some(now);Ok(result)
    }
    pub fn advance(&mut self,now:usize,points:&[KernelPoint],windows:&[TradingInterval])->Result<Vec<EligibleNSource>,String>{
        if now!=self.last.map_or(0,|i|i+1)||self.folded_window==Some(0)||points.iter().any(|p|p.known_index>now){return Err("eligible N requires consecutive known prefixes".into());}
        let mut result=vec![];let mut live=BTreeSet::new();
        for w in windows.iter().filter(|w|w.start_index<=now&&w.end_index.is_none_or(|end|now<end)){
            let scope=format!("{}:{}",w.source_path,w.start_index);live.insert(scope.clone());
            let suffix:Vec<_>=points.iter().filter(|p|p.order()>=(w.start_low_index,w.start_low_ordinal)).cloned().collect();
            let prior=self.previous.get(&scope).map(Vec::as_slice).unwrap_or(&[]);let common=prior.iter().zip(&suffix).take_while(|(a,b)|a==b).count();
            let mut triples:Vec<_>=(2.max(common)..suffix.len()).map(|end|[suffix[end-2].clone(),suffix[end-1].clone(),suffix[end].clone()]).collect();
            if let Some(window)=self.folded_window{
                if prior!=suffix.as_slice(){let earliest=if self.structural{w.start_low_index}else{w.start_low_index.max(now.saturating_sub(window))};
                    triples.extend(folded_n_candidates(&suffix,earliest,Direction::Up,self.structural));triples.extend(folded_n_candidates(&suffix,earliest,Direction::Down,false));}
            }
            let seen=self.seen.entry(scope.clone()).or_default();
            for points in triples{if !seen.insert(geometry(&points)){continue;}
                result.push(EligibleNSource{points,known_at:now,source_path:w.source_path.clone(),window_start:w.start_index,low_index:w.start_low_index,low_ordinal:w.start_low_ordinal});}
            self.previous.insert(scope,suffix);
        }
        self.previous.retain(|key,_|live.contains(key));self.seen.retain(|key,_|live.contains(key));self.last=Some(now);Ok(result)
    }
}

pub fn advance_completed_n(observation:&NObservation,bar:&RuleCandle,index:usize,direction:Direction)->Result<NObservation,String>{
    let(Some(completion),Some(anchors),Some(targets))=(&observation.completion,&observation.anchors,&observation.targets)else{return Err("completed N with targets required".into());};
    if index!=observation.asof_index+1{return Err("next close required for completed N".into());}
    let mut result=observation.clone();let sign=direction.sign();let adverse=direction.adverse(bar);
    if result.first_defense_breach_index.is_none()&&sign*(adverse-completion.defense)<Decimal::ZERO{result.first_defense_breach_index=Some(index);}
    if result.first_origin_breach_index.is_none()&&sign*(adverse-anchors.origin)<Decimal::ZERO{result.first_origin_breach_index=Some(index);}
    let names=["equal_wave","one_p","two_t"];let prices=[targets.equal_wave,targets.one_p,targets.two_t];
    if result.first_origin_breach_index.is_none()&&!(direction==Direction::Up&&result.first_defense_breach_index.is_some()){
        let price=direction.extreme(bar);while result.milestones.len()<3{let next=result.milestones.len();let Some(target)=prices[next]else{break;};if sign*(price-target)<Decimal::ZERO{break;}
            let shared=result.milestones.last().is_some_and(|m|m.bar_index==index);result.milestones.push(Milestone{name:names[next].into(),target,bar_index:index,observed_at:bar.timestamp.clone(),observation_kind:"extreme".into(),shared_bar_with_previous:shared});}
    }
    result.asof_index=index;result.observed_at=bar.timestamp.clone();result.next_target=names.get(result.milestones.len()).map(|s|(*s).into());
    result.measured_wave_complete=result.milestones.len()==3;result.stacking_precondition_observed=result.measured_wave_complete;Ok(result)
}

#[derive(Debug,Clone,Serialize,Deserialize)]
struct PendingStructure{setup:NSetup,expires:usize,identity:String,source:EligibleNSource,observation:Option<NObservation>}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct StructuralNStream{pub ttl:usize,pub exclusive_targets:bool,pub events:Vec<Value>,seen:BTreeMap<String,EligibleNSource>,pending:Vec<PendingStructure>,last:Option<usize>,owners:StructuralNOwnership}
impl StructuralNStream{
    pub fn new(ttl:usize,exclusive_targets:bool)->Self{Self{ttl,exclusive_targets,events:vec![],seen:BTreeMap::new(),pending:vec![],last:None,owners:Default::default()}}
    fn event(bars:&[RuleCandle],index:usize,kind:&str,pending:&PendingStructure,fields:Value)->Value{
        let mut event=json!({"event":kind,"bar_index":index,"known_at":index,"timestamp":bars[index].timestamp,"symbol":bars[index].symbol,
            "n_id":pending.identity,"source_path":pending.source.source_path,"qualification_window_start":pending.source.window_start,
            "scope_low_index":pending.source.low_index,"scope_low_ordinal":pending.source.low_ordinal});
        if let(Some(event),Some(fields))=(event.as_object_mut(),fields.as_object()){event.extend(fields.clone());}event
    }
    pub fn advance(&mut self,bars:&[RuleCandle],index:usize,sources:&[EligibleNSource],active_scopes:&BTreeSet<String>,interrupted:bool,declines:&[DeclineStart],epoch:usize)->Result<Vec<Value>,String>{
        if index!=self.last.map_or(0,|i|i+1)||index>=bars.len(){return Err("structural N requires consecutive dates".into());}
        if sources.iter().any(|s|s.known_at!=index||!active_scopes.contains(&s.scope())||s.points.iter().any(|p|p.known_index>index)){return Err("structural N source unknown or outside current qualification".into());}
        let event_start=self.events.len();self.last=Some(index);self.owners.observe(bars,index,epoch,declines)?;
        if interrupted{
            for pending in &self.pending{if pending.observation.as_ref().is_some_and(|o|o.completion.is_some()){self.events.push(Self::event(bars,index,"n_structure_invalidated",pending,json!({"reason":"polyline_interrupted"})));}}
            self.pending.clear();self.seen.clear();return Ok(self.events[event_start..].to_vec());
        }
        for source in sources{
            let identity=source.identity();if self.seen.contains_key(&identity){continue;}self.seen.insert(identity.clone(),source.clone());
            let[a,b,c]=&source.points;if(a.kind,b.kind,c.kind)!=(PointKind::L,PointKind::H,PointKind::L)||!(a.value<=c.value&&c.value<b.value){continue;}
            let setup=NSetup{symbol:bars[0].symbol.clone(),timeframe:"1d".into(),direction:Direction::Up,source:"lecture_causal".into(),
                origin:PivotRef{index:a.index,confirmed_index:a.known_index,ordinal:Some(a.ordinal)},neckline:PivotRef{index:b.index,confirmed_index:b.known_index,ordinal:Some(b.ordinal)},
                pullback:PivotRef{index:c.index,confirmed_index:c.known_index,ordinal:Some(c.ordinal)},box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,
                allow_confirmation_bar:true,allow_outside_close:false,allow_mother_impulse:false,staged_defense:true,allow_mother_pullback:false,allow_inside_pullback:false,polyline_structure:true};
            self.pending.push(PendingStructure{setup,expires:index.saturating_add(self.ttl),identity,source:source.clone(),observation:None});
        }
        self.seen.retain(|_,s|active_scopes.contains(&s.scope()));let mut retained=vec![];let mut completed=vec![];
        for mut pending in std::mem::take(&mut self.pending){
            let prior=pending.observation.as_ref();
            if !active_scopes.contains(&pending.source.scope()){
                if prior.is_some_and(|o|o.completion.is_some()){self.events.push(Self::event(bars,index,"n_structure_invalidated",&pending,json!({"reason":"trading_eligibility_closed"})));}continue;
            }
            let observation=if let Some(prior)=prior.filter(|o|o.completion.is_some()){advance_completed_n(prior,&bars[index],index,Direction::Up)}else{
                observe_n(bars,&pending.setup,&NOptions{timeframe:"1d".into(),milestone_basis:MilestoneBasis::Extreme,asof_index:Some(index),measurement_start:None,measure_targets:true})};
            let Ok(observation)=observation else{continue;};
            let fresh=observation.completion.is_some()&&prior.is_none_or(|o|o.completion.is_none());pending.observation=Some(observation.clone());
            if fresh{completed.push(pending.clone());}
            if observation.completion.is_some(){
                if let Some(broken)=observation.first_defense_breach_index.into_iter().chain(observation.first_origin_breach_index).min(){
                    self.events.push(Self::event(bars,broken,"n_structure_invalidated",&pending,json!({"known_at":index,
                        "reason":if observation.first_origin_breach_index.is_some(){"origin_breached"}else{"defense_breached"}})));continue;
                }
            }
            if matches!(observation.status,NStatus::Invalidated|NStatus::PullbackExtended){continue;}
            if observation.completion.is_some()||index<pending.expires{retained.push(pending);}
        }
        self.pending=retained;
        completed.sort_by_key(|p|(p.observation.as_ref().and_then(|o|o.completion.as_ref()).map_or(usize::MAX,|c|c.bar_index),
            p.setup.origin.index,p.setup.origin.ordinal,p.setup.neckline.index,p.setup.neckline.ordinal,p.setup.pullback.index,p.setup.pullback.ordinal,p.identity.clone()));
        for pending in completed{
            let observation=pending.observation.as_ref().ok_or("missing completed observation")?;
            let completion=observation.completion.as_ref().ok_or("missing N completion")?;let targets=observation.targets.as_ref().ok_or("missing N targets")?;
            let mut fields=json!({"direction":"up","known_at":index,"origin":pending.setup.origin.index,"neckline":pending.setup.neckline.index,
                "pullback":pending.setup.pullback.index,"attack":completion.bar_index,"origin_ordinal":pending.setup.origin.ordinal,
                "neckline_ordinal":pending.setup.neckline.ordinal,"pullback_ordinal":pending.setup.pullback.ordinal,"defense":completion.defense,
                "real_break":completion.real_break,"virtual_break":completion.virtual_break,"box_anchor":targets.box_anchor,"equal_wave":targets.equal_wave,
                "one_p":targets.one_p,"two_t":targets.two_t,"structural_target":true,"target_eligible":false});
            if self.exclusive_targets{
                let selection=self.owners.select(bars,epoch,pending.setup.origin.index,completion.bar_index,&pending.identity)?;
                fields["structural_target_selected"]=json!(selection.selected);fields["structural_target_owner"]=json!(selection.owner);
                fields["structural_trend_id"]=json!(selection.trend_id);fields["structural_decline_index"]=json!(selection.decline_index);
                fields["structural_bottom_index"]=json!(selection.bottom_index);fields["structural_target_reason"]=json!(selection.reason);
            }
            self.events.push(Self::event(bars,completion.bar_index,"n_structure_completed",&pending,fields));
        }
        Ok(self.events[event_start..].to_vec())
    }
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TradingNMeasurement{pub source:EligibleNSource,pub setup:NSetup,pub observation:NObservation,pub closed_at:Option<usize>}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct PolylineTargetStream{
    last:Option<usize>,seen:BTreeSet<String>,pending:Vec<PendingStructure>,
    pub measurements:BTreeMap<String,TradingNMeasurement>,pub events:Vec<Value>,
}
impl PolylineTargetStream{
    pub fn advance(&mut self,bars:&[RuleCandle],index:usize,sources:&[EligibleNSource],scopes:&BTreeSet<String>,interrupted:bool,
        structure_window:usize,pattern_ttl:usize)->Result<Vec<Value>,String>{
        self.advance_with_policy(bars,index,sources,scopes,interrupted,structure_window,pattern_ttl,"lecture_causal",true)
    }
    pub fn advance_with_policy(&mut self,bars:&[RuleCandle],index:usize,sources:&[EligibleNSource],scopes:&BTreeSet<String>,interrupted:bool,
        structure_window:usize,pattern_ttl:usize,pivot_mode:&str,whole_wave:bool)->Result<Vec<Value>,String>{
        if index!=self.last.map_or(0,|i|i+1)||index>=bars.len(){return Err("trading N targets require consecutive dates".into());}
        if sources.iter().any(|s|s.known_at!=index||!scopes.contains(&s.scope())){return Err("trading N source must belong to an open current window".into());}
        let event_start=self.events.len();self.last=Some(index);let mut retained=vec![];
        for pending in std::mem::take(&mut self.pending){
            if !scopes.contains(&pending.source.scope())||interrupted{
                if let Some(observation)=pending.observation{self.measurements.insert(pending.identity,TradingNMeasurement{source:pending.source,setup:pending.setup,observation,closed_at:index.checked_sub(1)});}
            }else{retained.push(pending);}
        }self.pending=retained;
        if !interrupted{for source in sources{
            let identity=source.identity();if !self.seen.insert(identity.clone()){continue;}
            let[a,b,c]=&source.points;if source.known_at.saturating_sub(a.index)>structure_window{
                self.events.push(json!({"event":"n_candidate_skipped","bar_index":index,"reason":"expired_geometry","source_path":source.source_path,"origin":a.index}));continue;}
            let mother=whole_wave&&pivot_mode=="lecture_causal"&&a.kind==PointKind::L&&a.index==b.index&&b.index<c.index&&a.ordinal<b.ordinal;
            let same_bar=whole_wave&&pivot_mode=="lecture_causal"&&a.kind==PointKind::L&&b.kind==PointKind::H&&c.kind==PointKind::L&&a.index<b.index&&b.index==c.index&&b.ordinal<c.ordinal;
            if !(a.index<b.index&&b.index<c.index||mother||same_bar){
                self.events.push(json!({"event":"n_candidate_skipped","bar_index":index,"reason":"same_bar_n_rejected","source_path":source.source_path,
                    "origin":a.index,"neckline":b.index,"pullback":c.index}));continue;}
            let inside=same_bar&&b.index>0&&super::teaching_inside(&bars[b.index-1],&bars[b.index]);
            let direction=if a.kind==PointKind::L{Direction::Up}else{Direction::Down};
            let setup=NSetup{symbol:bars[0].symbol.clone(),timeframe:"1d".into(),direction,source:pivot_mode.into(),
                origin:PivotRef{index:a.index,confirmed_index:a.known_index,ordinal:None},neckline:PivotRef{index:b.index,confirmed_index:b.known_index,ordinal:None},
                pullback:PivotRef{index:c.index,confirmed_index:c.known_index,ordinal:None},box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,
                allow_confirmation_bar:whole_wave,allow_outside_close:whole_wave,allow_mother_impulse:mother,staged_defense:whole_wave&&direction==Direction::Up,
                allow_mother_pullback:same_bar&&!inside,allow_inside_pullback:inside,polyline_structure:false};
            self.pending.push(PendingStructure{setup,expires:source.known_at.saturating_add(pattern_ttl),identity,source:source.clone(),observation:None});
            self.events.push(json!({"event":"n_candidate_observed","bar_index":index,"known_at":source.known_at,"source_path":source.source_path,
                "origin":a.index,"neckline":b.index,"pullback":c.index,"direction":direction}));
        }}
        let mut retained=vec![];
        for mut pending in std::mem::take(&mut self.pending){
            let prior=pending.observation.as_ref();
            let observation=if let Some(prior)=prior.filter(|o|o.completion.is_some()){advance_completed_n(prior,&bars[index],index,pending.setup.direction)}
                else if let Some(prior)=prior.filter(|o|matches!(o.status,NStatus::Invalidated|NStatus::PullbackExtended)){
                    let mut observation=prior.clone();observation.asof_index=index;observation.observed_at=bars[index].timestamp.clone();Ok(observation)
                }else{observe_n(bars,&pending.setup,&NOptions{timeframe:"1d".into(),milestone_basis:MilestoneBasis::Extreme,asof_index:Some(index),
                    measurement_start:Some(pending.source.known_at),measure_targets:true})};
            let observation=match observation{Ok(o)=>o,Err(reason)=>{self.events.push(json!({"event":"n_geometry_rejected","bar_index":index,"known_at":pending.source.known_at,"setup":pending.setup,"reason":reason}));continue;}};
            // 已完成来源直接推进冻结目标，不再次寻找完成棒。
            self.measurements.insert(pending.identity.clone(),TradingNMeasurement{source:pending.source.clone(),setup:pending.setup.clone(),observation:observation.clone(),closed_at:None});
            pending.observation=Some(observation);
            if index>=pending.expires{if let Some(saved)=self.measurements.get_mut(&pending.identity){saved.closed_at=Some(index);}}
            else{retained.push(pending);}
        }
        self.pending=retained;Ok(self.events[event_start..].to_vec())
    }
}

/// 原 Python n_identity 的作用域隔离；重开窗保留原几何但产生独立交易身份。
pub fn trading_n_identity(measurement:&TradingNMeasurement)->Option<String>{
    let completion=measurement.observation.completion.as_ref()?;
    Some(format!("n:{}:{}:{}:{}:{}:{}",measurement.source.source_path,measurement.source.window_start,
        measurement.setup.origin.index,measurement.setup.neckline.index,measurement.setup.pullback.index,completion.bar_index))
}
