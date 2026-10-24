use super::{RuleCandle,GeometryPath,GeometryPoint,SecondaryHigh,date_at,number};
use crate::analysis::kernel::{KernelPoint,PointKind,PointState,certify_local_publication};
use rust_decimal::{Decimal,prelude::ToPrimitive};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct CombinedAContext {pub source_path:String,pub origin_index:usize,pub origin_price:Decimal,
    pub a_index:usize,pub a_price:Decimal,pub b_index:usize,pub b_price:Decimal,pub c_index:usize,pub c_price:Decimal,
    pub known_index:usize,pub n_index:usize,pub child_pullback_durations:Vec<usize>,pub rebound_highs:Vec<SecondaryHigh>,pub trend_level:u8}
impl CombinedAContext {fn identity(&self)->(usize,usize){(self.origin_index,self.c_index)}}
#[derive(Debug,Clone)]
struct PositiveN {origin:usize,attack:usize,known:usize,one_p:Decimal,two_t:Decimal}
fn index(v:&Value,key:&str)->Option<usize>{v.get(key)?.as_u64().and_then(|i|usize::try_from(i).ok())}
fn positive_ns(bars:&[RuleCandle],drawing:&[GeometryPath],now:usize,audit:&[Value])->Vec<PositiveN>{
    let mut result=BTreeMap::new();
    for event in audit {
        if event.get("event").and_then(Value::as_str)!=Some("n_completed")||event.get("direction").and_then(Value::as_str)!=Some("up"){continue;}
        let(Some(origin),Some(high),Some(low),Some(attack))=(index(event,"origin"),index(event,"neckline"),index(event,"pullback"),index(event,"bar_index"))else{continue;};
        let known=index(event,"known_at").unwrap_or(attack).max(attack);
        let(Some(one_p),Some(two_t))=(event.get("one_p").and_then(number),event.get("two_t").and_then(number))else{continue;};
        if origin<high&&high<low&&low<=attack&&known<=now{result.insert((origin,attack),PositiveN{origin,attack,known,one_p,two_t});}
    }
    for path in drawing {for points in path.points.windows(4){let(a,b,c,d)=(&points[0],&points[1],&points[2],&points[3]);
        if a.state!="seed"||!points.iter().zip(["L","H","L","H"]).all(|(p,k)|p.known(bars,k,now,true))
            ||!(a.index<b.index&&b.index<c.index&&c.index<d.index&&a.price<c.price&&c.price<b.price&&b.price<d.price)
            ||bars[d.index].close<=b.price{continue;}
        result.entry((a.index,d.index)).or_insert(PositiveN{origin:a.index,attack:d.index,known:d.index,
            one_p:Decimal::TWO*d.price-a.price,two_t:Decimal::from(3)*d.price-Decimal::TWO*a.price});
    }}result.into_values().collect()
}
fn shares_source(path:&GeometryPath,source:&str,paths:&BTreeMap<String,&GeometryPath>)->bool{
    let mut current=Some(path);let mut visited=BTreeSet::new();
    while let Some(path)=current{if path.id==source||path.source_path.as_deref()==Some(source){return true;}
        if !visited.insert(path.id.clone()){break;}current=path.source_path.as_ref().and_then(|s|paths.get(s).copied());}
    false
}
fn kernel_point(point:&GeometryPoint)->Result<KernelPoint,String>{Ok(KernelPoint::new(crate::analysis::kernel::KernelPointData{index:point.index,ordinal:point.ordinal,time:point.index.to_string(),
    kind:if point.kind=="H"{PointKind::H}else{PointKind::L},value:point.price.to_f64().ok_or("local price exceeds finite representation")?,
    known_index:point.known,level:1,path:0,state:match point.state.as_str(){"seed"=>PointState::Seed,"developing"=>PointState::Developing,_=>PointState::Confirmed},evidence:point.evidence.clone()}))}
/// 私有测幅路径遵循原严格关键位降采样；不进入正式多级趋势递归。
pub fn reduce_local_points(points:&[GeometryPoint])->Result<Vec<GeometryPoint>,String>{
    let(mut highs,mut lows,mut candidates)=(Vec::<usize>::new(),Vec::<usize>::new(),Vec::<GeometryPoint>::new());
    let(mut direction,mut anchor,mut key)=(None::<bool>,None::<usize>,None::<usize>);
    for(position,point)in points.iter().enumerate(){
        if point.kind=="H"{highs.push(position);}else{lows.push(position);}
        if direction.is_none(){
            if highs.len()<2||lows.len()<2{continue;}
            let high_change=points[highs[highs.len()-1]].price-points[highs[highs.len()-2]].price;
            let low_change=points[lows[lows.len()-1]].price-points[lows[lows.len()-2]].price;
            direction=if high_change>Decimal::ZERO&&low_change>Decimal::ZERO{Some(true)}else if high_change<Decimal::ZERO&&low_change<Decimal::ZERO{Some(false)}else{None};
            let Some(up)=direction else{continue;};let pool=if up{&highs}else{&lows};
            let selected=if up{pool.iter().copied().max_by_key(|&i|(points[i].price,std::cmp::Reverse(i)))}else{pool.iter().copied().min_by_key(|&i|(points[i].price,i))};
            anchor=if selected==Some(0){pool.last().copied()}else{selected};key=anchor.and_then(|a|a.checked_sub(1));continue;
        }
        let up=direction.ok_or("local direction unavailable")?;let mut start=anchor.ok_or("local anchor unavailable")?;
        let target=if up{"H"}else{"L"};let sign=if up{Decimal::ONE}else{-Decimal::ONE};
        if point.kind==target&&sign*(point.price-points[start].price)>Decimal::ZERO{start=position;anchor=Some(position);key=position.checked_sub(1);}
        let Some(key_index)=key else{continue;};
        if point.kind==target||sign*(point.price-points[key_index].price)>=Decimal::ZERO{continue;}
        let mut candidate=points[start].clone();candidate.known=point.known;candidate.flip=Some(if up{"翻多为空"}else{"翻空为多"}.into());
        candidate.confirmation_rule=Some("n_origin_local_structural_key_break".into());candidate.broken_key=Some(Box::new(points[key_index].clone()));
        candidate.evidence=json!({"confirmation_rule":"n_origin_local_structural_key_break","local_wave_turn":if up{"up_to_down"}else{"down_to_up"},
            "flip":candidate.flip,"broken_key":kernel_point(&points[key_index])?,"confirmed_by":kernel_point(point)?,
            "wave_direction_before":if up{"up"}else{"down"},"wave_direction_after":if up{"down"}else{"up"},
            "source_level2_position":start,"source_level2_available_at":points[start].known,"confirmed_on_level2":position});
        candidates.push(candidate);direction=Some(!up);
        let pool:Vec<_>=(start+1..=position).filter(|&i|points[i].kind==if up{"L"}else{"H"}).collect();
        anchor=if up{pool.iter().copied().min_by_key(|&i|(points[i].price,i))}else{pool.iter().copied().max_by_key(|&i|(points[i].price,std::cmp::Reverse(i)))};
        key=anchor.and_then(|a|a.checked_sub(1));
    }
    let source=points.iter().map(kernel_point).collect::<Result<Vec<_>,_>>()?;
    let raw=candidates.iter().map(kernel_point).collect::<Result<Vec<_>,_>>()?;
    let published=certify_local_publication(&raw,&source)?;
    published.into_iter().map(|point|{
        let mut geometry=candidates.iter().find(|p|p.index==point.index&&p.ordinal==point.ordinal).cloned().ok_or("local publication changed source identity")?;
        geometry.known=point.known_index;geometry.evidence=point.evidence.clone();
        geometry.confirmation_rule=geometry.evidence.get("confirmation_rule").and_then(Value::as_str).map(str::to_string);Ok(geometry)
    }).collect()
}
fn child_durations(bars:&[RuleCandle],points:&[GeometryPoint],origin:usize,c:usize,now:usize,positive:&[PositiveN])->Vec<usize>{
    let mut children=vec![];
    for n in positive.iter().filter(|n|origin<n.origin&&n.origin<n.attack&&n.attack<c&&n.known<=now){
        for p in points.windows(3){let(a,b,end)=(&p[0],&p[1],&p[2]);
            if !p.iter().zip(["H","L","H"]).all(|(p,k)|p.known(bars,k,now,true))||!(n.attack<a.index&&a.index<b.index&&b.index<end.index&&end.index<=c)
                ||a.flip.as_deref()!=Some("翻多为空")||end.flip.as_deref()!=Some("翻多为空")
                ||!(bars[n.origin].low<b.price&&b.price<a.price&&a.price<end.price)||a.price<=n.one_p{continue;}
            if bars[n.origin..=a.index].iter().map(|b|b.high).max()!=Some(a.price){continue;}
            children.push((a.index,b.index,b.index-a.index));
        }
    }
    children.into_iter().max().map(|(_,_,duration)|vec![duration]).unwrap_or_default()
}
pub fn combined_a_contexts_from_geometry(bars:&[RuleCandle],drawing:&[GeometryPath],first:&[GeometryPath],second:&[GeometryPath],third:&[GeometryPath],now:usize,audit:&[Value])->Result<Vec<CombinedAContext>,String>{
    if now>=bars.len(){return Ok(vec![]);}let positive=positive_ns(bars,drawing,now,audit);
    let paths:BTreeMap<_,_>=first.iter().chain(second).chain(third).map(|p|(p.id.clone(),p)).collect();
    let mut groups=BTreeMap::<(usize,usize),(CombinedAContext,bool)>::new();
    for n in &positive {
        let origin=n.origin;let origin_price=bars[origin].low;
        if n.known>now||!(origin_price<n.one_p&&n.one_p<n.two_t){continue;}
        let mut wave_paths:Vec<_>=second.iter().cloned().map(|p|(p,false)).collect();
        for source in first {
            let points:Vec<_>=source.points.iter().filter(|p|p.index>=origin&&p.known(bars,&p.kind,now,true)).cloned().collect();
            if !points.first().is_some_and(|p|p.index==origin&&p.kind=="L"){continue;}
            wave_paths.push((GeometryPath{id:format!("ordinary-local:{}:{origin}",source.id),source_path:Some(source.id.clone()),source_paths:vec![],points:reduce_local_points(&points)?},true));
        }
        for(path,local)in wave_paths{
            let Some(source_path)=path.source_path.as_ref().filter(|p|paths.contains_key(*p))else{continue;};
            let preceding:Vec<_>=first.iter().filter(|s|shares_source(s,source_path,&paths)).flat_map(|s|s.points.iter()).filter(|p|p.index<origin&&p.known(bars,"H",n.known,true)).collect();
            if preceding.is_empty(){continue;}
            for a in path.points.iter().filter(|a|a.known(bars,"H",now,true)&&a.flip.as_deref()==Some("翻多为空")){
                if a.index<=n.attack||!(n.one_p<a.price&&a.price<n.two_t)||bars[origin..=a.index].iter().map(|b|b.high).max()!=Some(a.price){continue;}
                let first_break=(a.index+1..=now).find(|&i|bars[i].high>a.price).unwrap_or(now+1);
                let Some(b)=path.points.iter().filter(|b|a.index<b.index&&b.index<first_break&&b.known(bars,"L",now,true)).min_by_key(|b|(b.price,b.index))else{continue;};
                let known=n.known.max(a.known).max(b.known);
                let Some(c)=path.points.iter().find(|c|c.index>b.index&&c.price>a.price&&c.known(bars,"H",now,true)&&c.flip.as_deref()==Some("翻多为空")
                    &&c.known>=known&&bars[b.index..=c.known].iter().map(|b|b.high).max()==Some(c.price))else{continue;};
                if !(origin_price<b.price&&b.price<a.price&&a.price<=c.price)||bars[n.attack+1..=c.known].iter().any(|b|b.low<origin_price&&b.close<origin_price){continue;}
                let Some(overlap)=(c.index+1..=now).find(|&i|origin_price<=bars[i].low&&bars[i].low<a.price)else{continue;};
                if (c.index+1..=now).find(|&i|bars[i].high>c.price).is_some_and(|i|i<=overlap){continue;}
                let combined_known=preceding.iter().map(|p|p.known).fold(known.max(c.known).max(overlap),usize::max);
                let mut highs:BTreeSet<_>=BTreeSet::new();
                for source in first.iter().filter(|s|shares_source(s,source_path,&paths)){for p in source.points.iter().filter(|p|p.index>c.index&&p.known(bars,"H",now,true)){
                    highs.insert(SecondaryHigh{index:p.index,price:p.price,known_index:p.known});}}
                let mut highs:Vec<_>=highs.into_iter().collect();highs.sort_by_key(|p|(p.index,p.known_index));
                let context=CombinedAContext{source_path:source_path.clone(),origin_index:origin,origin_price,a_index:a.index,a_price:a.price,b_index:b.index,b_price:b.price,c_index:c.index,c_price:c.price,
                    known_index:combined_known,n_index:n.attack,child_pullback_durations:child_durations(bars,&path.points,origin,c.index,c.known,&positive),rebound_highs:highs,trend_level:2};
                let key=(origin,a.index);
                if groups.get(&key).is_none_or(|(prior,was_local)|(*was_local&&!local)||(*was_local==local&&b.price<prior.b_price)){groups.insert(key,(context,local));}
            }
        }
    }
    let mut combined=BTreeMap::<(usize,usize),CombinedAContext>::new();
    for(context,_)in groups.into_values(){if combined.get(&context.identity()).is_none_or(|old|context.a_index>old.a_index){combined.insert(context.identity(),context);}}
    Ok(combined.into_values().collect())
}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct CombinedAState {last:Option<usize>,previous:Vec<CombinedAContext>,pub events:Vec<Value>,pub proofs:BTreeMap<usize,Value>,seen:BTreeSet<(usize,usize)>,retired:BTreeSet<(usize,usize)>}
impl CombinedAState {
    pub fn advance(&mut self,bars:&[RuleCandle],now:usize,current:&[CombinedAContext],entry_allowed:bool)->Result<Option<Value>,String>{
        if now>=bars.len()||self.last.is_some_and(|i|now!=i+1){return Err("combined A requires consecutive visible bars".into());}
        let mut result=None::<Value>;
        if now>0 {
            for c in current.iter().filter(|c|c.known_index<=now){if self.seen.insert(c.identity()){
                self.events.push(json!({"bar_index":now,"event":"combined_a_candidate","origin_index":c.origin_index,"a_high_index":c.a_index,"b_low_index":c.b_index,"c_high_index":c.c_index,"candidate_known_index":c.known_index,"source_path":c.source_path}));}}
            for c in &self.previous {
                if self.retired.contains(&c.identity())||c.known_index>=now||c.c_index>=now{continue;}
                let floor_numerator=c.c_price+Decimal::TWO*c.origin_price;let floor=floor_numerator/Decimal::from(3);
                if let Some(invalid)=(c.c_index+1..=now).find(|&i|bars[i].low<c.origin_price||bars[i].close*Decimal::from(3)<floor_numerator){
                    self.retired.insert(c.identity());self.events.push(json!({"bar_index":now,"event":"combined_a_invalidated","origin_index":c.origin_index,"c_high_index":c.c_index,
                        "invalidated_index":invalid,"invalidation_reason":if bars[invalid].low<c.origin_price{"origin_low_broken"}else{"two_thirds_close_broken"}}));continue;
                }
                if !entry_allowed{continue;}
                let duration=now-c.c_index;let internal=c.b_index-c.a_index;let exceeded_b=duration>internal;let exceeded_child=c.child_pullback_durations.iter().any(|d|duration>*d);
                if !exceeded_b&&!exceeded_child{continue;}
                let(bar,prev)=(&bars[now],&bars[now-1]);let body=bar.close-bar.open;let span=bar.high-bar.low;
                if prev.volume<=Decimal::ZERO||bar.volume<=prev.volume||bar.open<=Decimal::ZERO||span<=Decimal::ZERO
                    ||body*Decimal::from(100)<bar.open*Decimal::from(3)||body*Decimal::from(5)<span*Decimal::from(3){continue;}
                let high=c.rebound_highs.iter().filter(|p|p.index<now&&p.known_index<now).max_by_key(|p|(p.index,p.known_index));
                let gap=bar.open>prev.high&&bar.low>prev.high&&bar.close>prev.high;
                let breakout=high.is_some_and(|p|prev.close<=p.price&&p.price<bar.close);if !gap&&!breakout{continue;}
                let(reference_index,reference_price,reference_known)=if gap{(now-1,prev.high,now-1)}else if let Some(high)=high{(high.index,high.price,high.known_index)}else{continue;};
                let bottom=(c.c_index+1..=now).min_by_key(|&i|(bars[i].low,i)).ok_or("combined pullback unavailable")?;
                let min_close=bars[c.c_index+1..=now].iter().map(|b|b.close).min().ok_or("combined close unavailable")?;
                self.retired.insert(c.identity());
                let proof=json!({"buy_point_type":"combined_a_pullback_breakout","trend_level":c.trend_level,"source_path":c.source_path,
                    "origin_index":c.origin_index,"origin_price":c.origin_price,"a_high_index":c.a_index,"a_high_price":c.a_price,
                    "b_low_index":c.b_index,"b_low_price":c.b_price,"c_high_index":c.c_index,"c_high_price":c.c_price,"candidate_known_index":c.known_index,"n_index":c.n_index,
                    "pullback_low_index":bottom,"pullback_low_price":bars[bottom].low,"pullback_duration":duration,"internal_b_duration":internal,"child_pullback_durations":c.child_pullback_durations,
                    "duration_exceeded_internal_b":exceeded_b,"duration_exceeded_child_n":exceeded_child,"two_thirds_floor":floor,"minimum_pullback_close":min_close,
                    "breakout_type":if gap{"gap"}else{"body_breakout"},"rebound_high_index":reference_index,"rebound_high_price":reference_price,"rebound_high_known_index":reference_known,
                    "breakout_close":bar.close,"breakout_body_fraction":body/bar.open,"breakout_body_range_fraction":body/span,"breakout_volume":bar.volume,"previous_volume":prev.volume,
                    "stop":bars[bottom].low,"target":c.c_price,"attack":now,"squeeze_confirmation":"combined_a_pullback_breakout",
                    "combined_a_origin_date":date_at(bars,c.origin_index),"combined_a_origin_price":c.origin_price,"combined_a_top_date":date_at(bars,c.c_index),"combined_a_top_price":c.c_price,
                    "combined_a_known_date":date_at(bars,c.known_index),"combined_a_internal_pullback_sessions":internal,"combined_a_child_pullback_sessions":c.child_pullback_durations.last(),
                    "combined_a_pullback_sessions":duration,"combined_a_pullback_date":date_at(bars,bottom),"combined_a_pullback_low":bars[bottom].low,
                    "combined_a_two_thirds_price":floor,"combined_a_minimum_close":min_close,"combined_a_breakout_date":date_at(bars,reference_index),"combined_a_breakout_price":reference_price,
                    "combined_a_breakout_known_date":date_at(bars,reference_known),"combined_a_breakout_type":if gap{"gap"}else{"body_breakout"},"confirmation_close":bar.close,
                    "breakout_volume_multiple":bar.volume/prev.volume,"breakout_body_pct":body/bar.open,"breakout_body_ratio":body/span,"counter_ratio":(c.c_price-min_close)/(c.c_price-c.origin_price)});
                let mut event=proof.clone();event["bar_index"]=json!(now);event["event"]=json!("combined_a_pullback_breakout");self.events.push(event);
                if result.as_ref().and_then(|v|index(v,"c_high_index")).is_none_or(|old|c.c_index>old){result=Some(proof);}
            }
        }
        if let Some(proof)=&result{self.proofs.insert(now,proof.clone());}self.previous=current.to_vec();self.last=Some(now);Ok(result)
    }
}
