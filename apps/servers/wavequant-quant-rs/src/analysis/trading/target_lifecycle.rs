//! 普通N与防守失效后的新N共同竞争同一个底部测幅资格。
use super::{Direction,RuleCandle,targets::{self,TradingNMeasurement,BottomNTargetState,PositiveNCompletion,DeclineStart},reformation::{PositiveNReformationHistory,PositiveNSeed}};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

#[derive(Debug,Clone,Serialize,Deserialize)]
struct Candidate{measurement:TradingNMeasurement,previous_attack:Option<usize>,root_attack:Option<usize>,level:u8}
impl Candidate{
    fn completion(&self)->Result<PositiveNCompletion,String>{let c=self.measurement.observation.completion.as_ref().ok_or("target candidate must be completed")?;
        Ok(PositiveNCompletion{origin:self.measurement.setup.origin.index,attack:c.bar_index,known_at:self.measurement.source.known_at.max(c.bar_index),defense:Some(c.defense),reformed:self.previous_attack.is_some()})}
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct TargetLifecycle{
    original:Option<BottomNTargetState>,combined:Option<BottomNTargetState>,reformation:PositiveNReformationHistory,
    scopes:BTreeSet<String>,seen:BTreeSet<String>,published:BTreeSet<String>,retired:BTreeSet<String>,
    canonical:BTreeMap<usize,Candidate>,reformed:BTreeMap<String,Candidate>,owners:BTreeMap<usize,String>,pub events:Vec<Value>,
}
impl TargetLifecycle{
    pub fn current_measurements(&self)->Vec<TradingNMeasurement>{self.canonical.values().chain(self.reformed.values()).map(|c|c.measurement.clone()).collect()}
    pub fn advance(&mut self,bars:&[RuleCandle],now:usize,measurements:&BTreeMap<String,TradingNMeasurement>,scopes:&BTreeSet<String>,
        declines:&[DeclineStart],known_declines:&BTreeMap<usize,usize>)->Result<Vec<Value>,String>{
        let event_start=self.events.len();
        for measurement in measurements.values(){
            let Some(identity)=targets::trading_n_identity(measurement)else{continue;};
            if !scopes.contains(&measurement.source.scope())&&self.published.contains(&identity)&&self.retired.insert(format!("window:{identity}")){
                self.events.push(json!({"event":"n_target_source_retired","bar_index":now,"known_at":now,"n_id":identity,
                    "attack":measurement.observation.completion.as_ref().map(|c|c.bar_index),"reason":"trading_eligibility_closed"}));
            }
        }
        for candidate in self.reformed.values(){let m=&candidate.measurement;
            let Some(identity)=targets::trading_n_identity(m)else{continue;};
            if !scopes.contains(&m.source.scope())&&self.published.contains(&identity)&&self.retired.insert(format!("window:{identity}")){
                self.events.push(json!({"event":"n_target_source_retired","bar_index":now,"known_at":now,"n_id":identity,
                    "attack":m.observation.completion.as_ref().map(|c|c.bar_index),"reason":"trading_eligibility_closed"}));
            }
        }
        if scopes.is_empty(){self.original=None;self.combined=None;self.canonical.clear();self.reformed.clear();self.scopes.clear();return Ok(self.events[event_start..].to_vec());}
        let reopened=self.original.is_none()||(!self.scopes.is_empty()&&scopes.is_disjoint(&self.scopes));
        if reopened{self.original=Some(BottomNTargetState::new(bars,now)?);self.combined=Some(BottomNTargetState::new(bars,now)?);
            self.reformation=PositiveNReformationHistory::new(now);self.canonical.clear();self.reformed.clear();self.owners.clear();}
        let arrivals=if reopened{known_declines.iter().filter(|(_,known)|**known<=now).map(|(&index,_)|DeclineStart{index,known_at:now}).collect::<Vec<_>>()}else{declines.to_vec()};
        let mut fresh:Vec<_>=measurements.values().filter(|m|m.setup.direction==Direction::Up&&scopes.contains(&m.source.scope())&&m.observation.completion.is_some())
            .filter_map(|m|targets::trading_n_identity(m).map(|id|(id,m.clone()))).filter(|(id,_)|self.seen.insert(id.clone())).collect();
        fresh.sort_by_key(|(_,m)|m.setup.allow_inside_pullback);
        let ordinary_completions=fresh.iter().map(|(_,m)|Candidate{measurement:m.clone(),previous_attack:None,root_attack:None,level:0}.completion())
            .map(|result|result.map(|mut n|{n.known_at=now;n})).collect::<Result<Vec<_>,_>>()?;
        let original=self.original.as_mut().ok_or("original bottom tracker missing")?;original.advance(bars,now,&arrivals,&ordinary_completions)?;
        let mut ordinary_today=vec![];
        for(_,measurement)in fresh{
            let c=measurement.observation.completion.as_ref().ok_or("N completion missing")?;
            let selected=original.selected.get(&c.bar_index).ok_or("ordinary N selection missing")?;
            if selected.origin!=measurement.setup.origin.index||selected.known_at!=now||self.canonical.contains_key(&c.bar_index){continue;}
            let candidate=Candidate{measurement,previous_attack:None,root_attack:None,level:0};ordinary_today.push(candidate.clone());self.canonical.insert(selected.attack,candidate);
        }
        for candidate in self.canonical.values_mut(){
            if let Some(updated)=measurements.values().find(|m|targets::trading_n_identity(m)==targets::trading_n_identity(&candidate.measurement)){candidate.measurement=updated.clone();}
        }
        let seeds=self.canonical.values().filter(|c|scopes.contains(&c.measurement.source.scope())).filter_map(|c|{
            let n=c.measurement.observation.completion.as_ref()?;
            original.decisions.get(&n.bar_index).filter(|q|q.eligible).map(|_|PositiveNSeed{origin:c.measurement.setup.origin.index,attack:n.bar_index,
                known_at:c.measurement.source.known_at.max(n.bar_index),defense:n.defense,level:c.level,measurement_start:Some(c.measurement.source.known_at)})
        }).collect::<Vec<_>>();
        let reforms=self.reformation.advance(bars,now,&seeds)?;let mut reformed_today=vec![];
        for reform in reforms{
            let Some(owner)=self.canonical.get(&reform.root_attack)else{continue;};
            let source=owner.measurement.source.clone();let measurement=TradingNMeasurement{source,setup:reform.setup,observation:reform.observation,closed_at:None};
            let identity=targets::trading_n_identity(&measurement).ok_or("reformation missing completion")?;
            if !self.reformed.contains_key(&identity){let candidate=Candidate{measurement,previous_attack:Some(reform.previous_attack),root_attack:Some(reform.root_attack),level:reform.level};
                reformed_today.push(candidate.clone());self.reformed.insert(identity,candidate);}
        }
        for candidate in self.reformed.values_mut(){
            if !scopes.contains(&candidate.measurement.source.scope()){continue;}
            while candidate.measurement.observation.asof_index<now{let index=candidate.measurement.observation.asof_index+1;
                candidate.measurement.observation=targets::advance_completed_n(&candidate.measurement.observation,&bars[index],index,Direction::Up)?;}
        }
        let mut today=ordinary_today;today.extend(reformed_today);
        let completions=today.iter().map(|c|c.completion().map(|mut n|{n.known_at=now;n})).collect::<Result<Vec<_>,_>>()?;
        let combined=self.combined.as_mut().ok_or("combined bottom tracker missing")?;let retirement_start=combined.retirements.len();
        combined.advance(bars,now,&arrivals,&completions)?;
        for candidate in today{
            let m=&candidate.measurement;let c=m.observation.completion.as_ref().ok_or("N completion missing")?;let targets=m.observation.targets.as_ref().ok_or("N targets missing")?;
            let identity=targets::trading_n_identity(m).ok_or("N identity missing")?;
            if !self.published.insert(identity.clone()){continue;}
            let q=combined.decisions.get(&c.bar_index).ok_or("combined N qualification missing")?;let chosen=combined.selected.get(&c.bar_index).ok_or("combined N selection missing")?;
            let eligible=q.eligible&&chosen.origin==m.setup.origin.index&&chosen.known_at==now;
            if eligible{self.owners.entry(c.bar_index).or_insert_with(||identity.clone());}let primary=eligible&&self.owners.get(&c.bar_index)==Some(&identity);
            self.events.push(json!({"event":"n_completed","bar_index":c.bar_index,"known_at":now,"direction":"up","n_id":identity,
                "origin":m.setup.origin.index,"neckline":m.setup.neckline.index,"pullback":m.setup.pullback.index,"defense":c.defense,
                "outside_close_confirmed":m.setup.allow_outside_close&&m.setup.pullback.index==c.bar_index,
                "counter_ratio":m.observation.anchors.as_ref().map(|a|a.retracement_ratio),"n_level":candidate.level,
                "equal_wave":targets.equal_wave,"box_anchor":targets.box_anchor,"one_p":if primary{targets.one_p}else{None},"two_t":if primary{targets.two_t}else{None},
                "target_eligible":primary,"target_primary":candidate.previous_attack.is_none()||primary,"target_source_id":if primary{Some(identity.clone())}else{None},
                "target_source_attack":q.source_attack,"target_decline_index":q.decline_index,"target_bottom_index":q.bottom_index,
                "target_qualification_reason":if eligible&&!primary{"non_primary_n"}else{q.reason.as_str()},"source_path":m.source.source_path,
                "qualification_window_start":m.source.window_start,"window_start":m.source.known_at,
                "reformed_from":candidate.previous_attack,"reformed_root":candidate.root_attack}));
        }
        for retirement in &combined.retirements[retirement_start..]{if let Some(identity)=self.owners.get(&retirement.attack){self.events.push(json!({"event":"n_target_source_retired",
            "bar_index":retirement.bar_index,"known_at":now,"n_id":identity,"attack":retirement.attack,"reason":retirement.reason}));}}
        for candidate in self.canonical.values().chain(self.reformed.values()){
            let m=&candidate.measurement;let Some(identity)=targets::trading_n_identity(m)else{continue;};
            if !self.published.contains(&identity){continue;}
            if !scopes.contains(&m.source.scope())&&self.retired.insert(format!("window:{identity}")){self.events.push(json!({"event":"n_target_source_retired",
                "bar_index":now,"known_at":now,"n_id":identity,"attack":m.observation.completion.as_ref().map(|c|c.bar_index),"reason":"trading_eligibility_closed"}));}
            if let Some(broken)=m.observation.first_defense_breach_index.into_iter().chain(m.observation.first_origin_breach_index).min(){
                if self.retired.insert(format!("broken:{identity}")){self.events.push(json!({"event":"n_invalidated","bar_index":broken,"known_at":now.max(broken),"n_id":identity,
                    "attack":m.observation.completion.as_ref().map(|c|c.bar_index),"direction":"up","reason":if m.observation.first_origin_breach_index.is_some_and(|i|i==broken){"origin_broken"}else{"squeeze_defense_broken"}}));}
            }
        }
        self.scopes=scopes.clone();Ok(self.events[event_start..].to_vec())
    }
}
