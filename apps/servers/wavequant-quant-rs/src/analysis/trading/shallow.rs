//! shallow_base_breakout.py：浅回撤仅产生独立候选，不能冒充正式空多交替。
use super::{RuleCandle,qualification::{index,price}};
use super::super::kernel::{KernelFrame,PointKind};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ShallowAlternationCandidate{
    pub trend_level:u8,pub origin_index:usize,pub origin_price:Decimal,pub flip_high_index:usize,pub flip_high_price:Decimal,
    pub pullback_index:usize,pub pullback_price:Decimal,pub known_index:usize,pub retracement:Decimal,
}
impl ShallowAlternationCandidate{pub fn identity(&self)->(u8,usize,usize,usize){(self.trend_level,self.origin_index,self.flip_high_index,self.pullback_index)}}
#[derive(Clone)]
struct Anchor{level:u8,origin:usize,origin_price:Decimal,high:usize,high_price:Decimal,known:usize,path:String}
fn clock(value:&Value,dates:&BTreeMap<String,usize>)->Option<usize>{value.as_u64().and_then(|v|usize::try_from(v).ok()).or_else(||dates.get(value.as_str()?).copied())}
fn point_known(p:&Value,dates:&BTreeMap<String,usize>)->Option<usize>{clock(p.get("available_at").or_else(||p.get("known_index"))?,dates)}
fn make(anchor:&Anchor,pullback:usize,counter:Decimal,known:usize)->Option<ShallowAlternationCandidate>{
    if !(anchor.origin<anchor.high&&anchor.high<pullback&&anchor.origin_price<counter&&counter<anchor.high_price)
        ||2*(pullback-anchor.high)>=anchor.high-anchor.origin{return None;}
    let span=anchor.high_price-anchor.origin_price;let depth=anchor.high_price-counter;
    if depth*Decimal::from(1000)<span*Decimal::from(618)||depth*Decimal::from(3)>=span*Decimal::TWO{return None;}
    Some(ShallowAlternationCandidate{trend_level:anchor.level,origin_index:anchor.origin,origin_price:anchor.origin_price,
        flip_high_index:anchor.high,flip_high_price:anchor.high_price,pullback_index:pullback,pullback_price:counter,
        known_index:anchor.known.max(known),retracement:depth/span})
}
/// 兼容 Python geometry 输入；available_at 可为原日期或 Rust 已知坐标。
pub fn shallow_candidate_from_geometry(levels:&[(Value,Value)],dates:&BTreeMap<String,usize>,now:usize)->Option<ShallowAlternationCandidate>{
    let mut candidates=vec![];
    for(level,source)in levels{
        let Some(number)=index(level,"trend_level").and_then(|v|u8::try_from(v).ok())else{continue;};let mut anchors=BTreeMap::<(usize,usize),Anchor>::new();
        let paths:BTreeMap<_,_>=level.get("strokes").and_then(Value::as_array).into_iter().flatten().filter_map(|p|Some((p.get("id")?.as_str()?,p))).collect();
        let sources:BTreeMap<_,_>=source.get("strokes").and_then(Value::as_array).into_iter().flatten().filter_map(|p|Some((p.get("id")?.as_str()?,p))).collect();
        for high in level.get("bear_to_bull_highs").and_then(Value::as_array).into_iter().flatten(){
            let Some(origin)=high.get("confirmed_low")else{continue;};
            let(Some(o),Some(h),Some(low),Some(peak),Some(known),Some(path))=(index(origin,"index"),index(high,"index"),price(origin,"value"),price(high,"value"),point_known(high,dates),high.get("source_path").and_then(Value::as_str))else{continue;};
            anchors.insert((o,h),Anchor{level:number,origin:o,origin_price:low,high:h,high_price:peak,known,path:path.into()});
        }
        for path in paths.values(){
            if path.get("display_only").and_then(Value::as_bool)==Some(true){continue;}let Some(low)=path.get("points").and_then(Value::as_array).and_then(|p|p.last())else{continue;};
            if low.get("kind").and_then(Value::as_str)!=Some("L")||low.get("flip").and_then(Value::as_str)!=Some("翻空为多"){continue;}
            let Some(key)=low.get("preceding_turn")else{continue;};if key.get("kind").and_then(Value::as_str)!=Some("H"){continue;}
            let(Some(o),Some(k),Some(low_price),Some(key_price),Some(source_id))=(index(low,"index"),index(key,"index"),price(low,"value"),price(key,"value"),path.get("source_path").and_then(Value::as_str))else{continue;};
            let Some(source)=sources.get(source_id).filter(|p|p.get("display_only").and_then(Value::as_bool)!=Some(true))else{continue;};
            let high=source.get("points").and_then(Value::as_array).into_iter().flatten().filter(|p|p.get("kind").and_then(Value::as_str)==Some("H")
                &&index(p,"index").is_some_and(|j|j>o)&&p.get("state").and_then(Value::as_str)!=Some("developing")&&p.get("display_only").and_then(Value::as_bool)!=Some(true)
                &&point_known(p,dates).is_some_and(|j|j<=now)).filter_map(|p|Some((p,index(p,"index")?,price(p,"value")?)))
                .max_by(|(_,ai,a),(_,bi,b)|a.cmp(b).then_with(||bi.cmp(ai)));
            let Some((high,h,peak))=high else{continue;};if !(k<o&&o<h&&peak>key_price&&key_price>low_price){continue;}
            let(Some(lk),Some(hk),Some(kk),Some(id))=(point_known(low,dates),point_known(high,dates),point_known(key,dates),path.get("id").and_then(Value::as_str))else{continue;};
            anchors.entry((o,h)).or_insert(Anchor{level:number,origin:o,origin_price:low_price,high:h,high_price:peak,known:lk.max(hk).max(kk),path:id.into()});
        }
        let mut latest=BTreeMap::<usize,Anchor>::new();for a in anchors.into_values(){if latest.get(&a.origin).is_none_or(|old|a.high>old.high){latest.insert(a.origin,a);}}
        for a in latest.into_values(){if a.known>now{continue;}
            let Some(source_path)=paths.get(a.path.as_str()).and_then(|p|p.get("source_path")).and_then(Value::as_str).and_then(|id|sources.get(id))else{continue;};
            let lows=source_path.get("points").and_then(Value::as_array).into_iter().flatten().filter(|p|p.get("kind").and_then(Value::as_str)==Some("L")
                &&p.get("state").and_then(Value::as_str)!=Some("developing")&&p.get("display_only").and_then(Value::as_bool)!=Some(true))
                .filter_map(|p|Some((index(p,"index")?,price(p,"value")?,point_known(p,dates)?)))
                .filter(|(j,_,k)|a.high<*j&&*j<=now&&*k<=now).min_by_key(|(j,p,_)|(*p,*j));
            if let Some((j,p,k))=lows{candidates.extend(make(&a,j,p,k));}
        }
    }
    candidates.into_iter().max_by_key(|c|(c.flip_high_index,c.trend_level))
}
/// 内核生命周期提供正式翻多证明，A/B 端点仍取直接下级闭合点，不从每日极值补点。
pub fn shallow_candidate_from_frames(frames:&[KernelFrame],now:usize)->Option<ShallowAlternationCandidate>{
    let mut candidates=vec![];
    for level in frames.iter().filter(|f|(2..=3).contains(&f.level)){
        let Some(source)=frames.iter().find(|f|f.level+1==level.level)else{continue;};let mut anchors=BTreeMap::<(usize,usize),Anchor>::new();
        for e in &level.events{
            if e.known_index>now{continue;}let Some(proof)=e.evidence.get("lifecycle_event")else{continue;};
            if proof.get("direction").and_then(Value::as_str)!=Some("up")||proof.get("flip_confirmed").and_then(Value::as_bool)!=Some(true){continue;}
            let(Some(o),Some(h),Some(k),Some(low),Some(peak),Some(key))=(index(proof,"origin_index"),index(proof,"flip_index"),index(proof,"broken_key_index"),
                price(proof,"origin_price"),price(proof,"breakout_level"),price(proof,"broken_key_price"))else{continue;};
            if !(k<o&&o<h&&low<key&&key<peak){continue;}
            let Some(path)=e.origin.as_ref().map(|p|p.path).or_else(||proof.get("source_path").and_then(Value::as_str).and_then(|s|s.rsplit('-').next()).and_then(|s|s.parse::<usize>().ok()))else{continue;};
            let high=source.points.iter().filter(|p|p.path==path&&p.kind==PointKind::H&&p.is_closed()&&p.index>o&&p.known_index<=now)
                .max_by(|a,b|a.value.total_cmp(&b.value).then_with(||b.index.cmp(&a.index)));
            let Some(high)=high else{continue;};let Ok(high_price)=high.value.to_string().parse::<Decimal>()else{continue;};if high_price<=key{continue;}
            let known=e.known_index.max(high.known_index);let a=Anchor{level:level.level,origin:o,origin_price:low,high:high.index,high_price,known,path:path.to_string()};
            anchors.entry((o,a.high)).or_insert(a);
        }
        let mut latest=BTreeMap::<usize,Anchor>::new();for a in anchors.into_values(){if latest.get(&a.origin).is_none_or(|p|a.high>p.high){latest.insert(a.origin,a);}}
        for a in latest.into_values(){let Ok(path)=a.path.parse::<usize>()else{continue;};
            let low=source.points.iter().filter(|p|p.path==path&&p.kind==PointKind::L&&p.is_closed()&&p.index>a.high&&p.index<=now&&p.known_index<=now)
                .min_by(|a,b|a.value.total_cmp(&b.value).then(a.index.cmp(&b.index)));
            if let Some(low)=low{if let Ok(price)=low.value.to_string().parse::<Decimal>(){candidates.extend(make(&a,low.index,price,low.known_index));}}
        }
    }
    candidates.into_iter().max_by_key(|c|(c.flip_high_index,c.trend_level))
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct ShallowBaseState{last:Option<usize>,previous:Option<ShallowAlternationCandidate>,seen:BTreeSet<(u8,usize,usize,usize)>,used:BTreeSet<(u8,usize,usize,usize)>}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct ShallowBaseEvaluation{pub events:Vec<Value>,pub breakout:Option<Value>}
impl ShallowBaseState{
    pub fn advance(&mut self,bars:&[RuleCandle],now:usize,current:Option<ShallowAlternationCandidate>,minimum_reward_risk:Decimal,entry_allowed:bool)->Result<ShallowBaseEvaluation,String>{
        if now>=bars.len()||self.last.map_or(0,|i|i+1)!=now||minimum_reward_risk<Decimal::ZERO||current.as_ref().is_some_and(|c|c.known_index>now){return Err("shallow candidate requires consecutive known bars and valid reward/risk".into());}
        let mut output=ShallowBaseEvaluation::default();if now>0{if let Some(c)=&current{if self.seen.insert(c.identity()){
            output.events.push(json!({"bar_index":now,"event":"shallow_alternation_candidate","trend_level":c.trend_level,"origin_index":c.origin_index,"flip_high_index":c.flip_high_index,
                "pullback_index":c.pullback_index,"pullback_price":c.pullback_price,"retracement":c.retracement,"status":"pending_base_breakout_not_formal_alternation"}));
        }}}
        let previous=self.previous.take();self.previous=current;self.last=Some(now);let Some(c)=previous else{return Ok(output);};
        if self.used.contains(&c.identity())||!entry_allowed||c.pullback_index>=now{return Ok(output);}
        let sessions=now-c.pullback_index-1;if !(40..=120).contains(&sessions)||now<40||c.known_index>=now{return Ok(output);}
        if bars[c.pullback_index..=now].iter().any(|b|b.low<c.pullback_price){return Ok(output);}
        let base=&bars[now-40..now];let high=base.iter().map(|b|b.high).max().ok_or("base missing")?;let low=base.iter().map(|b|b.low).min().ok_or("base missing")?;
        if low<=Decimal::ZERO||(high-low)*Decimal::from(100)>low*Decimal::from(18){return Ok(output);}
        let b=&bars[now];let p=&bars[now-1];let spread=b.high-b.low;let body=b.close-b.open;
        if bars[c.pullback_index+1..now].iter().any(|p|b.close<=p.high)||b.close<=high||body<=Decimal::ZERO||spread<=Decimal::ZERO
            ||body*Decimal::from(100)<b.open*Decimal::from(5)||body*Decimal::from(5)<spread*Decimal::from(3)||(b.close-b.low)*Decimal::from(5)<spread*Decimal::from(4){return Ok(output);}
        let average=bars[now-20..now].iter().map(|b|b.volume).sum::<Decimal>()/Decimal::from(20);
        if average<=Decimal::ZERO||b.volume<Decimal::TWO*average||b.volume<=p.volume||!(low<b.close&&b.close<c.flip_high_price){return Ok(output);}
        let rr=(c.flip_high_price-b.close)/(b.close-low);if rr<minimum_reward_risk{return Ok(output);}self.used.insert(c.identity());
        output.breakout=Some(json!({"buy_point_type":"shallow_base_breakout","trend_level":c.trend_level,"origin_index":c.origin_index,"origin_price":c.origin_price,
            "flip_high_index":c.flip_high_index,"flip_high_price":c.flip_high_price,"alternation_low_index":c.pullback_index,"alternation_low_price":c.pullback_price,
            "candidate_known_index":c.known_index,"counter_ratio":c.retracement,"counter_limit":Decimal::new(618,3),"base_start_index":now-40,"base_end_index":now-1,
            "base_sessions":sessions,"base_high":high,"base_low":low,"base_width_fraction":(high-low)/low,"breakout_close":b.close,"breakout_body_fraction":body/b.open,
            "breakout_body_range_fraction":body/spread,"breakout_volume":b.volume,"previous_volume":p.volume,"base_volume_average":average,"breakout_volume_multiple":b.volume/average,
            "stop":low,"target":c.flip_high_price,"gross_reward_risk":rr,"attack":now,"squeeze_confirmation":"shallow_base_breakout"}));Ok(output)
    }
}
