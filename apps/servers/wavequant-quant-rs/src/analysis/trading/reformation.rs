//! 轧空防守失效后的独立正N家族，保留最初起点且只使用当日已知价格。
use super::{RuleCandle,Direction,NSetup,NObservation,NOptions,PivotRef,BoxAnchorMode,MilestoneBasis,observe_n};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use std::collections::BTreeMap;

#[derive(Debug,Clone,PartialEq,Eq,Serialize,Deserialize)]
pub struct PositiveNSeed{pub origin:usize,pub attack:usize,pub known_at:usize,pub defense:Decimal,pub level:u8,pub measurement_start:Option<usize>}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ReformedPositiveN{pub setup:NSetup,pub observation:NObservation,pub previous_attack:usize,pub root_attack:usize,pub level:u8}
pub fn positive_n_identity(origin:usize,neckline:usize,pullback:usize,attack:usize)->String{format!("up:{origin}:{neckline}:{pullback}:{attack}")}
impl PositiveNSeed{
    fn validate(&self,length:usize)->Result<(),String>{
        if self.origin>=self.attack||self.attack>self.known_at||self.known_at>=length||self.defense<=Decimal::ZERO||self.measurement_start.is_some_and(|i|i>self.known_at){return Err("positive N seed must have known causal anchors and positive defense".into());}Ok(())
    }
    fn signature(&self)->String{format!("{}:{}:{}:{}:{}:{:?}",self.origin,self.attack,self.known_at,self.defense,self.level,self.measurement_start)}
}
#[derive(Debug,Clone,Serialize,Deserialize)]
struct PositiveNFamily{seed:PositiveNSeed,start:usize,attack:usize,peak:usize,index:usize,defense:Decimal,breach:Option<usize>,low:Option<usize>,finished:bool,formed:BTreeMap<String,ReformedPositiveN>}
impl PositiveNFamily{
    fn new(seed:PositiveNSeed,start:usize)->Self{let start=start.max(seed.measurement_start.unwrap_or(0));Self{attack:seed.attack,peak:seed.attack,index:seed.attack.max(start.saturating_sub(1)),defense:seed.defense,seed,start,breach:None,low:None,finished:false,formed:BTreeMap::new()}}
    fn advance(&mut self,bars:&[RuleCandle],end:usize)->Result<(),String>{
        if self.finished{self.index=end;return Ok(());}
        for now in self.index+1..=end{
            self.index=now;let bar=&bars[now];let adverse=if now==self.start{bar.close}else{bar.low};let forward=if now==self.start{bar.close}else{bar.high};
            if adverse<bars[self.seed.origin].low{self.finished=true;self.index=end;break;}
            if self.breach.is_none(){
                if adverse<self.defense{self.breach=Some(now);self.low=Some(now);}
                else{if forward>bars[self.peak].high{self.peak=now;}continue;}
            }else if self.low.is_some_and(|low|adverse<bars[low].low){self.low=Some(now);}
            let Some(low)=self.low else{continue;};
            if now<self.seed.known_at||self.peak>=low||forward<=bars[self.peak].high||bar.close<=bars[self.peak].close{continue;}
            let setup=NSetup{symbol:bar.symbol.clone(),timeframe:"1d".into(),direction:Direction::Up,
                origin:PivotRef{index:self.seed.origin,confirmed_index:self.seed.known_at,ordinal:None},
                neckline:PivotRef{index:self.peak,confirmed_index:self.seed.known_at.max(self.peak).max(self.breach.ok_or("reformation breach missing")?),ordinal:None},
                pullback:PivotRef{index:low,confirmed_index:now,ordinal:None},source:"lecture_causal".into(),box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,
                allow_confirmation_bar:true,allow_outside_close:true,staged_defense:true,allow_mother_impulse:false,allow_mother_pullback:false,allow_inside_pullback:false,polyline_structure:false};
            let options=NOptions{timeframe:"1d".into(),milestone_basis:MilestoneBasis::Extreme,asof_index:Some(now),
                measurement_start:if self.start>0{Some(self.start.max(self.seed.known_at))}else{None},measure_targets:true};
            let Ok(observation)=observe_n(bars,&setup,&options)else{continue;};
            let Some(completion)=observation.completion.as_ref().filter(|c|c.bar_index==now)else{continue;};
            let defense=completion.defense;
            self.formed.entry(positive_n_identity(self.seed.origin,self.peak,low,now)).or_insert(ReformedPositiveN{setup,observation,previous_attack:self.attack,root_attack:self.seed.attack,level:self.seed.level});
            self.attack=now;self.peak=now;self.defense=defense;self.breach=None;self.low=None;
        }Ok(())
    }
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct PositiveNReformationHistory{start:usize,index:Option<usize>,families:BTreeMap<String,PositiveNFamily>}
impl PositiveNReformationHistory{
    pub fn new(start:usize)->Self{Self{start,..Default::default()}}
    pub fn advance(&mut self,bars:&[RuleCandle],index:usize,seeds:&[PositiveNSeed])->Result<Vec<ReformedPositiveN>,String>{
        if index<self.start||index>=bars.len(){return Err("positive N history requires an available window date".into());}
        for seed in seeds{seed.validate(index+1)?;}
        if self.index.is_some_and(|prior|index<prior){self.families.clear();}
        let mut active=BTreeMap::new();let mut formed=BTreeMap::new();
        for seed in seeds{
            let mut seed=seed.clone();if self.start>0{seed.known_at=seed.known_at.max(self.start);}let signature=seed.signature();
            let mut family=active.remove(&signature).or_else(||self.families.remove(&signature)).unwrap_or_else(||PositiveNFamily::new(seed,self.start));
            family.advance(bars,index)?;
            for(identity,value)in &family.formed{formed.entry(identity.clone()).or_insert_with(||value.clone());}
            active.insert(signature,family);
        }
        self.families=active;self.index=Some(index);let mut result:Vec<_>=formed.into_values().collect();
        result.sort_by_key(|n|(n.setup.pullback.confirmed_index,n.setup.origin.index,n.previous_attack));Ok(result)
    }
}
pub fn positive_n_reformations(bars:&[RuleCandle],seeds:&[PositiveNSeed])->Result<Vec<ReformedPositiveN>,String>{
    let Some(index)=bars.len().checked_sub(1)else{return Ok(vec![]);};PositiveNReformationHistory::default().advance(bars,index,seeds)
}
