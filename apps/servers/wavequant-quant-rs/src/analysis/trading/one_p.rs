//! one_p_breakout_entry.py：先到一饱、随后突破冻结末跌高的三日观察。
use super::{RuleCandle,qualification::price};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

#[derive(Debug,Clone,Serialize,Deserialize)]
struct Source {identity:String,path:String,attack:usize,known:usize,one_p:Decimal,reached:Option<usize>}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct OnePKey {pub level:u8,pub path:String,pub index:usize,pub ordinal:usize,pub price:Decimal,pub known:usize}
impl OnePKey {
    fn owner(&self)->String{format!("{}:{}",self.level,self.path)}
    fn identity(&self)->(u8,String,usize,usize,Decimal){(self.level,self.path.clone(),self.index,self.ordinal,self.price)}
}
#[derive(Debug,Clone,Serialize,Deserialize)]
struct Breakout {key:OnePKey,index:usize,sources:Vec<Source>}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct OnePBreakoutStream {
    pub events:Vec<Value>,index:Option<usize>,event_cursor:usize,pending_events:Vec<Value>,sources:BTreeMap<String,Source>,
    seen_sources:BTreeSet<String>,retired:BTreeSet<String>,keys:BTreeMap<String,OnePKey>,paths:BTreeSet<String>,
    crossed:BTreeSet<(u8,String,usize,usize,Decimal)>,windows:Vec<Breakout>,
}
fn base_path(mut path:&str)->&str {
    loop {if let Some((prefix,rest))=path.split_once('-'){if matches!(prefix,"reversal"|"secondary"|"tertiary"){path=rest;continue;}}return path;}
}
fn clock(value:Option<&Value>,dates:&BTreeMap<String,usize>)->Option<usize>{
    match (value?).kind() {Some(crate::indexed_json::Kind::Number(n))=>usize::try_from(n.as_u64()?).ok(),Some(crate::indexed_json::Kind::String(s))=>dates.get(s).copied(),_=>None}
}
fn string<'a>(e:&'a Value,key:&str)->Option<&'a str>{e.get(key)?.as_str()}
impl OnePBreakoutStream {
    fn publish(&mut self,bars:&[RuleCandle],index:usize,key:&OnePKey,source:&Source,breakout:usize,prior:bool){
        let Some(reached)=source.reached else{return;};
        let date=|i:usize|bars[i].timestamp.get(..10).unwrap_or(&bars[i].timestamp);
        self.events.push(json!({"event":"one_p_breakout_watch","bar_index":index,"known_at":index,
            "timestamp":bars[index].timestamp,"symbol":bars[index].symbol,"reason":"one_p_before_last_fall_high_breakout_no_entry",
            "trend_level":key.level,"source_path":key.path,"key_index":key.index,"key_date":date(key.index),"key_price":key.price,
            "key_known_index":key.known,"n_id":source.identity,"n_index":source.attack,"n_date":date(source.attack),"one_p":source.one_p,
            "one_p_known_index":source.known,"one_p_reached_index":reached,"one_p_reached_date":date(reached),
            "breakout_index":breakout,"breakout_date":date(breakout),"observation_day":index-breakout+1,"prior_breakout":prior}));
    }
    fn observe_sources(&mut self,bars:&[RuleCandle],index:usize,events:&[Value],dates:&BTreeMap<String,usize>){
        self.pending_events.extend(events.iter().skip(self.event_cursor).cloned());self.event_cursor=events.len();
        let mut pending=Vec::new();
        for e in std::mem::take(&mut self.pending_events){
            let Some(day)=clock(e.get("bar_index"),dates)else{continue;};
            let known=clock(e.get("known_at").or(e.get("bar_index")),dates);
            let (Some(known),Some(identity))=(known,string(&e,"n_id").filter(|s|!s.is_empty()))else{continue;};
            if day.max(known)>index{pending.push(e);continue;}
            if string(&e,"event")==Some("n_structure_invalidated"){self.retired.insert(identity.into());self.sources.remove(identity);continue;}
            if string(&e,"event")!=Some("n_structure_completed")||string(&e,"direction")!=Some("up")||self.seen_sources.contains(identity)||self.retired.contains(identity){continue;}
            let(Some(one_p),Some(path),Some(attack))=(price(&e,"one_p").filter(|p|*p>Decimal::ZERO),string(&e,"source_path").filter(|s|!s.is_empty()),
                clock(e.get("attack").or(e.get("bar_index")),dates))else{continue;};
            if attack>day.max(known){continue;}
            self.seen_sources.insert(identity.into());self.sources.insert(identity.into(),Source{identity:identity.into(),path:path.into(),attack,known:index,one_p,reached:None});
        }
        self.pending_events=pending;
        for source in self.sources.values_mut(){let observed=if source.known==index{bars[index].close}else{bars[index].high};
            if source.reached.is_none()&&observed>=source.one_p{source.reached=Some(index);}}
    }
    fn read_keys(&mut self,index:usize,levels:&[Value],dates:&BTreeMap<String,usize>){
        let mut keys=BTreeMap::new();let mut paths=BTreeSet::new();
        for(position,row)in levels.iter().enumerate(){
            let level=super::qualification::index(row,"trend_level").unwrap_or(position+1);if !(1..=3).contains(&level){continue;}
            for stroke in row.get("strokes").and_then(Value::as_array).into_iter().flatten(){
                let Some(path)=string(stroke,"source_path").or_else(||string(stroke,"id")).filter(|p|!p.is_empty())else{continue;};
                if row.get("_path_market_end_indices").and_then(|v|v.get(path)).and_then(Value::as_u64).is_some_and(|i|index as u64>=i){continue;}
                let owner=format!("{level}:{path}");paths.insert(owner.clone());
                for point in stroke.get("points").and_then(Value::as_array).into_iter().flatten().rev(){
                    if matches!(string(point,"state"),Some("seed"|"developing"))||point.get("display_only").and_then(Value::as_bool)==Some(true)
                        ||string(point,"wave_identity_state")==Some("direction_only"){continue;}
                    let Some(context)=point.get("lifecycle_context").filter(|v|v.is_object())else{continue;};
                    let(Some(known),Some(point_known))=(clock(context.get("available_at"),dates),clock(point.get("available_at").or(context.get("available_at")),dates))else{continue;};
                    if known.max(point_known)>index{continue;}
                    if string(context,"direction")!=Some("up"){break;}
                    let Some(key)=context.get("key").filter(|v|string(v,"kind")==Some("H"))else{break;};
                    let (Some(key_index),Some(key_known),Some(value))=(clock(key.get("index"),dates),clock(key.get("available_at"),dates),price(key,"value"))else{break;};
                    let ordinal=super::qualification::index(key,"ordinal").unwrap_or(0);
                    if key_index<=key_known&&key_known<=index&&value>Decimal::ZERO{keys.insert(owner,OnePKey{level:level as u8,path:path.into(),index:key_index,ordinal,price:value,known:known.max(point_known).max(key_known)});}
                    break;
                }
            }
        }
        self.keys=keys;self.paths=paths;
    }
    pub fn advance(&mut self,bars:&[RuleCandle],index:usize,levels:&[Value],structural_events:&[Value])->Result<Vec<Value>,String>{
        if index!=self.index.map_or(0,|i|i+1)||index>=bars.len(){return Err("one-p observation requires consecutive market dates".into());}
        let start=self.events.len();self.index=Some(index);
        let mut retained=vec![];
        for mut window in std::mem::take(&mut self.windows){
            if index>window.index+2||!self.paths.contains(&window.key.owner()){continue;}
            window.sources.retain(|s|self.sources.contains_key(&s.identity));if window.sources.is_empty(){continue;}
            for source in &window.sources{self.publish(bars,index,&window.key,source,window.index,true);}
            retained.push(window);
        }
        self.windows=retained;
        for key in self.keys.values().cloned().collect::<Vec<_>>(){
            if self.crossed.contains(&key.identity())||key.known>=index{continue;}
            let sources:Vec<_>=self.sources.values().filter(|s|base_path(&s.path)==base_path(&key.path)&&s.attack>key.index&&s.known<index&&s.reached.is_some_and(|i|i<index)).cloned().collect();
            for source in &sources{self.publish(bars,index,&key,source,index,false);}
            if bars[index].high>key.price{self.crossed.insert(key.identity());if !sources.is_empty(){self.windows.push(Breakout{key,index,sources});}}
        }
        let dates=bars[..=index].iter().enumerate().map(|(i,b)|(b.timestamp.get(..10).unwrap_or(&b.timestamp).to_owned(),i)).collect();
        self.observe_sources(bars,index,structural_events,&dates);self.read_keys(index,levels,&dates);
        Ok(self.events[start..].to_vec())
    }
}
