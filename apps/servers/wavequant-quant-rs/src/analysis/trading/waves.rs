//! A/B/C 生命周期与五顶十满投射；来源 Python a_wave / wave_projection / wave_continuation。
//! 结构观察、目标触达和实际入场分别记账，首日新目标只允许使用收盘价。
use super::{RuleCandle, validate_bars, ratio, Direction};
use crate::analysis::kernel::{KernelPoint, PointKind};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use crate::indexed_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub const A_WAVE_POLICY: &str = "one_p_break_confirmed_a_origin_lifetime_v1";
pub const PROJECTION_RULE: &str = "five_top_ten_full_v3";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all="snake_case")]
pub enum AWaveClass { Ordinary, Strong }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all="snake_case")]
pub enum AWavePhase { A, B, C, Completed, Invalidated }

pub fn classify_a_attack(high:Decimal, one_p:Decimal, two_t:Decimal)->Option<AWaveClass> {
    if high<=Decimal::ZERO || one_p<=Decimal::ZERO || two_t<=one_p || high<=one_p {None}
    else {Some(if high>=two_t {AWaveClass::Strong}else{AWaveClass::Ordinary})}
}
pub fn a_origin_broken(low:Decimal, origin:Decimal)->bool {low<origin}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AWaveSeed {
    pub source_id:String, pub origin_index:usize, pub attack_index:usize, pub known_at:usize,
    pub one_p:Decimal, pub two_t:Decimal, pub defense:Decimal, pub confirm_before:Option<usize>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct AWaveTurn {pub index:usize, pub known_at:usize}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AWaveEvent {
    pub bar_index:usize, pub event:String, pub source_id:String, pub origin_index:usize, pub attack_index:usize,
    pub one_p:Decimal, pub two_t:Decimal, pub a_class:AWaveClass, pub phase:AWavePhase,
    pub confirmed_index:usize, pub strong_index:Option<usize>, pub a_high_index:usize,
    pub a_top_known_at:Option<usize>, pub b_low_index:Option<usize>, pub b_known_at:Option<usize>,
    pub c_high_index:Option<usize>, pub c_known_at:Option<usize>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct AState {
    seed:AWaveSeed, through:Option<usize>, peak:usize, observation_start:usize, ended:bool,
    phase:AWavePhase, a_class:AWaveClass, confirmed:Option<usize>, strong:Option<usize>,
    a_top_known:Option<usize>, bottom:Option<usize>, b_known:Option<usize>, c_peak:Option<usize>,
    c_known:Option<usize>, events:Vec<AWaveEvent>,
}
impl AState {
    fn new(bars:&[RuleCandle],seed:AWaveSeed,index:usize,start:usize)->Result<Self,String> {
        if seed.source_id.is_empty() || !(seed.origin_index<seed.attack_index && seed.attack_index<=seed.known_at && seed.known_at<=index)
            || seed.one_p<=Decimal::ZERO || seed.two_t<=seed.one_p || seed.defense<=Decimal::ZERO {return Err("A seeds require causal indices and positive increasing targets".into());}
        let known=seed.known_at.max(start);
        let ended=(seed.origin_index+1).max(start)..=known;
        let ended=ended.into_iter().any(|i|a_origin_broken(bars[i].low,bars[seed.origin_index].low));
        let mut peak=seed.attack_index;
        for i in seed.attack_index.max(start)..=known {if bars[i].high>bars[peak].high {peak=i;}}
        Ok(Self{seed,through:known.checked_sub(1),peak,observation_start:start,ended,phase:AWavePhase::A,
            a_class:AWaveClass::Ordinary,confirmed:None,strong:None,a_top_known:None,bottom:None,b_known:None,
            c_peak:None,c_known:None,events:vec![]})
    }
    fn emit(&mut self,now:usize,kind:&str) {
        if let Some(confirmed)=self.confirmed {self.events.push(AWaveEvent{bar_index:now,event:kind.into(),
            source_id:self.seed.source_id.clone(),origin_index:self.seed.origin_index,attack_index:self.seed.attack_index,
            one_p:self.seed.one_p,two_t:self.seed.two_t,a_class:self.a_class,phase:self.phase,confirmed_index:confirmed,
            strong_index:self.strong,a_high_index:self.peak,a_top_known_at:self.a_top_known,b_low_index:self.bottom,
            b_known_at:self.b_known,c_high_index:self.c_peak,c_known_at:self.c_known});}
    }
    fn advance(&mut self,bars:&[RuleCandle],now:usize,turns:Option<&BTreeSet<usize>>) {
        self.through=Some(now); if self.ended{return;}
        let bar=&bars[now];
        if a_origin_broken(bar.low,bars[self.seed.origin_index].low) {
            if self.confirmed.is_some(){self.phase=AWavePhase::Invalidated;self.emit(now,"a_wave_invalidated");}
            self.ended=true;return;
        }
        if self.confirmed.is_none() {
            if self.seed.confirm_before.is_some_and(|limit|now>=limit) || now>self.seed.attack_index && bar.low<self.seed.defense {self.ended=true;return;}
            let observed=if now==self.observation_start {bar.close}else{bar.high};
            if bar.high>bars[self.peak].high {self.peak=now;}
            if let Some(class)=classify_a_attack(observed,self.seed.one_p,self.seed.two_t) {
                self.confirmed=Some(now);self.a_class=class;self.strong=(class==AWaveClass::Strong).then_some(now);
                self.emit(now,"a_wave_confirmed");
            }return;
        }
        match self.phase {
            AWavePhase::Completed|AWavePhase::Invalidated=>{},
            AWavePhase::A=>{
                let extended=bar.high>bars[self.peak].high; if extended{self.peak=now;}
                if self.a_class==AWaveClass::Ordinary && bar.high>=self.seed.two_t {
                    self.a_class=AWaveClass::Strong;self.strong=Some(now);self.emit(now,"a_wave_upgraded");
                }else if extended{self.emit(now,"a_wave_extended");}
                if self.peak<now && turns.is_some_and(|points|points.contains(&self.peak)) {
                    self.phase=AWavePhase::B;self.a_top_known=Some(now);
                    self.bottom=((self.peak+1).max(self.observation_start)..=now).min_by_key(|i|bars[*i].low);
                    self.b_known=Some(now);self.emit(now,"a_wave_top_confirmed");
                }
            },
            AWavePhase::B=>{
                if self.bottom.is_some_and(|i|bar.low<bars[i].low) {self.bottom=Some(now);self.b_known=Some(now);self.emit(now,"b_wave_updated");}
                if bar.high>bars[self.peak].high {self.phase=AWavePhase::C;self.c_peak=Some(now);self.c_known=Some(now);self.emit(now,"c_wave_started");}
            },
            AWavePhase::C=>{
                if self.c_peak.is_some_and(|i|bar.high>bars[i].high) {self.c_peak=Some(now);self.c_known=Some(now);self.emit(now,"c_wave_extended");}
                if self.c_peak.is_some_and(|i|i<now && turns.is_some_and(|points|points.contains(&i))) {
                    self.phase=AWavePhase::Completed;self.c_known=Some(now);self.emit(now,"c_wave_completed");
                }
            }
        }
    }
}

fn window_end(length:usize,start:usize,end:Option<usize>)->Result<Option<usize>,String> {
    if start>=length.max(1) || end.is_some_and(|e|e<start||e>=length) {return Err("observation window must be inside the market".into());}
    Ok(end.or_else(||length.checked_sub(1)))
}
fn same_bar(a:&RuleCandle,b:&RuleCandle)->bool {
    a.symbol==b.symbol && a.timestamp==b.timestamp && a.open==b.open && a.high==b.high && a.low==b.low
        && a.close==b.close && a.volume==b.volume && a.adjustment_factor==b.adjustment_factor
        && a.raw_is_st==b.raw_is_st && a.raw_trading_active==b.raw_trading_active
}
/// 前缀只能增长；相同长度不代表相同行情。修改已冻结行情必须新建请求状态。
fn sync_market(frozen:&mut Vec<RuleCandle>,bars:&[RuleCandle])->Result<(),String> {
    if bars.len()<frozen.len() || frozen.iter().zip(bars).any(|(a,b)|!same_bar(a,b)) {
        return Err("wave market changed; create a new source-owned stream".into());
    }
    if bars.len()>frozen.len() {
        validate_bars(&bars[frozen.len().saturating_sub(1)..])?;
        frozen.extend_from_slice(&bars[frozen.len()..]);
    }Ok(())
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AWaveHistoryStream {
    bars:Vec<RuleCandle>, pub start_index:usize, pub end_index:Option<usize>, index:Option<usize>,
    pub processed_observations:usize,states:BTreeMap<String,AState>,turns:BTreeMap<usize,BTreeSet<usize>>,
}
impl AWaveHistoryStream {
    pub fn new(bars:&[RuleCandle],start:usize,end:Option<usize>)->Result<Self,String> {
        window_end(bars.len(),start,end)?;validate_bars(bars)?;
        Ok(Self{bars:bars.to_vec(),start_index:start,end_index:end,index:start.checked_sub(1),processed_observations:0,states:BTreeMap::new(),turns:BTreeMap::new()})
    }
    pub fn append_market(&mut self,bars:&[RuleCandle])->Result<(),String>{sync_market(&mut self.bars,bars)}
    pub fn advance(&mut self,index:usize,seeds:&[AWaveSeed],turns:&[AWaveTurn])->Result<Vec<AWaveEvent>,String> {
        let end=window_end(self.bars.len(),self.start_index,self.end_index)?;
        if index<self.start_index || end.is_none_or(|e|index>e) || self.index.is_some_and(|i|index<i) {return Err("A history requires a nondecreasing date in its observation window".into());}
        let identities:BTreeSet<_>=seeds.iter().map(|s|s.source_id.as_str()).collect();
        if identities.len()!=seeds.len(){return Err("A source identities must be unique".into());}
        let mut by_known:BTreeMap<usize,BTreeSet<usize>>=BTreeMap::new();
        for turn in turns {
            if turn.index>turn.known_at || turn.known_at>index {return Err("A turns must be causally available".into());}
            by_known.entry(turn.known_at.max(self.start_index)).or_default().insert(turn.index);
        }
        let old: BTreeMap<_,_>=self.turns.iter().filter(|(known,_)|self.index.is_some_and(|i|**known<=i)).collect();
        let revised:BTreeMap<_,_>=by_known.iter().filter(|(known,_)|self.index.is_some_and(|i|**known<=i)).collect();
        if old!=revised{self.states.clear();} self.turns=by_known;
        self.states.retain(|source,_|identities.contains(source.as_str()));
        for original in seeds {
            let mut seed=original.clone();
            if seed.known_at<self.start_index {
                if !(seed.origin_index<seed.attack_index && seed.attack_index<=seed.known_at) {return Err("A seed source indices are not causal".into());}
                seed.known_at=self.start_index;
            }
            if seed.confirm_before.is_some_and(|limit|limit>index) {seed.confirm_before=None;}
            let replay=self.states.get(&seed.source_id).is_some_and(|state| {
                let mut structural=state.seed.clone();structural.confirm_before=seed.confirm_before;
                structural!=seed || [state.seed.confirm_before,seed.confirm_before].into_iter().flatten()
                    .min().is_some_and(|limit|state.through.is_some_and(|through|limit<=through)) && state.seed!=seed
            });
            if replay {self.states.remove(&seed.source_id);}
            if !self.states.contains_key(&seed.source_id) {self.states.insert(seed.source_id.clone(),AState::new(&self.bars,seed.clone(),index,self.start_index)?);}
            let state=self.states.get_mut(&seed.source_id).ok_or("A source state missing")?;state.seed=seed;
            if state.ended{state.through=Some(index);continue;}
            for now in state.through.map_or(0,|i|i+1)..=index {
                state.advance(&self.bars,now,self.turns.get(&now));self.processed_observations+=1;
                if state.ended{state.through=Some(index);break;}
            }
        }
        self.index=Some(index);let mut events:Vec<_>=self.states.values().flat_map(|s|s.events.iter().cloned()).collect();
        events.sort_by(|a,b|(a.bar_index,&a.source_id).cmp(&(b.bar_index,&b.source_id)));Ok(events)
    }
}
pub fn a_wave_history(bars:&[RuleCandle],seeds:&[AWaveSeed],turns:&[AWaveTurn])->Result<Vec<AWaveEvent>,String> {
    if bars.is_empty(){return if seeds.is_empty()&&turns.is_empty(){Ok(vec![])}else{Err("A sources unavailable in empty history".into())};}
    AWaveHistoryStream::new(bars,0,None)?.advance(bars.len()-1,seeds,turns)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WaveProjectionSetup {
    pub origin_index:usize,pub attack_index:usize,pub squeeze_index:usize,
    pub origin:Decimal,pub box_anchor:Decimal,pub two_t:Decimal,pub defense:Decimal,
}
impl WaveProjectionSetup {
    pub fn validate(&self)->Result<(),String>{self.validate_direction(Direction::Up)}
    fn validate_direction(&self,direction:Direction)->Result<(),String> {
        let sign=if direction==Direction::Up{Decimal::ONE}else{-Decimal::ONE};
        if !(self.origin_index<self.attack_index && self.attack_index<self.squeeze_index)
            || !(self.origin>Decimal::ZERO&&self.defense>Decimal::ZERO&&self.box_anchor>Decimal::ZERO&&self.two_t>Decimal::ZERO
                &&sign*(self.defense-self.origin)>=Decimal::ZERO&&sign*(self.box_anchor-self.defense)>Decimal::ZERO&&sign*(self.two_t-self.box_anchor)>Decimal::ZERO)
            || self.two_t!=Decimal::from(3)*self.box_anchor-Decimal::TWO*self.origin {return Err("projection requires chronological anchors and two-T from the frozen N box".into());}Ok(())
    }
}
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum TargetPolicy {NamedStages,NearestBox}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WaveProjectionEvent {
    pub bar_index:usize,pub event:String,pub attack:usize,pub state:String,pub target:Option<Decimal>,
    pub defense:Decimal,pub two_t:Decimal,pub box_height:Decimal,pub a_origin:Decimal,pub a_high:Decimal,
    pub a_high_index:usize,pub b_low:Option<Decimal>,pub b_low_index:Option<usize>,pub reached_target:Option<Decimal>,
    pub crossed_boxes:u32,pub target_stage:String,pub projection_span:Decimal,pub reached_stage:Option<String>,pub rule:String,
}
fn projection_up()->Direction{Direction::Up}
#[derive(Debug,Clone,Serialize,Deserialize)]
struct ProjectionState {
    #[serde(default="projection_up")] direction:Direction,
    setup:WaveProjectionSetup,policy:TargetPolicy,start:usize,through:Option<usize>,ended:bool,
    peak:Decimal,peak_index:usize,a_high:Decimal,a_index:usize,b_low:Option<Decimal>,b_index:Option<usize>,
    reached:bool,state:String,target:Option<Decimal>,stage:String,span:Decimal,extension:usize,events:Vec<WaveProjectionEvent>,
}
impl ProjectionState {
    fn new(bars:&[RuleCandle],setup:WaveProjectionSetup,policy:TargetPolicy,start:usize)->Result<Self,String> {
        setup.validate()?;
        let attack=bars.get(setup.attack_index).ok_or("projection attack unavailable")?;
        let first=setup.attack_index.max(start);
        let ended=((setup.origin_index+1).max(start)..=setup.attack_index).any(|i|a_origin_broken(bars[i].low,setup.origin));
        let span=Decimal::from(3)*(setup.box_anchor-setup.origin);
        Ok(Self{direction:Direction::Up,setup,policy,start,through:first.checked_sub(1),ended,peak:attack.high,peak_index:0,
            a_high:attack.high,a_index:0,b_low:None,b_index:None,reached:false,state:"await_two_t".into(),target:None,
            stage:"five_top".into(),span,extension:0,events:vec![]}.with_attack())
    }
    fn with_attack(mut self)->Self{self.peak_index=self.setup.attack_index;self.a_index=self.setup.attack_index;self}
    fn emit(&mut self,i:usize,kind:&str,hit:Option<Decimal>,hit_stage:Option<String>) {
        self.events.push(WaveProjectionEvent{bar_index:i,event:kind.into(),attack:self.setup.attack_index,state:self.state.clone(),
            target:self.target,defense:self.setup.defense,two_t:self.setup.two_t,box_height:self.setup.box_anchor-self.setup.origin,
            a_origin:self.setup.origin,a_high:self.a_high,a_high_index:self.a_index,b_low:self.b_low,b_low_index:self.b_index,
            reached_target:hit,crossed_boxes:u32::from(hit.is_some()),target_stage:self.stage.clone(),projection_span:self.span,
            reached_stage:hit_stage,rule:PROJECTION_RULE.into()});
    }
    fn advance(&mut self,bars:&[RuleCandle],end:usize) {
        if self.ended{self.through=Some(end);return;}
        let first=self.setup.attack_index.max(self.start);
        for i in self.through.map_or(first,|j|j+1)..=end {
            self.advance_bar(&bars[i],&bars[i-1],i);
            if self.ended {break;}
        }
    }
    fn advance_bar(&mut self,bar:&RuleCandle,previous:&RuleCandle,i:usize) {
        if self.ended{return;}let sign=if self.direction==Direction::Up{Decimal::ONE}else{-Decimal::ONE};
        let high=if self.direction==Direction::Up{bar.high}else{bar.low};let low=if self.direction==Direction::Up{bar.low}else{bar.high};
        let previous_high=if self.direction==Direction::Up{previous.high}else{previous.low};let previous_low=if self.direction==Direction::Up{previous.low}else{previous.high};
        let first=self.setup.attack_index.max(self.start);
            self.through=Some(i);
            let adverse=if i==first && self.start>self.setup.attack_index{bar.close}else{low};
            if i>self.setup.attack_index && sign*(adverse-self.setup.origin)<Decimal::ZERO {
                if self.state!="await_two_t"{self.state="invalidated".into();self.target=None;self.emit(i,"wave_projection_invalidated",None,None);}
                self.ended=true;return;
            }
            let previous_peak=self.peak;
            if sign*(high-self.peak)>Decimal::ZERO{self.peak=high;self.peak_index=i;}
            let mut observed=if i==first{bar.close}else{high};
            self.reached|=sign*(observed-self.setup.two_t)>=Decimal::ZERO;
            if self.state=="await_two_t" {
                if !self.reached || i<self.setup.squeeze_index {return;}
                self.state="ready".into();self.a_high=self.peak;self.a_index=self.peak_index;
                self.emit(i,"wave_projection_ready",None,None);
                if sign*(bar.close-previous.close)>=Decimal::ZERO {return;}
            }
            if self.state!="pullback" && sign*(bar.close-previous.close)<Decimal::ZERO {
                self.state="pullback".into();self.target=None;
                let same_a=self.peak_index==self.a_index;self.a_high=self.peak;self.a_index=self.peak_index;
                if !same_a || self.b_low.is_none(){self.b_low=(i>self.peak_index).then_some(low);self.b_index=(i>self.peak_index).then_some(i);}
                else if self.b_low.is_some_and(|old|sign*(low-old)<Decimal::ZERO){self.b_low=Some(low);self.b_index=Some(i);}
                if self.stage!="five_top"{self.span=self.a_high-self.setup.origin;}
                self.emit(i,"wave_projection_pullback",None,None);return;
            }
            if self.state=="pullback" {
                if self.b_low.is_none_or(|old|sign*(low-old)<Decimal::ZERO){self.b_low=Some(low);self.b_index=Some(i);}
                if self.b_index.is_some_and(|j|j<i) && sign*(low-previous_low)>=Decimal::ZERO && sign*(bar.close-previous_high)>Decimal::ZERO && sign*(bar.close-bar.open)>Decimal::ZERO {
                    self.state="pushing".into();self.target=self.b_low.map(|low|low+if self.policy==TargetPolicy::NamedStages{self.span}else{self.a_high-self.setup.origin});
                    self.emit(i,"wave_projection_push",None,None);observed=bar.close;
                }else{return;}
            }else if self.state=="ready" {
                if sign*(high-previous_peak)<=Decimal::ZERO{return;}
                self.state="stacking".into();self.target=Some(self.setup.two_t+if self.policy==TargetPolicy::NamedStages{self.span}else{self.setup.box_anchor-self.setup.origin});
                self.emit(i,"wave_projection_stack",None,None);observed=bar.close;
            }
            if let Some(target)=self.target.filter(|target|sign*(observed-*target)>=Decimal::ZERO) {
                let hit_stage=self.stage.clone();
                if self.policy==TargetPolicy::NearestBox {
                    let box_height=self.setup.box_anchor-self.setup.origin;
                    self.target=Some(target+((observed-target)/box_height).floor()*box_height+box_height);
                    self.state="stacking".into();self.emit(i,"wave_projection_target_reached",Some(target),None);return;
                }
                if self.stage=="five_top"{self.stage="ten_full".into();}
                else{self.extension+=1;self.stage=format!("extension_{}",self.extension);}
                self.a_high=self.peak;self.a_index=self.peak_index;self.span=self.a_high-self.setup.origin;
                self.target=Some(self.a_high+self.span);self.b_low=None;self.b_index=None;self.state="stacking".into();
                self.emit(i,"wave_projection_target_reached",Some(target),Some(hit_stage));
            }

    }
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WaveProjectionHistory {bars:Vec<RuleCandle>,pub start_index:usize,pub end_index:Option<usize>,streams:BTreeMap<String,ProjectionState>}
impl WaveProjectionHistory {
    pub fn new(bars:&[RuleCandle],start:usize,end:Option<usize>)->Result<Self,String> {
        window_end(bars.len(),start,end)?;validate_bars(bars)?;
        Ok(Self{bars:bars.to_vec(),start_index:start,end_index:end,streams:BTreeMap::new()})
    }
    pub fn append_market(&mut self,bars:&[RuleCandle])->Result<(),String>{sync_market(&mut self.bars,bars)}
    pub fn advance(&mut self,setup:&WaveProjectionSetup,end:usize,policy:TargetPolicy,source_identity:Option<&str>,measurement_start:Option<usize>)->Result<Vec<WaveProjectionEvent>,String> {
        let limit=window_end(self.bars.len(),self.start_index,self.end_index)?;
        if end<self.start_index || limit.is_none_or(|i|end>i) || measurement_start.is_some_and(|i|limit.is_none_or(|end|i>end)) {return Err("projection date outside its observation window".into());}
        setup.validate()?;let start=self.start_index.max(measurement_start.unwrap_or(self.start_index));
        if setup.attack_index>end || start>end{return Ok(vec![]);}
        let key=crate::indexed_json::to_string(&(setup,policy,source_identity,start)).map_err(|e|e.to_string())?;
        if self.streams.get(&key).is_some_and(|state|state.through.is_some_and(|i|end<i)){self.streams.remove(&key);}
        if !self.streams.contains_key(&key){self.streams.insert(key.clone(),ProjectionState::new(&self.bars,setup.clone(),policy,start)?);}
        let stream=self.streams.get_mut(&key).ok_or("projection stream missing")?;stream.advance(&self.bars,end);Ok(stream.events.clone())
    }
    pub fn close(&mut self){self.streams.clear();}
}
pub fn wave_projection_history(bars:&[RuleCandle],setup:&WaveProjectionSetup,asof:Option<usize>,policy:TargetPolicy)->Result<Vec<WaveProjectionEvent>,String> {
    let end=asof.or_else(||bars.len().checked_sub(1)).ok_or("projection as-of unavailable")?;
    if end>=bars.len(){return Err("projection as-of unavailable".into());}
    WaveProjectionHistory::new(&bars[..=end],0,None)?.advance(setup,end,policy,None,None)
}

fn day(bars:&[RuleCandle],index:usize)->String {bars[index].timestamp.get(..10).unwrap_or(&bars[index].timestamp).to_string()}
fn field_price(value:&Value,key:&str)->Option<Decimal>{super::qualification::price(value,key)}
fn field_index(value:&Value,key:&str)->Option<usize>{super::qualification::index(value,key)}
fn merge_fields(value:&mut Value,fields:&Value) {if let (Some(target),Some(source))=(value.as_object_mut(),fields.as_object()){for(key,value)in source{target.insert(key.clone(),value.clone());}}}

/// 冻结昨日之前的 A 和 B，不允许今日攻击高点反向制造自己的回调结构。
pub fn wave_pullback_context(bars:&[RuleCandle],setup:&WaveProjectionSetup,now:usize,a_confirmed_at:Option<usize>)->Option<Value> {
    if !(setup.squeeze_index<now && now<bars.len()) {return None;}
    if (setup.origin_index+1..now).any(|i|a_origin_broken(bars[i].low,setup.origin)){return None;}
    let mut peak=setup.attack_index;
    for i in setup.attack_index+1..now{if bars[i].high>bars[peak].high{peak=i;}}
    let one_p=Decimal::TWO*setup.box_anchor-setup.origin;
    let a_class=classify_a_attack(bars[peak].high,one_p,setup.two_t)?;
    let confirmed=(setup.attack_index..now).find(|i|bars[*i].high>one_p)?;
    if a_confirmed_at.is_some_and(|i|i<setup.attack_index||i>=now){return None;}
    if a_confirmed_at.is_none() && (setup.attack_index+1..=confirmed).any(|i|bars[i].low<setup.defense){return None;}
    let strong=a_class==AWaveClass::Strong;let milestone=if strong{setup.two_t}else{one_p};
    if peak>=now-1 || bars[peak].high<one_p{return None;}
    let bottom=(peak+1..now).min_by_key(|i|bars[*i].low)?;
    if !(peak+1..now).any(|i|bars[i].close<bars[i-1].close){return None;}
    let amplitude=bars[peak].high-setup.origin;
    let target=bars[bottom].low+amplitude;
    let reached=(setup.attack_index..now).find(|i|if strong{bars[*i].high>=milestone}else{bars[*i].high>milestone})?;
    let squeeze_break=(peak+1..now).find(|i|bars[*i].low<setup.defense);
    let mut context=json!({
        "wave_entry_path":if strong{"two_t_held_defense_gap_attack"}else{"one_p_held_defense_rebound"},
        "wave_a_class":a_class,"wave_a_confirmed_date":day(bars,a_confirmed_at.unwrap_or(confirmed)),
        "wave_entry_one_p":one_p,"wave_entry_milestone_date":day(bars,reached.max(setup.squeeze_index)),
        "wave_entry_n_date":day(bars,setup.attack_index),"wave_entry_two_t":setup.two_t,
        "wave_entry_two_t_date":if strong{day(bars,reached.max(setup.squeeze_index))}else{String::new()},
        "wave_a_origin":setup.origin,"wave_a_origin_date":day(bars,setup.origin_index),
        "wave_a_high":bars[peak].high,"wave_a_high_index":peak,"wave_a_high_date":day(bars,peak),"wave_a_amplitude":amplitude,
        "wave_b_low":bars[bottom].low,"wave_b_low_index":bottom,"wave_b_low_date":day(bars,bottom),
        "wave_b_broke_squeeze_low":usize::from(squeeze_break.is_some()),
        "wave_b_squeeze_break_date":squeeze_break.map(|i|day(bars,i)).unwrap_or_default(),
        "wave_b_duration":bottom-peak,"wave_b_consolidation_duration":now-1-bottom,"wave_b_elapsed_duration":now-1-peak,
        "wave_duration_unit":"trading_bars","wave_c_0618_target":bars[bottom].low+ratio(618,1000)*amplitude,
        "wave_equal_target":target,"wave_defense":setup.defense
    });
    if strong{context["wave_c_1618_target"]=json!(bars[bottom].low+ratio(1618,1000)*amplitude);context["wave_c_2618_target"]=json!(bars[bottom].low+ratio(2618,1000)*amplitude);}
    Some(context)
}

pub fn wave_confirmation_state(proof:&Value)->Option<(String,Decimal)> {
    Some((proof.get("wave_confirmation_phase")?.as_str()?.into(),field_price(proof,"wave_gap_high")?))
}
pub fn wave_confirmation_is_new(proof:&Value,previous:Option<&(String,Decimal)>)->bool {
    match previous{None=>true,Some((phase,high))=>phase=="body" && proof.get("wave_confirmation_phase").and_then(Value::as_str)==Some("gap")
        && field_price(proof,"wave_gap_high").is_some_and(|value|value>*high)}
}

/// 普通 A 必须收盘突破已知阻力；强 A 使用真实跳空、量价实体或强 A 阻力再突破。
pub fn wave_gap_entry(bars:&[RuleCandle],setup:&WaveProjectionSetup,now:usize,pivots:&[KernelPoint],a_confirmed_at:Option<usize>)->Option<Value> {
    if !(setup.squeeze_index<now && now<bars.len()){return None;}
    let (bar,prev)=(&bars[now],&bars[now-1]);
    let gap=bar.open>prev.high && bar.low>prev.high;
    let body=bar.close-bar.open;let strong_body=body>=bar.open*ratio(3,100) && body>=(bar.high-bar.low)*ratio(6,10);
    let volume_up=bar.volume>prev.volume;
    if !((gap||body>Decimal::ZERO) && bar.low>=setup.origin && bar.close>bars[setup.attack_index].high){return None;}
    let mut context=wave_pullback_context(bars,setup,now,a_confirmed_at)?;
    let ordinary=context.get("wave_a_class").and_then(Value::as_str)==Some("ordinary");
    let prior_b_low=field_price(&context,"wave_b_low")?;let peak=field_index(&context,"wave_a_high_index")?;
    if !gap && bar.low<prior_b_low {
        context["wave_b_low"]=json!(bar.low);context["wave_b_low_index"]=json!(now);context["wave_b_low_date"]=json!(day(bars,now));
        context["wave_b_duration"]=json!(now-peak);context["wave_b_consolidation_duration"]=json!(0);context["wave_b_elapsed_duration"]=json!(now-peak);
        let amplitude=field_price(&context,"wave_a_amplitude")?;
        for (key,multiple) in [("wave_c_0618_target",ratio(618,1000)),("wave_equal_target",Decimal::ONE),
            ("wave_c_1618_target",ratio(1618,1000)),("wave_c_2618_target",ratio(2618,1000))] {
            if context.get(key).is_some(){context[key]=json!(bar.low+multiple*amplitude);}
        }
    }
    if bar.close>=field_price(&context,"wave_equal_target")?{return None;}
    let two_t_break=(setup.attack_index.max(setup.squeeze_index)..peak).find(|i|bars[*i-1].close<=setup.two_t
        && setup.two_t<bars[*i].close && bars[*i].close>bars[*i].open);
    let mut strong_a_rebreak=false;let mut midpoint=Decimal::ZERO;
    if !ordinary {if let Some(break_index)=two_t_break {
        midpoint=(bars[break_index].open+bars[break_index].close)/Decimal::TWO;
        let resistance=&bars[peak];
        strong_a_rebreak=peak>break_index && resistance.high>bars[peak-1].high && resistance.close<resistance.open
            && resistance.close<bars[peak-1].close && bar.low>=prior_b_low
            && (break_index+1..now).all(|i|bars[i].close>=midpoint) && bar.close>=midpoint
            && bar.close>field_price(&context,"wave_a_high")? && body>Decimal::ZERO && volume_up;
    }}
    if !ordinary && !(strong_a_rebreak||gap||strong_body&&volume_up){return None;}
    let resistance=pivots.iter().filter(|p|p.kind==PointKind::H && p.is_closed() && peak<p.index && p.index<now && p.known_index<now)
        .max_by_key(|p|(p.index,p.known_index));
    let resistance_price=resistance.and_then(|p|p.value.to_string().parse::<Decimal>().ok());
    let breakout=resistance_price.is_some_and(|price|bar.high>price);
    let body_breakout=!gap && resistance_price.is_some_and(|price|bar.close>price) && strong_body && volume_up;
    let rebound=ordinary && body>Decimal::ZERO && resistance_price.is_some_and(|price|bar.close>price);
    if !(if ordinary{rebound}else{strong_a_rebreak||gap&&(breakout||volume_up)||body_breakout}){return None;}
    if strong_a_rebreak {
        let break_index=two_t_break?;
        context["wave_entry_path"]=json!("two_t_strong_a_resistance_rebreak");context["wave_resistance_date"]=json!(day(bars,peak));
        context["wave_resistance_high"]=json!(bars[peak].high);context["wave_two_t_break_date"]=json!(day(bars,break_index));
        context["wave_two_t_body_midpoint"]=json!(midpoint);
        context["wave_five_top_target"]=json!(field_price(&context,"wave_b_low")?+setup.two_t-setup.origin);
    }
    let breakout_high=if strong_a_rebreak{field_price(&context,"wave_a_high")?}else{resistance_price.unwrap_or(Decimal::ZERO)};
    let breakout_date=if strong_a_rebreak{day(bars,peak)}else{resistance.map(|p|day(bars,p.index)).unwrap_or_default()};
    merge_fields(&mut context,&json!({
        "wave_confirmation_phase":if ordinary{"rebound"}else if gap{"gap"}else{"body"},
        "wave_gap_trigger":if rebound{"rebound_close_breakout"}else if strong_a_rebreak{"strong_a_volume_close_breakout"}
            else if body_breakout{"volume_body_breakout"}else if breakout&&volume_up{"breakout_and_volume"}else if breakout{"breakout"}else{"volume"},
        "wave_breakout_close":bar.close,"wave_body_fraction":body/bar.open,
        "wave_body_range_fraction":if bar.high>bar.low{body/(bar.high-bar.low)}else{Decimal::ZERO},
        "wave_breakout_high":breakout_high,"wave_breakout_date":breakout_date,
        "wave_gap_previous_high":prev.high,"wave_gap_high":bar.high,"wave_gap_low":bar.low,
        "wave_gap_volume":bar.volume,"wave_gap_previous_volume":prev.volume
    }));Some(context)
}

/// 只有执行层接受当前 LONG 后才调用；观察证明自身不是交易信号。
pub fn wave_entry_events(now:usize,attack:usize,stop:Decimal,proof:&Value)->Result<Vec<Value>,String> {
    let class=proof.get("wave_a_class").and_then(Value::as_str).ok_or("wave entry is missing A class")?;
    let target=field_price(proof,"wave_equal_target").ok_or("wave entry equal target missing")?;
    let mut event=if class=="ordinary" {
        json!({"event":"wave_ordinary_entry","bar_index":now,"attack":attack,
            "one_p":field_price(proof,"wave_entry_one_p").ok_or("ordinary A one-P missing")?,
            "a_high":field_price(proof,"wave_a_high").ok_or("ordinary A high missing")?,
            "two_t":field_price(proof,"wave_entry_two_t").ok_or("ordinary A two-T missing")?,
            "b_low":field_price(proof,"wave_b_low").ok_or("ordinary A B-low missing")?,
            "target":target,"target_0618":field_price(proof,"wave_c_0618_target"),"defense":stop,"owner_signal_index":now})
    }else if class=="strong"{
        json!({"event":"wave_c_equal_target","bar_index":now,"attack":attack,"target":target,"defense":stop,"owner_signal_index":now})
    }else{return Err("unknown wave A class".into());};
    if let Some(source)=proof.get("wave_a_source_id"){event["source_id"]=source.clone();}Ok(vec![event])
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WaveCandidate {
    pub source_id:String,pub setup:WaveProjectionSetup,pub known_at:usize,pub window_start:usize,
    pub retired_at:Option<usize>,pub source_fields:Value,pub require_a_confirmation:bool,
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct WaveState {
    /// 非零资格窗口从此时重新观察 A，窗口外行情只作为原始几何锚点。
    pub source_windows:BTreeMap<String,usize>,
    a_streams:BTreeMap<usize,AWaveHistoryStream>,projection_history:Option<WaveProjectionHistory>,
    pub a_confirmations:BTreeMap<String,usize>,emitted:BTreeSet<String>,
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct WaveDailyOutput {
    pub events:Vec<Value>,pub proofs:BTreeMap<String,Value>,pub entry_projections:BTreeMap<String,WaveProjectionEvent>,
}
fn emit_unique(state:&mut WaveState,event:Value,out:&mut Vec<Value>)->Result<(),String> {
    let key=crate::indexed_json::to_string(&event).map_err(|e|e.to_string())?;
    if state.emitted.insert(key){out.push(event);}Ok(())
}
fn enrich_a(event:&AWaveEvent,bars:&[RuleCandle],source:Option<&Value>)->Result<Value,String> {
    let mut value=crate::indexed_json::to_value(event).map_err(|e|e.to_string())?;
    for(key,index)in [("origin_index",Some(event.origin_index)),("attack_index",Some(event.attack_index)),
        ("confirmed_index",Some(event.confirmed_index)),("strong_index",event.strong_index),("a_high_index",Some(event.a_high_index)),
        ("a_top_known_at",event.a_top_known_at),("b_low_index",event.b_low_index),("b_known_at",event.b_known_at),
        ("c_high_index",event.c_high_index),("c_known_at",event.c_known_at)] {
        let name=if key.ends_with("_index"){key.replace("_index","_date")}else{format!("{key}_date")};
        value[&name]=json!(index.map(|i|day(bars,i)));
    }
    value["origin_price"]=json!(bars[event.origin_index].low);value["a_high_price"]=json!(bars[event.a_high_index].high);
    value["b_low_price"]=json!(event.b_low_index.map(|i|bars[i].low));value["c_high_price"]=json!(event.c_high_index.map(|i|bars[i].high));
    if let Some(fields)=source{merge_fields(&mut value,fields);}Ok(value)
}

/// 日调度仅装配已经完成的 N 与真实轧空确认，不自行重新识别趋势或确认资格。
pub fn advance_daily_waves(bars:&[RuleCandle],now:usize,seeds:&[AWaveSeed],turns:&[AWaveTurn],
    candidates:&[WaveCandidate],pivots:&[KernelPoint],state:&mut WaveState)->Result<WaveDailyOutput,String> {
    if now>=bars.len(){return Err("daily waves require an available close".into());}
    let mut output=WaveDailyOutput::default();
    let mut groups:BTreeMap<usize,Vec<AWaveSeed>>=BTreeMap::new();
    let source_fields:BTreeMap<_,_>=candidates.iter().map(|c|(c.source_id.as_str(),&c.source_fields)).collect();
    for candidate in candidates {state.source_windows.insert(candidate.source_id.clone(),candidate.window_start);}
    for seed in seeds {
        let start=state.source_windows.get(&seed.source_id).copied().unwrap_or(0);
        if start>now{return Err("A source window is in the future".into());}
        groups.entry(start).or_default().push(seed.clone());
    }
    state.a_streams.retain(|start,_|groups.contains_key(start));state.a_confirmations.clear();
    for(start,seeds)in groups {
        if !state.a_streams.contains_key(&start){state.a_streams.insert(start,AWaveHistoryStream::new(bars,start,None)?);}
        let stream=state.a_streams.get_mut(&start).ok_or("A history missing")?;stream.append_market(bars)?;
        let events=stream.advance(now,&seeds,turns)?;
        for event in events {
            if event.event=="a_wave_confirmed"{state.a_confirmations.insert(event.source_id.clone(),event.bar_index);}
            let fields=source_fields.get(event.source_id.as_str()).copied();
            emit_unique(state,enrich_a(&event,bars,fields)?,&mut output.events)?;
        }
    }
    if state.projection_history.is_none(){state.projection_history=Some(WaveProjectionHistory::new(bars,0,None)?);}
    state.projection_history.as_mut().ok_or("projection history missing")?.append_market(bars)?;
    for candidate in candidates {
        if candidate.source_id.is_empty() || candidate.known_at>now || candidate.setup.squeeze_index>now {return Err("wave candidate must be causally available".into());}
        candidate.setup.validate()?;
        let confirmed=state.a_confirmations.get(&candidate.source_id).copied();
        if candidate.require_a_confirmation && confirmed.is_none(){continue;}
        let mut source=candidate.source_fields.clone();
        if !source.is_object(){source=json!({});}
        source["n_id"]=json!(candidate.source_id);source["source_id"]=json!(candidate.source_id);
        let mut ready=crate::indexed_json::to_value(&candidate.setup).map_err(|e|e.to_string())?;merge_fields(&mut ready,&source);
        ready["event"]=json!("wave_continuation_ready");ready["bar_index"]=json!(candidate.setup.squeeze_index.max(candidate.known_at));
        emit_unique(state,ready,&mut output.events)?;
        let retirement=candidate.retired_at.filter(|retired|*retired<=now);
        let last=retirement.and_then(|retired|retired.checked_sub(1)).unwrap_or(now);
        let start=candidate.known_at.max(candidate.window_start);
        if retirement!=Some(0) && candidate.setup.squeeze_index<=last && start<=last {
            let events=state.projection_history.as_mut().ok_or("projection history missing")?.advance(&candidate.setup,last,TargetPolicy::NamedStages,
                Some(&candidate.source_id),Some(start))?;
            for event in events {
                let mut row=crate::indexed_json::to_value(&event).map_err(|e|e.to_string())?;merge_fields(&mut row,&source);
                row["origin_index"]=json!(candidate.setup.origin_index);
                row["projection_label"]=json!(match event.state.as_str(){"stacking"=>"叠箱","pushing"=>"堆箱","ready"=>"二吐完成，等待转浪",
                    "pullback"=>"B浪回调，等待再攻击","invalidated"=>"最低价跌破A起点，转浪失效",_=>""});
                emit_unique(state,row,&mut output.events)?;
            }
        }
        if let Some(retired)=retirement.filter(|retired|candidate.setup.squeeze_index.max(candidate.known_at)<*retired) {
            let mut row=json!({"event":"wave_projection_invalidated","bar_index":retired,"attack":candidate.setup.attack_index,
                "origin_index":candidate.setup.origin_index,"state":"invalidated","target":null,"target_stage":"five_top","reason":"bottom_target_source_retired"});
            merge_fields(&mut row,&source);emit_unique(state,row,&mut output.events)?;
        }
        // 命名大箱退役不终止独立 A/B/C；A 起点才是后续 C 的生命周期边界。
        if now>candidate.setup.squeeze_index && now>=candidate.window_start {
            if let Some(mut proof)=wave_gap_entry(bars,&candidate.setup,now,pivots,confirmed) {
                proof["wave_a_source_id"]=json!(candidate.source_id);
                let mut row=proof.clone();row["event"]=json!("wave_gap_observed");row["bar_index"]=json!(now);row["attack"]=json!(candidate.setup.attack_index);
                merge_fields(&mut row,&source);emit_unique(state,row,&mut output.events)?;
                output.proofs.insert(candidate.source_id.clone(),proof);
            }
        }
        if start<=now {
            let history=state.projection_history.as_mut().ok_or("projection history missing")?.advance(&candidate.setup,now,TargetPolicy::NearestBox,
                Some(&candidate.source_id),Some(start))?;
            if let Some(event)=history.last(){output.entry_projections.insert(candidate.source_id.clone(),event.clone());}
        }
    }
    output.events.sort_by_key(|event|field_index(event,"bar_index").unwrap_or(usize::MAX));Ok(output)
}

/// 单 N 仅保留当前目标状态；不持有行情数组或累计事件历史。
#[derive(Debug,Clone)]
pub struct IncrementalProjection(ProjectionState);
#[derive(Debug,Clone)]
pub struct ProjectionUpdate {pub stage:String,pub target:Option<Decimal>,pub five_top:Option<Decimal>,pub ten_full:Option<Decimal>}
impl IncrementalProjection {
    pub fn new(setup:WaveProjectionSetup,bar:&RuleCandle,now:usize,peak:Decimal,peak_index:usize)->Result<Self,String>{
        Self::directional(setup,bar,now,peak,peak_index,Direction::Up)
    }
    pub fn directional(setup:WaveProjectionSetup,_bar:&RuleCandle,now:usize,peak:Decimal,peak_index:usize,direction:Direction)->Result<Self,String>{
        setup.validate_direction(direction)?;let span=Decimal::from(3)*(setup.box_anchor-setup.origin);
        Ok(Self(ProjectionState{direction,setup,policy:TargetPolicy::NamedStages,start:now,through:now.checked_sub(1),ended:false,
            peak,peak_index,a_high:peak,a_index:peak_index,b_low:None,b_index:None,reached:false,state:"await_two_t".into(),
            target:None,stage:"five_top".into(),span,extension:0,events:vec![]}))
    }
    pub fn advance(&mut self,bar:&RuleCandle,previous:&RuleCandle,now:usize)->Result<ProjectionUpdate,String>{
        if self.0.through.is_some_and(|i|now!=i+1){return Err("projection requires consecutive dates".into());}
        self.0.advance_bar(bar,previous,now);
        let mut result=ProjectionUpdate{stage:if self.0.state=="await_two_t"{"await_two_t".into()}else{self.0.stage.clone()},target:self.0.target.or_else(||(self.0.state=="await_two_t").then_some(self.0.setup.two_t)),five_top:None,ten_full:None};
        if self.0.stage=="five_top" {result.five_top=self.0.target;}else if self.0.stage=="ten_full" {result.ten_full=self.0.target;}
        for event in &self.0.events {match event.reached_stage.as_deref(){Some("five_top")=>result.five_top=event.reached_target,Some("ten_full")=>result.ten_full=event.reached_target,_=>{}}}
        // 下跌投射允许数学结果越过零，但价格域消费者不能收到非正价格。
        if result.target.is_some_and(|price|price<=Decimal::ZERO){result.target=None;result.stage="price_target_unavailable".into();}
        result.five_top=result.five_top.filter(|price|*price>Decimal::ZERO);
        result.ten_full=result.ten_full.filter(|price|*price>Decimal::ZERO);
        self.0.events.clear();Ok(result)
    }
}

#[cfg(test)]
mod projection_regression_tests {
    use super::*;

    fn bar(day:usize,open:i64,high:i64,low:i64,close:i64)->RuleCandle {
        RuleCandle{symbol:"projection".into(),timestamp:format!("2026-01-{:02}",day+1),
            open:Decimal::from(open),high:Decimal::from(high),low:Decimal::from(low),close:Decimal::from(close),
            volume:Decimal::from(100),adjustment_factor:Decimal::ONE,raw_is_st:None,raw_trading_active:None}
    }
    fn fixture()->(Vec<RuleCandle>,WaveProjectionSetup) {
        (vec![bar(0,10,11,10,10),bar(1,11,12,11,11),bar(2,12,15,12,14),
              bar(3,14,17,14,16),bar(4,16,18,16,17),bar(5,18,23,18,22),bar(6,23,37,23,36)],
         WaveProjectionSetup{origin_index:0,attack_index:1,squeeze_index:3,
             origin:Decimal::from(10),box_anchor:Decimal::from(12),two_t:Decimal::from(16),defense:Decimal::from(11)})
    }
    #[test]
    fn named_targets_advance_causally_in_batch_and_single_bar_paths() {
        let (bars,setup)=fixture();
        let events=WaveProjectionHistory::new(&bars,3,None).unwrap()
            .advance(&setup,6,TargetPolicy::NamedStages,None,None).unwrap();
        let reached:Vec<_>=events.iter().filter_map(|event|event.reached_stage.as_deref()
            .map(|stage|(event.bar_index,stage,event.reached_target.unwrap()))).collect();
        assert_eq!(reached,vec![(5,"five_top",Decimal::from(22)),(6,"ten_full",Decimal::from(36))]);
        let mut stream=IncrementalProjection::new(setup,&bars[3],3,bars[3].high,3).unwrap();
        let ready=stream.advance(&bars[3],&bars[2],3).unwrap();
        assert!(ready.five_top.is_none());assert!(ready.ten_full.is_none());
        let stacking=stream.advance(&bars[4],&bars[3],4).unwrap();
        assert_eq!(stacking.five_top,Some(Decimal::from(22)));assert!(stacking.ten_full.is_none());
        let five=stream.advance(&bars[5],&bars[4],5).unwrap();
        assert_eq!(five.stage,"ten_full");assert_eq!(five.five_top,Some(Decimal::from(22)));
        assert_eq!(five.ten_full,Some(Decimal::from(36)));
        let ten=stream.advance(&bars[6],&bars[5],6).unwrap();
        assert_eq!(ten.stage,"extension_1");assert_eq!(ten.ten_full,Some(Decimal::from(36)));
        assert_eq!(ten.target,Some(Decimal::from(64)));
        assert!(stream.0.events.is_empty());
    }
    #[test]
    fn origin_break_removes_target_and_repeated_dates_are_rejected() {
        let (bars,setup)=fixture();
        let mut stream=IncrementalProjection::new(setup,&bars[3],3,bars[3].high,3).unwrap();
        stream.advance(&bars[3],&bars[2],3).unwrap();
        assert!(stream.advance(&bars[3],&bars[2],3).is_err());
        stream.advance(&bars[4],&bars[3],4).unwrap();
        let broken=bar(5,10,11,9,10);
        assert!(stream.advance(&broken,&bars[4],5).unwrap().target.is_none());
    }
}

#[cfg(test)]
mod inverse_projection_price_tests {
    use super::*;
    fn candle(day:usize,open:&str,high:&str,low:&str,close:&str)->RuleCandle{
        RuleCandle{symbol:"inverse".into(),timestamp:format!("2026-01-{day:02}"),open:open.parse().unwrap(),high:high.parse().unwrap(),low:low.parse().unwrap(),close:close.parse().unwrap(),
            volume:100.into(),adjustment_factor:Decimal::ONE,raw_is_st:None,raw_trading_active:None}
    }
    #[test]
    fn nonpositive_downward_price_target_is_unavailable(){
        let previous=candle(2,"4","5","3","4");let squeeze=candle(3,"3","4","1","2");
        let mut stream=IncrementalProjection::directional(WaveProjectionSetup{origin_index:0,attack_index:1,squeeze_index:3,
            origin:20.into(),box_anchor:14.into(),two_t:2.into(),defense:17.into()},&squeeze,3,1.into(),3,Direction::Down).unwrap();
        stream.advance(&squeeze,&previous,3).unwrap();
        let result=stream.advance(&candle(4,"2","2","0.5","1"),&squeeze,4).unwrap();
        assert_eq!(result.stage,"price_target_unavailable");assert!(result.target.is_none());assert!(result.five_top.is_none());
        assert!(!stream.0.ended);
    }
}
