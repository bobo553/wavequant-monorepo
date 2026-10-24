//! 交易资格视图的只读投影；资格段和 ABC 观察绝不写回正式趋势几何。
use super::KernelFrame;
use crate::analysis::trading::{RuleCandle,abc::{self,AbcAnchor},qualification::{index,price},secondary::session_date};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use sha2::{Digest,Sha256};
use std::collections::BTreeSet;
fn text<'a>(v:&'a Value,key:&str)->Option<&'a str>{v.get(key)?.as_str()}
fn order(v:&Value)->(usize,usize){(index(v,"index").unwrap_or(usize::MAX),index(v,"ordinal").unwrap_or(0))}
fn points(v:&Value)->&[Value]{v.get("points").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])}
fn normalize(value:&Value,bars:&[RuleCandle])->Value{match (value).kind() {
    Some(crate::indexed_json::Kind::Array(rows))=>Value::Array(rows.iter().map(|v|normalize(v,bars)).collect()),
    Some(crate::indexed_json::Kind::Object(fields))=>{let mut out:crate::indexed_json::Map<String,Value>=fields.iter().map(|(k,v)|(k.clone(),normalize(v,bars))).collect();
        if !out.contains_key("available_at"){if let Some(i)=index(value,"known_index"){out.insert("available_at".into(),json!(i));}}
        for key in ["available_at","source_available_at"]{if let Some(i)=out.get(key).and_then(Value::as_u64).and_then(|i|usize::try_from(i).ok()).filter(|i|*i<bars.len()){out.insert(key.into(),json!(session_date(&bars[i])));}}
        if let Some(i)=index(value,"index").filter(|i|*i<bars.len()){out.insert("time".into(),json!(session_date(&bars[i])));}Value::Object(out)},_=>value.clone()}}
fn reference(index:usize,ordinal:usize,value:Decimal,kind:&str,known:usize,bars:&[RuleCandle])->Option<Value>{
    if index>known||known>=bars.len()||value<=Decimal::ZERO{return None;}
    Some(json!({"index":index,"ordinal":ordinal,"time":session_date(&bars[index]),"value":value,"kind":kind,"label":kind,
        "available_at":session_date(&bars[known]),"state":"confirmed","display_only":false,"observations":[]}))
}
fn reference_only(point:&Value)->Value{
    let mut out=crate::indexed_json::Map::new();for key in ["index","ordinal","time","kind","value","available_at","label"]{
        if let Some(value)=point.get(key){out.insert(key.into(),value.clone());}
    }Value::Object(out)
}
fn same_origin(proof:&Value,point:&Value)->bool{
    let Some(origin)=proof.get("wave_origin").or_else(||proof.get("origin"))else{return false;};
    order(origin)==order(point)&&text(origin,"kind")==text(point,"kind")&&price(origin,"value")==price(point,"value")
}
fn fraction(value:Decimal)->String{
    let value=value.normalize();let mut numerator=value.mantissa();let mut denominator=10_i128.pow(value.scale());
    let(mut a,mut b)=(numerator.unsigned_abs(),denominator as u128);while b!=0{let remainder=a%b;a=b;b=remainder;}
    let gcd=a.max(1)as i128;numerator/=gcd;denominator/=gcd;
    if denominator==1{numerator.to_string()}else{format!("{numerator}/{denominator}")}
}
fn wave_reference(value:&Value)->Option<Value>{
    let(i,ordinal,value_price,known,time,kind)=(index(value,"index")?,index(value,"ordinal").unwrap_or(0),price(value,"value")?,value.get("available_at")?,text(value,"time")?,text(value,"kind")?);
    if time.is_empty()||!matches!(kind,"H"|"L"|"K")||!(known.as_u64().is_some()||known.as_str().is_some_and(|s|!s.is_empty())){return None;}
    Some(json!([i,ordinal,time,kind,fraction(value_price),known]))
}
pub fn trend_wave_identity(proof:&Value,level:u8,source_path:&str)->Option<String>{
    let rule=text(proof,"confirmation_rule")?;let direction=text(proof,"direction")?;
    if !(1..=3).contains(&level)||source_path.is_empty()||!matches!(direction,"up"|"down")
        ||!matches!(rule,"strict_same_level_market_key_break"|"source_key_break_alternation_then_market_turn"|"source_n_strict_one_p_target"){return None;}
    let mut fields=vec!["origin","broken_key","confirmed_by"];
    if rule=="source_key_break_alternation_then_market_turn"{fields.extend(if direction=="up"{["flip_high","alternation_low"]}else{["flip_low","alternation_high"]});}
    if rule=="source_n_strict_one_p_target"{fields.extend(["n_neckline","n_pullback","n_completion"]);}
    let mut references=fields.iter().map(|field|proof.get(*field).and_then(wave_reference)).collect::<Option<Vec<_>>>()?;
    if rule=="source_key_break_alternation_then_market_turn"{references.push(wave_reference(proof.get("retracement_origin").or_else(||proof.get("origin"))?)?);}
    let origin=wave_reference(proof.get("wave_origin").or_else(||proof.get("origin"))?)?;let known=proof.get("available_at")?;
    if !(known.as_u64().is_some()||known.as_str().is_some_and(|s|!s.is_empty())){return None;}
    let mut payload=vec![json!(level),json!(source_path),json!(direction),json!(rule),known.clone(),json!(references),origin];
    if rule=="source_n_strict_one_p_target"{payload.push(json!(fraction(price(proof,"box_anchor")?)));payload.push(json!(fraction(price(proof,"one_p_target")?)));}
    let encoded=crate::indexed_json::to_string(&payload).ok()?;let mut ascii=String::with_capacity(encoded.len());
    for character in encoded.chars(){if character.is_ascii(){ascii.push(character);}else{
        let mut units=[0_u16;2];for &unit in character.encode_utf16(&mut units).iter(){ascii.push_str(&format!("\\u{unit:04x}"));}
    }}
    Some(format!("trend-wave-v1:{:x}",Sha256::digest(ascii.as_bytes())))
}
fn confirmed_trend_legs(points:&[Value],level:u8,source_path:&str)->Vec<Value>{
    let mut legs:Vec<Value>=vec![];
    for point in points{
        let Some(proof)=point.get("active_trend_confirmation").filter(|p|p.is_object())else{continue;};
        if text(point,"incoming_trend_state")!=Some("confirmed"){continue;}
        let Some(direction)=text(proof,"direction").filter(|d|matches!(*d,"up"|"down"))else{continue;};
        if text(point,"kind")!=Some(if direction=="up"{"H"}else{"L"}){continue;}
        let Some(origin)=points.iter().find(|p|same_origin(proof,p))else{continue;};
        let mut leg=json!({"points":[origin,point],"direction":direction,"confirmation":proof,"available_at":point["available_at"]});
        if let Some(wave)=trend_wave_identity(proof,level,source_path){leg["wave_id"]=json!(wave);}
        if let Some(previous)=legs.last_mut().filter(|p|text(p,"direction")==Some(direction)&&p.get("confirmation").is_some_and(|p|same_origin(p,origin))){
            let old=previous.get("points").and_then(Value::as_array).and_then(|p|p.last()).and_then(|p|price(p,"value"));
            if price(point,"value").zip(old).is_some_and(|(current,old)|if direction=="up"{current>old}else{current<old}){*previous=leg;}
        }else{legs.push(leg);}
    }legs
}
fn last_fall_high_reanchors(points:&[Value],bars:&[RuleCandle],level:u8)->Vec<Value>{
    let mut out=vec![];
    for(position,broken)in points.iter().enumerate().filter(|(_,p)|text(p,"kind")==Some("L")){
        let(Some(floor),Some(known))=(price(broken,"value"),text(broken,"available_at"))else{continue;};
        let known=known.max(text(broken,"time").unwrap_or(""));
        let Some(now)=(1..bars.len()).find(|&i|session_date(&bars[i]).as_str()>=known&&bars[i-1].close>=floor&&bars[i].close<floor)else{continue;};
        let date=session_date(&bars[now]);let Some(previous)=points[..position].iter().rev().find(|p|text(p,"kind")==Some("H"))else{continue;};
        let formal=points.iter().enumerate().skip(position+1).filter(|(_,p)|text(p,"kind")==Some("L")&&text(p,"available_at").is_some_and(|known|known<=date.as_str())).last();
        let(new_key,active,source_level)=if let Some((active_index,active))=formal{
            let Some(new_key)=points[..active_index].iter().rev().find(|p|text(p,"kind")==Some("H"))else{continue;};(new_key,active,level)
        }else{
            if position+2!=points.len(){continue;}let Some(new_key)=points.last().filter(|p|text(p,"kind")==Some("H"))else{continue;};
            let Some(active)=new_key.get("confirmed_by").filter(|p|text(p,"kind")==Some("L")&&price(p,"value").is_some_and(|v|v<floor))else{continue;};
            (new_key,active,level.saturating_sub(1))
        };
        if order(new_key).0==order(previous).0{continue;}let(Some(key_known),Some(low_known))=(text(new_key,"available_at"),text(active,"available_at"))else{continue;};
        let available=date.as_str().max(key_known).max(low_known);
        out.push(json!({"id":format!("level{level}-last-fall-high-reanchor-{}-{}-{available}",order(broken).0,order(active).0),
            "kind":"last_fall_high_reanchor","trend_level":level,"available_at":available,
            "confirmation_rule":"market_close_break_and_confirmed_replacement_key","active_low_source_level":source_level,
            "previous_key":reference_only(previous),"broken_low":reference_only(broken),"active_low":reference_only(active),"new_key":reference_only(new_key),
            "confirmed_by":{"index":now,"time":date,"available_at":date,"kind":"K","label":"收盘跌破 K线","value":bars[now].close,
                "open":bars[now].open,"high":bars[now].high,"low":bars[now].low,"previous_close":bars[now-1].close,"break_basis":"close_cross"}}));
    }out
}
fn permission_strokes(frame:&KernelFrame,bars:&[RuleCandle])->Vec<Value>{
    let level=frame.level;let geometry=normalize(&super::permission_geometry(frame),bars);
    geometry.get("strokes").and_then(Value::as_array).into_iter().flatten().map(|stroke|{
        let mut stroke=stroke.clone();let rows=points(&stroke).to_vec();
        stroke["key_transitions"]=json!(last_fall_high_reanchors(&rows,bars,level));
        stroke["confirmed_legs"]=json!(confirmed_trend_legs(&rows,level,text(&stroke,"source_path").unwrap_or("")));
        stroke["kind"]=json!(match level{0=>"polyline",1=>"reversal",2=>"secondary",_=>"tertiary"});
        stroke["view"]=json!("strategy_permissions");stroke
    }).collect()
}

/// Python promote_alternation_segments 的资格层副本；突破 K 不是已确认高点。
pub fn promote_alternation_segments(strokes:&[Value],bars:&[RuleCandle],events:&[Value],level:u8)->Result<Value,String>{
    let mut result=strokes.to_vec();let mut added:Vec<Value>=vec![];let mut merged=0usize;
    let mut qualified:Vec<_>=events.iter().filter(|e|text(e,"event")==Some("squeeze_alternation_breakout")
        &&index(e,"trend_level")==Some(usize::from(level))&&text(e,"breakout_basis")==Some("high_after_confirmed_alternation")).collect();
    qualified.sort_by_key(|e|(index(e,"bar_index"),index(e,"a_high_index"),index(e,"b_low_index"),text(e,"source_path").unwrap_or("")));
    for event in qualified{
        let(Some(now),Some(a),Some(b),Some(top),Some(bottom),Some(raw))=(index(event,"bar_index"),index(event,"a_high_index"),index(event,"b_low_index"),
            price(event,"a_high_price"),price(event,"b_low_price"),text(event,"source_path"))else{continue;};
        if !(a<b&&b<=now&&now<bars.len())||top<=bottom{continue;}
        let known=session_date(&bars[now]);let mut pair=vec![];
        for(kind,i,value)in [("H",a,top),("L",b,bottom)]{
            let Some(mut p)=reference(i,0,value,kind,now,bars)else{continue;};
            p["trend_level"]=json!(level);p["flip"]=json!(if kind=="L"{"多头确认"}else{"空多交替高点确认"});
            p["reversal"]=json!(if kind=="L"{"正反转"}else{"负反转"});p["confirmation_rule"]=json!("confirmed_alternation_then_high_breakout");
            p["confirmed_by"]=json!({"index":now,"time":known,"kind":"K","value":bars[now].high,"available_at":known});
            p["levels"]=json!([{"name":"空翻多高点","price":top},{"name":"空多交替低点","price":bottom}]);p["wave_direction_after"]=json!(if kind=="L"{"up"}else{"down"});pair.push(p);
        }
        let mut handled=false;
        if matches!(level,2|3){for stroke in result.iter_mut().chain(added.iter_mut()){
            let original=points(stroke).to_vec();if original.is_empty()||b<=order(&original[0]).0||a>=order(original.last().ok_or("empty permission path")?).0{continue;}
            handled=true;
            for(position,edge)in original.windows(2).enumerate(){let(left,right)=(&edge[0],&edge[1]);
                if !(order(left).0<=a&&a<b&&b<=order(right).0){continue;}
                let joined=known.as_str().max(text(left,"available_at").unwrap_or(""));
                let inserts:Vec<_>=pair.iter().filter(|p|order(p).0!=order(left).0&&order(p).0!=order(right).0).map(|p|{let mut p=p.clone();p["available_at"]=json!(joined);p}).collect();
                let sequence:Vec<_>=std::iter::once(left).chain(inserts.iter()).chain(std::iter::once(right)).collect();
                if !inserts.is_empty()&&joined<=text(right,"available_at").unwrap_or("")&&sequence.windows(2).all(|edge|{
                    text(edge[0],"kind")!=text(edge[1],"kind")&&price(edge[0],"value").zip(price(edge[1],"value")).is_some_and(|(a,b)|if text(edge[0],"kind")==Some("L"){a<b}else{a>b})}){
                    let mut combined=original[..=position].to_vec();combined.extend(inserts);combined.extend_from_slice(&original[position+1..]);stroke["points"]=json!(combined);merged+=1;
                }break;
            }break;
        }}
        if handled{continue;}
        let name=if level==3{"tertiary"}else{"secondary"};added.push(json!({"id":format!("{name}-alternation-{a}-{b}"),"kind":name,"trend_level":level,
            "source_path":raw,"points":pair,"available_at":known,"confirmation_evidence":event,"view":"strategy_permissions"}));
    }
    let count=added.len()+merged;result.extend(added);
    let wave_count=result.iter().flat_map(points).filter_map(|p|Some((index(p,"index")?,index(p,"ordinal").unwrap_or(0),text(p,"kind")?)))
        .collect::<BTreeSet<_>>().len();
    Ok(json!({"qualified_strokes":result,"strategy_confirmed_wave_count":wave_count,"confirmed_alternation_segment_count":count}))
}

/// 只读取已经产生的证书和当时行情；不会重新调用任何趋势判定函数。
pub fn project_qualification(frame:&KernelFrame,source_frame:Option<&KernelFrame>,audit:&[Value],bars:&[RuleCandle],asof:usize)->Result<Value,String>{
    if asof>=bars.len()||frame.level>3||source_frame.is_some_and(|s|s.level+1!=frame.level){return Err("qualification projection requires matching available prefix".into());}
    let bars=&bars[..=asof];let visible:Vec<_>=audit.iter().filter(|e|index(e,"bar_index").is_some_and(|i|i<=asof)&&index(e,"known_at").is_none_or(|i|i<=asof)).cloned().collect();
    let mut evidence=visible.clone();
    evidence.extend(frame.events.iter().filter(|e|e.known_index<=asof).filter_map(|e|e.evidence.get("lifecycle_event").cloned()));
    let mut squeeze:Vec<_>=evidence.iter().filter(|e|matches!(text(e,"event"),Some("squeeze_alternation_confirmed"|"squeeze_alternation_breakout"|"squeeze_alternation_invalidated"))).cloned().collect();
    for event in &evidence{if let Some(proof)=event.get("confirmation_evidence").filter(|p|text(p,"event")==Some("squeeze_alternation_confirmed")){
        squeeze.push(proof.clone());
        if let(Some(attack),Some(now),Some(high))=(index(proof,"attack"),index(proof,"bar_index"),price(proof,"a_high_price")){
            if attack<=now&&now<=asof{if let Some(earlier)=(attack..=now).find(|&i|bars[i].close>high){let mut breakout=proof.clone();breakout["event"]=json!("squeeze_alternation_breakout");breakout["breakout_index"]=json!(earlier);squeeze.push(breakout);}}
        }
    }}
    let mut seen=BTreeSet::new();squeeze.retain(|e|seen.insert(e.to_string()));let squeeze=abc::high_breakout_events(bars,&squeeze);
    let strokes=permission_strokes(frame,bars);let mut result=promote_alternation_segments(&strokes,bars,&squeeze,frame.level)?;
    let transitions:usize=strokes.iter().map(|s|s.get("key_transitions").and_then(Value::as_array).map_or(0,Vec::len)).sum();
    result["key_transition_count"]=json!(transitions);result["qualification_events"]=json!(squeeze);
    let landmarks=super::landmarks::project_landmarks_with_source(frame,bars,&visible,asof,source_frame)?;
    let mut anchors=vec![];
    if frame.level==3{for high in landmarks.get("bear_to_bull_highs").and_then(Value::as_array).into_iter().flatten(){
        let Some(low)=high.get("confirmed_low")else{continue;};let(Some(origin),Some(peak),Some(low_price),Some(high_price),Some(path),Some(available))=
            (index(low,"index"),index(high,"index"),price(low,"value"),price(high,"value"),text(high,"source_path"),text(high,"available_at"))else{continue;};
        let Some(known)=bars.iter().rposition(|b|session_date(b)==available)else{continue;};anchors.push(AbcAnchor{origin_index:origin,high_index:peak,known_index:known,origin_price:low_price,high_price,source_path:path.into()});
    }}
    let mut abc=abc::tertiary_abc_observations(bars,&anchors,&visible,false);
    for event in &mut abc{let now=index(event,"bar_index").ok_or("ABC clock missing")?;let date=session_date(&bars[now]);
        event["id"]=json!(format!("rule-abc-{}-{}-{}-{}-{}",text(event,"source_path").unwrap_or(""),index(event,"a_high_index").unwrap_or(0),index(event,"b_low_index").unwrap_or(0),index(event,"attack").unwrap_or(0),text(event,"event").unwrap_or("")));
        event["time"]=json!(date);event["available_at"]=json!(date);event["price"]=json!(bars[now].close);
        event["levels"]=json!([{"name":"a 起点","price":event["a_origin_price"]},{"name":"a 高点 / c 突破参考","price":event["a_high_price"]},
            {"name":"b 低点 / c 候选失效参考","price":event["b_low_price"]},{"name":"a 的 2/3 回撤价","price":event["two_thirds_price"]},{"name":"a 的 1/2 回撤价","price":event["half_price"]}]);
    }
    result["abc_observations"]=json!(abc);Ok(result)
}
