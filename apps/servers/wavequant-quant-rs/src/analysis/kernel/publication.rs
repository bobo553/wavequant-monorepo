//! trend_publication.py 的方向许可生产；结构来源与许可发布链严格分开。
//! 不更改原结构节点、闭合时钟或递归来源，许可按自己的首次可知日记录。
use super::{Direction,DirectionCertificate,NDirectionCertificate,KernelBar,KernelFrame,KernelPoint,PointKind,PointState};
use super::super::trading::RuleCandle;
use rust_decimal::prelude::ToPrimitive;
use crate::indexed_json::{Value,json};
use std::collections::BTreeMap;

fn number(value:&Value,key:&str)->Option<usize>{value.get(key)?.as_u64().and_then(|n|usize::try_from(n).ok())}
fn amount(value:&Value,key:&str)->Option<f64>{value.get(key)?.as_f64().or_else(||value.get(key)?.as_str()?.parse().ok())}
fn remove(point:&mut KernelPoint,key:&str){if let Some(object)=point.evidence.as_object_mut(){object.remove(key);}}
fn reference(point:&KernelPoint)->Value{json!({"index":point.index,"ordinal":point.ordinal,"time":point.time,"kind":point.kind,
    "value":point.value,"available_at":point.known_index,"label":if point.kind==PointKind::H{"H"}else{"L"}})}
fn matches(point:&KernelPoint,value:&Value)->bool{number(value,"index")==Some(point.index)&&number(value,"ordinal").unwrap_or(0)==point.ordinal
    &&value.get("kind").and_then(Value::as_str)==Some(if point.kind==PointKind::H{"H"}else{"L"})&&amount(value,"value")==Some(point.value)}
fn same(left:&KernelPoint,right:&KernelPoint)->bool{left.order()==right.order()&&left.kind==right.kind&&left.value==right.value}
fn origin(proof:&Value)->&Value{proof.get("wave_origin").unwrap_or(&proof["origin"])}
fn known(proof:&Value)->Option<usize>{number(proof,"available_at").or_else(||number(proof,"known_index"))}
fn rule(proof:&Value)->&str{proof["confirmation_rule"].as_str().unwrap_or("")}
fn direction(proof:&Value)->Option<Direction>{match proof["direction"].as_str(){Some("up")=>Some(Direction::Up),Some("down")=>Some(Direction::Down),_=>None}}
fn ordinary(proof:DirectionCertificate)->Value{
    let up=proof.direction==Direction::Up;let counter=proof.counter.as_ref().map(reference);let flip=reference(&proof.flip);
    let mut trigger=if proof.rule=="strict_same_level_market_key_break"{reference(&proof.flip)}else{let mut trigger=json!({});for field in ["index","ordinal","time","kind","value","available_at","close","market_high","market_low"]{if let Some(value)=proof.confirmed_by.get(field){trigger[field]=value.clone();}}trigger};
    if trigger.get("available_at").is_none(){trigger["available_at"]=json!(proof.known_index);}
    if trigger.get("ordinal").is_none(){trigger["ordinal"]=json!(0);}
    let mut result=json!({"available_at":proof.known_index,"confirmation_rule":proof.rule,"direction":proof.direction,
        "origin":reference(&proof.origin),"broken_key":reference(&proof.broken_key),"confirmed_by":trigger});
    result[if up{"flip_high"}else{"flip_low"}]=flip;result[if up{"alternation_low"}else{"alternation_high"}]=counter.map_or(Value::Null,|v|v);
    if let Some(origin)=&proof.retracement_origin{result["retracement_origin"]=reference(origin);}
    if let Some(counter)=&proof.counter{let base=proof.retracement_origin.as_ref().unwrap_or(&proof.origin);let impulse=proof.direction.sign()*(proof.flip.value-base.value);
        if impulse>0.0{result["retracement_ratio"]=json!(proof.direction.sign()*(proof.flip.value-counter.value)/impulse);}
        result["source_break_available_at"]=json!(proof.flip.known_index);result["alternation_available_at"]=json!(counter.known_index);}
    result
}
fn n_proof(proof:&NDirectionCertificate,bars:&[RuleCandle])->Result<Value,String>{
    let up=proof.direction==Direction::Up;let completion=&proof.completion;
    let bar=bars.get(completion.bar_index).ok_or("N permission completion candle unavailable")?;
    let box_anchor=if up{completion.virtual_high}else{completion.virtual_low};
    Ok(json!({"available_at":proof.known_index,"confirmation_rule":proof.confirmation_rule,"direction":proof.direction,
        "origin":reference(&proof.origin),"broken_key":reference(&proof.neckline),"confirmed_by":proof.confirmed_by,
        "n_neckline":reference(&proof.neckline),"n_pullback":reference(&proof.pullback),
        "n_completion":{"index":completion.bar_index,"ordinal":0,"time":bar.timestamp,"kind":if up{"H"}else{"L"},"value":box_anchor.to_f64(),
            "available_at":completion.bar_index,"close":bar.close.to_f64(),"market_high":bar.high.to_f64(),"market_low":bar.low.to_f64(),
            "virtual_high":completion.virtual_high.to_f64(),"virtual_low":completion.virtual_low.to_f64(),"real_break":completion.real_break,"virtual_break":completion.virtual_break},
        "one_p_target":proof.target.to_f64(),"box_anchor":box_anchor.to_f64(),"defense":proof.defense.to_f64(),"target_basis":proof.confirmed_by["target_basis"]}))
}
fn wave_holds(proof:&Value,bars:&[KernelBar],endpoint:usize)->bool{
    let reference=origin(proof);let(Some(start),Some(value),Some(direction))=(number(reference,"index"),amount(reference,"value"),direction(proof))else{return false;};
    start<endpoint&&endpoint<bars.len()&&bars[start..=endpoint].iter().all(|bar|if direction==Direction::Up{bar.low>=value}else{bar.high<=value})
}
fn held_floor(point:&KernelPoint,bars:&[KernelBar],through:usize)->bool{
    point.index<=through&&through<bars.len()&&bars[point.index].low==point.value&&bars[point.index..=through].iter().all(|bar|bar.low>=point.value)
}
fn known_leg(proof:&Value,point:&KernelPoint,d:Direction,base:&KernelPoint)->bool{
    direction(proof)==Some(d)&&number(&proof["confirmed_by"],"index").is_some_and(|index|index<=point.index)
        &&matches(base,origin(proof))&&known(proof).is_some_and(|index|index<=point.known_index)
}
fn source_path(point:&KernelPoint)->usize{point.evidence.get("source").and_then(|value|number(value,"path")).unwrap_or(point.path)}
fn mask_n(source:&[KernelPoint],qualified:Option<&[KernelPoint]>)->Vec<KernelPoint>{
    source.iter().map(|point|{let mut result=point.clone();if let Some(qualified)=qualified{
        if let Some(known)=qualified.iter().filter(|published|published.is_closed()&&same(point,published)&&published.path==point.path).map(|p|p.known_index).min(){result.known_index=result.known_index.max(known);}
        else{result.state=PointState::Seed;}}
        result
    }).collect()
}
struct Publication<'a>{source:&'a[KernelPoint],n_source:Vec<KernelPoint>,bars:&'a[RuleCandle],market:&'a[KernelBar],n_enabled:bool,
    active:Option<Value>,extreme:Option<KernelPoint>,memo:BTreeMap<(usize,usize,u8,String),Option<Value>>}
impl Publication<'_>{
    fn position(&self,point:&KernelPoint)->Result<usize,String>{self.source.iter().position(|source|same(source,point)&&source.is_closed())
        .ok_or_else(||"permission candidate must identify its actual confirmed lower source".into())}
    fn qualify(&mut self,position:usize,key:Option<&KernelPoint>,cutoff:usize,d:Direction)->Result<Option<Value>,String>{
        let memo=(position,cutoff,if d==Direction::Up{0}else{1},crate::indexed_json::to_string(&key.map(reference)).map_err(|e|e.to_string())?);
        if let Some(saved)=self.memo.get(&memo){return Ok(saved.clone());}
        let mut proof=super::qualify_direction(self.source,position,key,self.market,cutoff,d)?.map(ordinary);
        if self.n_enabled{if let Some(n)=super::qualify_n_direction(&self.n_source,position,self.bars,cutoff,d)?{
            if proof.as_ref().is_none_or(|ordinary|known(ordinary).is_none_or(|index|n.known_index<index||(n.known_index==index&&rule(ordinary)=="source_key_break_alternation_then_market_turn"))){proof=Some(n_proof(&n,self.bars)?);}
        }}self.memo.insert(memo,proof.clone());Ok(proof)
    }
    fn continuation(&self,point:&KernelPoint,d:Direction)->Option<Value>{
        let active=self.active.as_ref()?;if direction(active)!=Some(d)||point.known_index>=self.market.len()||!wave_holds(active,self.market,point.index){return None;}
        if !self.n_enabled&&rule(active)=="source_n_strict_one_p_target"{return None;}
        let position=self.position(point).ok()?;if self.source[position].known_index>point.known_index{return None;}
        let base=self.source.iter().find(|source|matches(source,origin(active)))?;
        known_leg(active,point,d,base).then(||active.clone())
    }
    fn annotate(&mut self,point:&mut KernelPoint,previous:&KernelPoint,key:Option<&KernelPoint>)->Result<(),String>{
        let d=if previous.kind==PointKind::L&&point.kind==PointKind::H{Direction::Up}else{Direction::Down};
        point.evidence["incoming_trend_state"]=json!("unconfirmed");
        let mut proof=if d==Direction::Up{point.evidence.get("incoming_trend_confirmation").cloned()}else{None};
        let mut base=previous.clone();
        if d==Direction::Down{remove(point,"incoming_trend_confirmation");
            if previous.kind==PointKind::H&&point.kind==PointKind::L&&point.known_index<self.market.len(){
                let position=self.position(previous)?;proof=self.qualify(position,key,point.known_index,d)?;
            }
        }
        if proof.as_ref().is_none_or(|proof|!known_leg(proof,point,d,&base)){
            if let Some(continued)=self.continuation(point,d){if let Some(source)=self.source.iter().find(|p|matches(p,origin(&continued))){base=source.clone();proof=Some(continued);}}
        }
        if let Some(mut proof)=proof.filter(|proof|known_leg(proof,point,d,&base)){
            if d==Direction::Down&&self.active.as_ref().and_then(direction)==Some(Direction::Up){if let Some(extreme)=&self.extreme{
                let source=self.position(extreme).ok().map(|position|&self.source[position]);
                if let Some(source)=source.filter(|source|!same(extreme,&base)&&known(&proof).is_some_and(|index|source.known_index<=index)){
                    let mut extended=proof.clone();extended["wave_origin"]=reference(source);
                    if number(&proof["confirmed_by"],"index").is_some_and(|index|wave_holds(&extended,self.market,index)){proof=extended;}
                }
            }}
            point.evidence["incoming_trend_confirmation"]=proof.clone();point.evidence["incoming_trend_state"]=json!("confirmed");
            let same_wave=self.active.as_ref().is_some_and(|active|direction(active)==Some(d)&&wave_holds(active,self.market,point.index)
                &&!(self.n_enabled&&rule(&proof)=="source_n_strict_one_p_target"&&!matches(&base,origin(active))));
            if !same_wave{self.active=Some(proof);self.extreme=Some(point.clone());}
            else if self.extreme.as_ref().is_some_and(|extreme|d.sign()*(point.value-extreme.value)>0.0){self.extreme=Some(point.clone());}
        }else{remove(point,"incoming_trend_confirmation");}
        if let Some(active)=&self.active{point.evidence["active_trend_confirmation"]=active.clone();}Ok(())
    }
}
fn normalize_candidate(mut point:KernelPoint)->KernelPoint{
    if !point.evidence.is_object(){point.evidence=json!({});}
    // 高低点直接升级在结构账本里保存proof；许可生产使用同一条真实证据。
    if point.evidence["confirmation_rule"]=="strict_known_trend_key_break"{
        if let Some(proof)=point.evidence.get("proof").cloned(){point.evidence["upgrade_evidence"]=proof.clone();point.evidence["broken_key"]=proof["broken_key"].clone();}
    }
    point
}
fn upgrade(point:&KernelPoint,direction:&str,role:&str)->bool{point.evidence["upgrade_evidence"]["direction"]==direction&&point.evidence["upgrade_evidence"]["role"]==role}
fn publish_path(candidates:&[KernelPoint],source:&[KernelPoint],n_source:Vec<KernelPoint>,bars:&[RuleCandle],market:&[KernelBar],asof:usize,n_enabled:bool)->Result<Vec<KernelPoint>,String>{
    let mut state=Publication{source,n_source,bars,market,n_enabled,active:None,extreme:None,memo:BTreeMap::new()};
    let mut result:Vec<KernelPoint>=vec![];let mut pressure:Option<KernelPoint>=None;let mut pending:Option<KernelPoint>=None;
    let mut pending_down:Option<KernelPoint>=None;let mut last_low:Option<KernelPoint>=None;
    for(offset,candidate)in candidates.iter().enumerate(){
        let mut point=candidate.clone();
        if point.kind==PointKind::H{
            if upgrade(&point,"down","origin"){state.position(&point)?;pending_down=Some(point.clone());}
            pressure=Some(candidate.clone());
            if n_enabled{if let Some(raw)=point.evidence.get("n_target_confirmation"){
                let proof:NDirectionCertificate=crate::indexed_json::from_value(raw.clone()).map_err(|e|format!("invalid owned N proof: {e}"))?;
                point.evidence["trend_confirmation"]=n_proof(&proof,bars)?;point.evidence["trend_confirmation_route"]=json!("source_n_strict_one_p_target");
            }}
            if result.last().is_some_and(|p|p.kind==PointKind::H){
                if let Some(continued)=state.continuation(&point,Direction::Up){if result.len()>1&&point.value>result[result.len()-1].value{
                    point.known_index=point.known_index.max(result[result.len()-1].known_index);point.evidence["incoming_trend_confirmation"]=continued;
                    let previous=result[result.len()-2].clone();state.annotate(&mut point,&previous,last_low.as_ref())?;
                    let last=result.len()-1;result[last]=point;pending=None;
                }}continue;
            }
            if let Some(previous)=result.last().cloned(){
                let mut proof=previous.evidence.get("trend_confirmation").cloned();
                if proof.as_ref().and_then(|p|number(&p["confirmed_by"],"index")).is_some_and(|index|point.index<index){
                    let Some(continued)=state.continuation(&point,Direction::Up)else{continue;};proof=Some(continued);
                }
                point.known_index=point.known_index.max(previous.known_index);
                if let Some(proof)=proof{point.evidence["incoming_trend_confirmation"]=proof;}
                state.annotate(&mut point,&previous,last_low.as_ref())?;
            }
            result.push(point);pending=None;continue;
        }
        let position=state.position(&point)?;
        if upgrade(&point,"down","endpoint"){
            if !result.is_empty(){
                let mut previous=if result[result.len()-1].kind==PointKind::H{result.last().cloned()}else{result.len().checked_sub(2).map(|i|result[i].clone())};
                let mut key=last_low.clone();let mut paired_key:Option<KernelPoint>=None;
                let reference=&point.evidence["upgrade_evidence"]["origin"];let upgraded=&point.evidence["broken_key"];
                let paired=pending_down.as_ref().filter(|p|matches(p,reference)).cloned()
                    .or_else(||candidates.iter().find(|p|p.kind==PointKind::H&&matches(p,reference)).cloned());
                if let Some(paired)=paired{let known_key=candidates.iter().find(|p|p.kind==PointKind::L&&matches(p,upgraded)&&known(upgraded)==Some(p.known_index));
                    let source_key=source.iter().find(|p|matches(p,upgraded));
                    if let(Some(known_key),Some(source_key),Some(key_known))=(known_key,source_key,known(upgraded)){
                        if source_key.known_index<=key_known&&paired.known_index<=point.known_index&&key_known<=point.known_index{
                            previous=Some(paired);let mut own_key=known_key.clone();own_key.known_index=key_known;key=Some(own_key);paired_key=Some(known_key.clone());
                        }
                    }
                }
                if let Some(previous)=previous.filter(|p|p.kind==PointKind::H){
                    state.annotate(&mut point,&previous,key.as_ref())?;
                    if point.evidence["incoming_trend_state"]=="confirmed"{
                        if let Some(outgoing)=state.qualify(position,pressure.as_ref(),asof,Direction::Up)?{
                            point.evidence["trend_confirmation_route"]=outgoing["confirmation_rule"].clone();point.evidence["trend_confirmation"]=outgoing;
                        }
                        if result.last().is_some_and(|last|last.kind==PointKind::H&&point.evidence.get("active_trend_confirmation").is_some_and(|proof|matches(&previous,origin(proof))&&!matches(last,origin(proof)))){
                            let Some(mut reference_low)=paired_key.filter(|key|result.last().is_some_and(|last|last.order()<key.order())&&key.order()<previous.order())else{continue;};
                            reference_low.known_index=point.known_index;reference_low.evidence["incoming_trend_state"]=json!("unconfirmed");remove(&mut reference_low,"incoming_trend_confirmation");
                            let mut reference_high=previous;reference_high.known_index=point.known_index;reference_high.evidence["incoming_trend_state"]=json!("unconfirmed");remove(&mut reference_high,"incoming_trend_confirmation");
                            result.extend([reference_low,reference_high]);
                        }
                        if result.last().is_some_and(|last|last.kind==PointKind::L){if result.last().is_some_and(|last|point.value<last.value){let last=result.len()-1;result[last]=point.clone();}}
                        else{result.push(point.clone());}
                        last_low=Some(point);pending=None;pending_down=None;
                    }
                }
            }continue;
        }
        if result.last().is_some_and(|p|p.kind==PointKind::L){continue;}
        if source[position].known_index<=asof&&pending.as_ref().is_none_or(|prior|!held_floor(prior,market,point.index)||point.value<prior.value){pending=Some(point.clone());}
        let Some(mut confirmation)=state.qualify(position,pressure.as_ref(),asof,Direction::Up)?else{
            if let Some(previous)=result.last().filter(|p|p.kind==PointKind::H){state.annotate(&mut point,previous,last_low.as_ref())?;}continue;
        };
        let mut earliest=point.known_index.max(known(&confirmation).ok_or("permission proof clock missing")?);
        let mut future_key=pressure.clone();
        for later in candidates.iter().skip(offset+1){
            if later.kind==PointKind::H{
                if number(&confirmation["confirmed_by"],"index").is_some_and(|index|later.index>=index){break;}
                future_key=Some(later.clone());continue;
            }
            if later.known_index>earliest{continue;}
            let later_position=state.position(later)?;
            let Some(local)=state.qualify(later_position,future_key.as_ref(),asof,Direction::Up)?else{continue;};
            let local_known=later.known_index.max(known(&local).ok_or("local permission proof clock missing")?);
            if local_known<earliest&&pending.as_ref().is_some_and(|base|number(&local["confirmed_by"],"index").is_some_and(|index|held_floor(base,market,index))){
                point=later.clone();confirmation=local;earliest=local_known;
            }
        }
        let candidate_known=point.known_index;
        if let Some(base)=pending.as_ref().filter(|base|number(&confirmation["confirmed_by"],"index").is_some_and(|index|held_floor(base,market,index))){
            point=base.clone();confirmation["wave_origin"]=reference(&source[state.position(&point)?]);
        }
        point.known_index=point.known_index.max(candidate_known).max(known(&confirmation).ok_or("permission clock missing")?).max(result.last().map_or(0,|p|p.known_index));
        point.evidence["structural_confirmation_rule"]=point.evidence["confirmation_rule"].clone();
        point.evidence["confirmation_rule"]=confirmation["confirmation_rule"].clone();
        point.evidence["trend_confirmation_route"]=json!(if rule(&confirmation)=="source_n_strict_one_p_target"{"source_n_strict_one_p_target"}
            else if !confirmation["alternation_low"].is_null(){"lower_level_break_alternation_turn"}else{"same_level_key_break"});
        point.evidence["trend_confirmation"]=confirmation;
        if let Some(previous)=result.last(){state.annotate(&mut point,previous,last_low.as_ref())?;}
        last_low=Some(point.clone());result.push(point);pending=None;
    }
    Ok(result.into_iter().filter(|point|point.index<=point.known_index&&point.known_index<=asof).collect())
}
/// 已有 raw 来源始终走普通规则；仅 N 分支用下级实际发布许可掩码。
pub fn attach_publication(frame:&mut KernelFrame,source:&[KernelPoint],qualified_source:Option<&[KernelPoint]>,bars:&[RuleCandle],asof:usize,n_enabled:bool)->Result<(),String>{
    frame.publication.clear();if frame.level==0{return Ok(());}
    if asof>=bars.len(){return Err("publication requires the visible market prefix".into());}
    let market=bars[..=asof].iter().enumerate().map(|(index,bar)|Ok(KernelBar{index,time:bar.timestamp.clone(),open:bar.open.to_f64().ok_or("market open overflow")?,
        high:bar.high.to_f64().ok_or("market high overflow")?,low:bar.low.to_f64().ok_or("market low overflow")?,close:bar.close.to_f64().ok_or("market close overflow")?})).collect::<Result<Vec<_>,String>>()?;
    let mut groups:BTreeMap<usize,Vec<KernelPoint>>=BTreeMap::new();for point in source{if point.is_closed()&&point.known_index<=asof{groups.entry(point.path).or_default().push(point.clone());}}
    let mut candidates:BTreeMap<usize,BTreeMap<(usize,usize),KernelPoint>>=BTreeMap::new();
    for point in frame.points.iter().chain(frame.candidate.iter().filter(|p|p.evidence.get("upgrade_evidence").is_some())){
        if point.evidence["wave_identity_state"]=="direction_only"||point.known_index>asof{continue;}
        let mut point=normalize_candidate(point.clone());point.state=PointState::Reversal;
        let by_order=candidates.entry(source_path(&point)).or_default();
        if by_order.get(&point.order()).is_none_or(|prior|point.known_index<prior.known_index){by_order.insert(point.order(),point);}
    }
    for(path,points)in &groups{
        let masked=mask_n(points,qualified_source);
        if n_enabled{for position in 0..points.len(){
            let direction=if points[position].kind==PointKind::L{Direction::Up}else{Direction::Down};
            let Some(proof)=super::qualify_n_direction(&masked,position,bars,asof,direction)?else{continue;};
            for point in super::n_confirmation_points(&proof,points,bars,asof,frame.level)?{
                let by_order=candidates.entry(*path).or_default();
                if let Some(old)=by_order.get_mut(&point.order()).filter(|old|old.known_index<=point.known_index){
                    if point.evidence.get("n_target_confirmation").is_some(){old.evidence["n_target_confirmation"]=json!(proof);}
                }else{by_order.insert(point.order(),point);}
            }
            frame.events.push(super::KernelEvent{title:"source_n_strict_one_p_target".into(),known_index:proof.known_index,point:proof.origin.clone(),key:Some(proof.neckline.clone()),origin:Some(proof.origin.clone()),evidence:n_proof(&proof,bars)?});
        }}
        if let Some(rows)=candidates.remove(path){let rows=rows.into_values().collect::<Vec<_>>();
            frame.publication.extend(publish_path(&rows,points,masked,bars,&market,asof,n_enabled)?);
        }
    }
    if !candidates.is_empty(){return Err("publication candidates must belong to an actual lower source path".into());}
    // 许可字段不回写结构点，避免许可知日被误当闭合知日递归到上一层。
    Ok(())
}
