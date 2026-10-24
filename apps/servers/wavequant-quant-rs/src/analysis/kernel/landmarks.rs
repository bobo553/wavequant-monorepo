//! 已确认趋势事实的地标投影；不追加顶点，不以视窗高低价替换冻结证据。
use super::{KernelFrame,KernelPoint};
use crate::analysis::trading::{RuleCandle,qualification::{index,price},secondary::session_date};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use std::collections::BTreeSet;
fn text<'a>(v:&'a Value,key:&str)->Option<&'a str>{v.get(key)?.as_str()}
fn order(v:&Value)->(usize,usize){(index(v,"index").unwrap_or(usize::MAX),index(v,"ordinal").unwrap_or(0))}
fn reference(v:&Value)->Value{let mut out=crate::indexed_json::Map::new();for key in ["index","ordinal","time","kind","value","available_at","label"]{if let Some(value)=v.get(key){out.insert(key.into(),value.clone());}}Value::Object(out)}
fn complete(v:&Value,kind:&str)->bool{text(v,"kind")==Some(kind)&&index(v,"index").is_some()&&price(v,"value").is_some()&&v.get("available_at").is_some()}
fn greater(high:&Value,key:&Value)->bool{complete(high,"H")&&complete(key,"H")&&price(high,"value").zip(price(key,"value")).is_some_and(|(h,k)|h>k)}
fn known(v:&Value)->String{text(v,"available_at").unwrap_or("").into()}
fn known_max(rows:&[&Value])->String{rows.iter().map(|v|known(v)).max().unwrap_or_default()}
fn points(stroke:&Value)->&[Value]{stroke.get("points").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])}
fn closed(v:&Value)->bool{!matches!(text(v,"state"),Some("seed"|"developing"))&&v.get("display_only").and_then(Value::as_bool)!=Some(true)}
fn confirmation_high(v:&Value)->Option<&Value>{if complete(v,"H"){Some(v)}else{v.as_array()?.iter().filter(|p|complete(p,"H")).max_by_key(|p|order(p))}}
fn sort(rows:&mut Vec<Value>){rows.sort_by_key(|p|(order(p),known(p),text(p,"source_path").unwrap_or("").to_string()));let mut ids=BTreeSet::new();rows.retain(|p|ids.insert((text(p,"source_path").unwrap_or("").to_string(),text(p,"id").unwrap_or("").to_string())));}
fn high_landmark(path:&str,high:&Value,low:&Value,key:&Value,level:u8,at:String)->Value{
    let mut out=reference(high);out["id"]=json!(format!("level{level}-bear-to-bull-high-{}-{}-{}-{}-{at}",order(high).0,order(high).1,order(low).0,order(low).1));
    out["label"]=json!(text(high,"label").unwrap_or("H·翻多"));out["trend_level"]=json!(level);out["source_level"]=json!(level.saturating_sub(1));out["source_path"]=json!(path);
    out["source_available_at"]=high["available_at"].clone();out["available_at"]=json!(at);out["flip"]=json!("翻空为多");out["confirmation_rule"]=json!("strict_last_fall_high_break");
    out["confirmed_low"]=reference(low);out["broken_key"]=reference(key);out
}
fn observed_highs(strokes:&[Value],level:u8)->Vec<Value>{let mut out=vec![];
    for stroke in strokes{let Some(path)=text(stroke,"id")else{continue;};let rows=points(stroke);
        for high in rows.iter().filter(|p|complete(p,"H")&&closed(p)){
            let mut observations=high.get("observations").and_then(Value::as_array).cloned().unwrap_or_default();
            if level>1&&!observations.iter().any(|e|text(e,"title")==Some("翻空为多")){
                for triple in rows.windows(3){if order(&triple[2])==order(high)&&text(&triple[1],"flip")==Some("翻空为多")&&complete(&triple[1],"L")
                    &&greater(high,&triple[0])&&order(&triple[0])<order(&triple[1])&&order(&triple[1])<order(high){
                    observations.push(json!({"title":"翻空为多","key":triple[0],"confirmed_low":triple[1],"available_at":known_max(&[high,&triple[0],&triple[1]])}));}}
            }
            for e in observations.iter().filter(|e|text(e,"title")==Some("翻空为多")){
                let(Some(low),Some(key))=(e.get("confirmed_low"),e.get("key"))else{continue;};if !complete(low,"L")||!greater(high,key){continue;}
                out.push(high_landmark(path,high,low,key,level,known_max(&[high,e,low,key])));
            }
        }
    }out
}
fn active_highs(strokes:&[Value],rows:Vec<Value>)->Vec<Value>{rows.into_iter().filter(|high|{
    let Some(low)=high.get("confirmed_low").filter(|p|complete(p,"L"))else{return false;};let Some(floor)=price(low,"value")else{return false;};
    let current=strokes.iter().find(|s|text(s,"id")==text(high,"source_path")).map(points).unwrap_or(&[]);
    !current.iter().filter(|p|closed(p)).any(|p|complete(p,"L")&&order(p)>order(high)&&price(p,"value").is_some_and(|p|p<floor)
        ||order(p)==order(high)&&text(p,"flip")==Some("翻多为空")&&p.get("confirmed_by").is_some_and(|c|complete(c,"L")&&price(c,"value").is_some_and(|p|p<floor)))
}).collect()}
pub fn bear_to_bull_highs(strokes:&[Value],level:u8)->Vec<Value>{
    let observed=observed_highs(strokes,level);let mut rows=vec![];
    if level>1{for stroke in strokes{let Some(path)=text(stroke,"id")else{continue;};for low in points(stroke).iter().filter(|p|complete(p,"L")&&closed(p)&&text(p,"flip")==Some("翻空为多")){
        let(Some(high),Some(key))=(low.get("confirmed_by").and_then(confirmation_high),low.get("broken_key"))else{continue;};if !greater(high,key){continue;}
        if observed.iter().any(|p|text(p,"source_path")==Some(path)&&p.get("confirmed_low").is_some_and(|p|order(p)==order(low))){continue;}
        rows.push(high_landmark(path,high,low,key,level,known_max(&[low,high])));
    }}}
    rows.extend(observed);let mut rows=active_highs(strokes,rows);sort(&mut rows);rows
}
fn low_landmark(path:&str,low:&Value,high:&Value,origin:&Value,key:&Value,level:u8,source_level:u8,rule:&str,at:String)->Option<Value>{
    let(bottom,peak,start)=(price(low,"value")?,price(high,"value")?,price(origin,"value")?);if !(start<bottom&&bottom<peak&&order(high)<order(low)){return None;}
    let ratio=(peak-bottom)/(peak-start);let mut out=reference(low);
    out["id"]=json!(format!("level{level}-bear-bull-alternation-low-{}-{}-{}-{}-{at}",order(high).0,order(high).1,order(low).0,order(low).1));
    out["label"]=json!(text(low,"label").unwrap_or("L·交替"));out["trend_level"]=json!(level);out["source_level"]=json!(source_level);out["source_path"]=json!(path);
    out["source_available_at"]=low["available_at"].clone();out["available_at"]=json!(at);out["flip"]=json!("空多交替");out["confirmation_rule"]=json!(rule);
    out["retracement_ratio"]=json!(ratio);out["weak_countermove"]=json!((peak-bottom)*Decimal::from(3)<peak-start);
    out["confirmed_flip_high"]=reference(high);out["confirmed_bear_low"]=reference(origin);out["broken_key"]=reference(key);out["retracement_origin"]=reference(origin);Some(out)
}
fn duration(rows:Vec<Value>,bars:&[RuleCandle])->Vec<Value>{rows.into_iter().filter_map(|mut row|{
    let origin=row.get("confirmed_bear_low")?;let high=row.get("confirmed_flip_high")?;
    let(a,h,b)=(index(origin,"index")?,index(high,"index")?,index(&row,"index")?);if !(a<h&&h<b&&b<bars.len()){return None;}
    let(low,top,bottom)=(price(origin,"value")?,price(high,"value")?,price(&row,"value")?);let close=bars[h+1..=b].iter().map(|p|p.close).min()?;
    if top>low&&(top-close)*Decimal::from(3)<(top-low)*Decimal::TWO&&(b-h)*2<h-a{
        let cross=(b+1..bars.len()).find(|&i|bars[i].close>top)?;if bars[b..=cross].iter().any(|p|p.low<bottom){return None;}
        let at=known(&row).max(session_date(&bars[cross]));row["available_at"]=json!(at);row["source_available_at"]=json!(at);row["duration_confirmation"]=json!("close_above_a_high_after_short_pullback");
    }Some(row)
}).collect()}
pub fn bear_bull_alternation_lows(strokes:&[Value],source:&[Value],bars:&[RuleCandle],level:u8)->Vec<Value>{
    let mut rows=vec![];
    for stroke in strokes{let Some(path)=text(stroke,"id")else{continue;};for low in points(stroke).iter().filter(|p|complete(p,"L")&&closed(p)){
        for event in low.get("observations").and_then(Value::as_array).into_iter().flatten(){
            if text(event,"title")!=Some("空多交替"){continue;}let(Some(high),Some(origin),Some(key),Some(retrace),Some(ratio))=
                (event.get("flip_high"),event.get("confirmed_bear_low"),event.get("broken_key"),event.get("origin"),price(event,"ratio"))else{continue;};
            if !greater(high,key)||!complete(origin,"L")||!complete(retrace,"L")||ratio<=Decimal::ZERO||ratio*Decimal::from(3)>=Decimal::TWO
                ||price(retrace,"value").zip(price(low,"value")).is_none_or(|(a,b)|a>=b){continue;}
            if let Some(mut row)=low_landmark(path,low,high,origin,key,level,level,"confirmed_higher_pullback_strictly_below_two_thirds",known_max(&[low,event,high,origin,key,retrace])){
                row["retracement_origin"]=reference(retrace);row["retracement_ratio"]=json!(ratio);row["weak_countermove"]=json!(event.get("weak_countermove").and_then(Value::as_bool).unwrap_or(false));rows.push(row);}
        }
    }}
    if level>1{for high in observed_highs(strokes,level){let Some(path)=text(&high,"source_path")else{continue;};
        if rows.iter().any(|p|text(p,"source_path")==Some(path)&&p.get("confirmed_flip_high").is_some_and(|h|order(h)==order(&high))){continue;}
        let Some(formal)=strokes.iter().find(|s|text(s,"id")==Some(path)).and_then(|s|points(s).iter().find(|p|order(p)==order(&high)))else{continue;};
        let(Some(low),Some(origin),Some(key))=(formal.get("confirmed_by"),high.get("confirmed_low"),high.get("broken_key"))else{continue;};
        if !complete(low,"L"){continue;}if let Some(row)=low_landmark(path,low,&high,origin,key,level,level-1,"confirmed_source_pullback_after_same_level_flip",known_max(&[&high,low])){
            if price(&row,"retracement_ratio").is_some_and(|r|r*Decimal::from(3)<Decimal::TWO){rows.push(row);}}
    }}
    for high in bear_to_bull_highs(strokes,level){let Some(path)=text(&high,"source_path")else{continue;};
        if rows.iter().any(|p|text(p,"source_path")==Some(path)&&p.get("confirmed_flip_high").is_some_and(|h|order(h)==order(&high))){continue;}
        let Some(stroke)=strokes.iter().find(|s|text(s,"id")==Some(path))else{continue;};let Some(position)=points(stroke).iter().position(|p|order(p)==order(&high))else{continue;};
        let low=if level<=1{points(stroke).get(position+1)}else{points(stroke)[position].get("confirmed_by").filter(|p|complete(p,"L")).and_then(|r|
            source.iter().find(|s|text(s,"id")==text(stroke,"source_path")).and_then(|s|points(s).iter().find(|p|order(p)==order(r)&&p.get("value")==r.get("value")&&p.get("time")==r.get("time"))))};
        let Some(low)=low.filter(|p|complete(p,"L")&&closed(p))else{continue;};let(Some(rebreak),Some(origin),Some(key))=(low.get("confirmed_by").and_then(confirmation_high),high.get("confirmed_low"),high.get("broken_key"))else{continue;};
        if !greater(rebreak,&high)||order(low)>=order(rebreak){continue;}
        if let Some(mut row)=low_landmark(path,low,&high,origin,key,level,if level<=1{level}else{level-1},"confirmed_higher_pullback_then_confirmed_flip_high_rebreak",known_max(&[&high,low,rebreak])){
            row["confirmed_rebreak_high"]=reference(rebreak);rows.push(row);}
    }
    let mut rows=duration(rows,bars);sort(&mut rows);rows
}
pub fn post_alternation_bull_highs(strokes:&[Value],lows:&[Value],level:u8)->Vec<Value>{let mut rows=vec![];
    for low in lows{let Some(path)=text(low,"source_path")else{continue;};let Some(stroke)=strokes.iter().find(|s|text(s,"id")==Some(path))else{continue;};
        let mut vertices:Vec<_>=points(stroke).iter().filter(|p|closed(p)).collect();vertices.sort_by_key(|p|order(p));let Some(position)=vertices.iter().position(|p|order(p)==order(low))else{continue;};
        let Some(high)=vertices.get(position+1).filter(|p|complete(p,"H"))else{continue;};if price(high,"value").zip(price(low,"value")).is_none_or(|(a,b)|a<=b){continue;}
        let at=known_max(&[low,high]);let mut row=reference(high);row["id"]=json!(format!("level{level}-post-alternation-bull-high-{}-{}-{}-{}-{at}",order(low).0,order(low).1,order(high).0,order(high).1));
        row["label"]=json!(text(high,"label").unwrap_or("H·多头段"));row["trend_level"]=json!(level);row["source_level"]=json!(level);row["source_path"]=json!(path);
        row["source_available_at"]=high["available_at"].clone();row["available_at"]=json!(at);row["flip"]=json!("空多交替后首段多头高点");
        row["confirmation_rule"]=json!("first_confirmed_same_level_high_after_bear_bull_alternation");row["confirmed_alternation_low"]=reference(low);
        for field in ["confirmed_flip_high","confirmed_bear_low","broken_key","retracement_origin","retracement_ratio"]{row[field]=low[field].clone();}rows.push(row);
    }sort(&mut rows);rows
}
pub fn bullish_turn_signals(lows:&[Value],bars:&[RuleCandle],level:u8)->Vec<Value>{let mut rows=vec![];
    for low in lows{let Some(high)=low.get("confirmed_flip_high").filter(|h|complete(h,"H"))else{continue;};let Some(threshold)=price(high,"value")else{continue;};
        let Some(now)=(1..bars.len()).find(|&i|session_date(&bars[i])>known(low)&&bars[i-1].close<=threshold&&bars[i].close>threshold)else{continue;};let date=session_date(&bars[now]);
        let mut row=json!({"id":format!("level{level}-bullish-turn-signal-{}-{}-{now}-{date}",order(low).0,order(low).1),"index":now,"ordinal":0,"time":date,"kind":"K","value":bars[now].close,
            "available_at":date,"label":format!("K{}·转多",now+1),"trend_level":level,"source_level":level,"source_path":low.get("source_path"),"source_available_at":date,"flip":"转多信号",
            "confirmation_rule":"first_strict_close_cross_above_flip_high_after_confirmed_alternation","breakout_level":threshold,"previous_close":bars[now-1].close,
            "open":bars[now].open,"high":bars[now].high,"low":bars[now].low,"close":bars[now].close,"confirmed_alternation_low":reference(low),"confirmed_flip_high":reference(high)});
        for field in ["confirmed_bear_low","broken_key","retracement_origin","retracement_ratio"]{row[field]=low[field].clone();}rows.push(row);
    }sort(&mut rows);rows
}
fn normalize(value:&Value,bars:&[RuleCandle])->Value{match (value).kind() {
    Some(crate::indexed_json::Kind::Array(rows))=>Value::Array(rows.iter().map(|v|normalize(v,bars)).collect()),
    Some(crate::indexed_json::Kind::Object(fields))=>{let mut out:crate::indexed_json::Map<String,Value>=fields.iter().map(|(k,v)|(k.clone(),normalize(v,bars))).collect();
        for key in ["available_at","source_available_at"]{if let Some(i)=fields.get(key).and_then(Value::as_u64).and_then(|i|usize::try_from(i).ok()).filter(|i|*i<bars.len()){out.insert(key.into(),json!(session_date(&bars[i])));}}
        if let Some(i)=index(value,"index").filter(|i|*i<bars.len()){out.insert("time".into(),json!(session_date(&bars[i])));}Value::Object(out)},_=>value.clone()}}
fn point_reference(point:&KernelPoint,bars:&[RuleCandle])->Option<Value>{
    if point.index>=bars.len()||point.known_index>=bars.len(){return None;}
    Some(json!({"index":point.index,"ordinal":point.ordinal,"time":session_date(&bars[point.index]),"kind":point.kind,"value":point.value,
        "available_at":session_date(&bars[point.known_index]),"label":if point.kind==super::PointKind::H{"H"}else{"L"}}))
}
fn scalar_reference(proof:&Value,index_field:&str,price_field:&str,kind:&str,ordinal_field:&str,known_field:&str,bars:&[RuleCandle],now:usize)->Option<Value>{
    let i=index(proof,index_field)?;let value=price(proof,price_field)?;let known=index(proof,known_field).unwrap_or(now);
    if i>known||known>now||known>=bars.len()||value<=Decimal::ZERO{return None;}
    Some(json!({"index":i,"ordinal":index(proof,ordinal_field).unwrap_or(0),"time":session_date(&bars[i]),"kind":kind,"value":value,"available_at":session_date(&bars[known]),"label":kind}))
}
fn cycle_path(raw:&str,level:u8)->String{if raw.starts_with("level"){raw.into()}else{format!("level{level}-{raw}")}}
/// 所有公开价格/日期均来自同一前缀；交易确认可以补充地标，不能重写几何。
pub fn project_landmarks(frame:&KernelFrame,bars:&[RuleCandle],trading_events:&[Value],asof:usize)->Result<Value,String>{
    project_landmarks_with_source(frame,bars,trading_events,asof,None)
}
pub fn project_landmarks_with_source(frame:&KernelFrame,bars:&[RuleCandle],trading_events:&[Value],asof:usize,source:Option<&KernelFrame>)->Result<Value,String>{
    if asof>=bars.len()||frame.level>3||source.is_some_and(|s|s.level+1!=frame.level){return Err("landmarks require matching available level prefix".into());}
    let bars=&bars[..=asof];let level=frame.level;let projected=normalize(&if level==0{super::geometry(frame)}else{super::permission_geometry(frame)},bars);
    let strokes=projected.get("strokes").and_then(Value::as_array).cloned().unwrap_or_default();
    let source=source.map(|s|normalize(&super::geometry(s),bars)).and_then(|s|s.get("strokes").and_then(Value::as_array).cloned()).unwrap_or_default();
    let mut highs=bear_to_bull_highs(&strokes,level);let mut lows=bear_bull_alternation_lows(&strokes,&source,bars,level);
    let mut bear_flips=vec![];let mut bear_alternations=vec![];
    let mut proofs:Vec<Value>=frame.events.iter().filter(|e|e.known_index<=asof).filter_map(|e|e.evidence.get("lifecycle_event").cloned()).collect();
    proofs.extend(trading_events.iter().filter(|e|index(e,"trend_level")==Some(usize::from(level))&&index(e,"bar_index").is_some_and(|i|i<=asof)).cloned());
    proofs.sort_by_key(|p|index(p,"bar_index"));let mut unique=BTreeSet::new();proofs.retain(|p|unique.insert(p.to_string()));
    for proof in &proofs{
        if proof.get("flip_confirmed").and_then(Value::as_bool)!=Some(true)||!matches!(text(proof,"event"),Some("trend_lifecycle_state"|"trend_lifecycle_anchor")){continue;}
        let(Some(now),Some(raw))=(index(proof,"bar_index"),text(proof,"source_path"))else{continue;};if now>asof{continue;}let path=cycle_path(raw,level);
        let up=text(proof,"direction")==Some("up");let state=text(proof,"state").unwrap_or("");
        let(Some(mut origin),Some(mut flip),Some(mut key))=(scalar_reference(proof,"origin_index","origin_price",if up{"L"}else{"H"},"origin_ordinal","source_known_index",bars,now),
            scalar_reference(proof,"flip_index","breakout_level",if up{"H"}else{"L"},"flip_ordinal","flip_known_index",bars,now),
            scalar_reference(proof,"broken_key_index","broken_key_price",if up{"H"}else{"L"},"broken_key_ordinal","broken_key_known_index",bars,now))else{continue;};
        for event in frame.events.iter().filter(|e|e.known_index<=now){if let Some(p)=event.origin.as_ref().filter(|p|p.index==order(&origin).0&&p.path.to_string()==raw.rsplit('-').next().unwrap_or("")){if let Some(r)=point_reference(p,bars){origin=r;}}
            if let Some(p)=event.key.as_ref().filter(|p|p.index==order(&key).0&&price(&key,"value").is_some_and(|v|p.value.to_string().parse::<Decimal>().ok()==Some(v))){if let Some(r)=point_reference(p,bars){key=r;}}
            if event.point.index==order(&flip).0&&price(&flip,"value").is_some_and(|v|event.point.value.to_string().parse::<Decimal>().ok()==Some(v)){if let Some(r)=point_reference(&event.point,bars){flip=r;}}
        }
        if up&&matches!(state,"空翻多"|"空多交替"|"转多确认")&&greater(&flip,&key){
            let at=index(proof,"source_known_index").unwrap_or(now).min(now);let mut item=high_landmark(&path,&flip,&origin,&key,level,session_date(&bars[at]));
            for f in ["special_bull_recovery","original_low_index","breach_index","recovered_index"]{if let Some(v)=proof.get(f){item[f]=v.clone();}}
            highs.push(item);
            if state=="空多交替"&&crate::analysis::trading::qualification::confirmed_alternation_event(proof){
                let Some(low)=scalar_reference(proof,"counter_index","counter_price","L","counter_ordinal","alternation_index",bars,now)else{continue;};
                if let Some(mut item)=low_landmark(&path,&low,&flip,&origin,&key,level,level,text(proof,"confirmation_rule").unwrap_or(""),session_date(&bars[now])){
                    item["confirmation_evidence"]=proof.get("confirmation_evidence").cloned().unwrap_or(Value::Null);item["special_bull_recovery"]=proof.get("special_bull_recovery").cloned().unwrap_or(json!(false));lows.push(item);}
            }
        }else if !up&&matches!(state,"多翻空"|"多空交替"|"转空确认")&&price(&flip,"value").zip(price(&key,"value")).is_some_and(|(a,b)|a<b){
            let mut item=reference(&flip);item["id"]=json!(format!("level{level}-bull-to-bear-low-{}-{}-{}",path,order(&flip).0,order(&flip).1));item["trend_level"]=json!(level);item["source_level"]=json!(level);
            item["source_path"]=json!(path);item["flip"]=json!("翻多为空");item["confirmed_high"]=reference(&origin);item["broken_key"]=reference(&key);item["confirmation_rule"]=json!("strict_last_rise_low_break");bear_flips.push(item);
            if state=="多空交替"&&crate::analysis::trading::qualification::confirmed_alternation_event(proof){
                if let Some(mut high)=scalar_reference(proof,"counter_index","counter_price","H","counter_ordinal","alternation_index",bars,now){
                    high["id"]=json!(format!("level{level}-bull-bear-alternation-high-{}-{}-{now}",order(&flip).0,order(&high).0));high["trend_level"]=json!(level);high["source_level"]=json!(level);high["source_path"]=json!(path);
                    high["flip"]=json!("多空交替");high["confirmed_flip_low"]=reference(&flip);high["confirmed_bull_high"]=reference(&origin);high["broken_key"]=reference(&key);high["confirmation_rule"]=proof["confirmation_rule"].clone();bear_alternations.push(high);}
            }
        }
    }
    // 撤销只为对应历史资格补充失效时钟，不能抹去已经发生的确认。
    for low in &mut lows{let origin=low.get("confirmed_bear_low").and_then(|p|index(p,"index"));let flip=low.get("confirmed_flip_high").and_then(|p|index(p,"index"));
        let failed=proofs.iter().filter(|p|cycle_path(text(p,"source_path").unwrap_or(""),level)==text(low,"source_path").unwrap_or("")
            &&matches!(text(p,"event"),Some("trend_lifecycle_confirmation_revoked"|"trend_lifecycle_invalidated"))
            &&(index(p,"origin_index").is_none_or(|i|Some(i)==origin))&&(index(p,"flip_index").is_none_or(|i|Some(i)==flip)))
            .filter_map(|p|index(p,"bar_index").filter(|i|*i<bars.len()&&session_date(&bars[*i])>=known(low))).min();
        if let Some(failed)=failed{low["invalidated_at"]=json!(session_date(&bars[failed]));}
    }
    highs=active_highs(&strokes,highs);sort(&mut highs);sort(&mut lows);sort(&mut bear_flips);sort(&mut bear_alternations);
    let post=post_alternation_bull_highs(&strokes,&lows,level);let bullish=bullish_turn_signals(&lows,bars,level);let mut bearish=vec![];
    for high in &bear_alternations{let Some(low)=high.get("confirmed_flip_low")else{continue;};let(Some(threshold),Some(defense))=(price(low,"value"),price(high,"value"))else{continue;};
        for now in 1..bars.len(){if session_date(&bars[now])<=known(high){continue;}if bars[now].high>defense{break;}if bars[now-1].close>=threshold&&bars[now].close<threshold{
            let date=session_date(&bars[now]);bearish.push(json!({"id":format!("level{level}-bearish-turn-{}-{}-{now}",text(high,"source_path").unwrap_or(""),order(low).0),"index":now,"ordinal":0,"time":date,"available_at":date,"kind":"K",
                "value":bars[now].close,"close":bars[now].close,"previous_close":bars[now-1].close,"label":format!("K{}·转空",now+1),"flip":"转空信号","trend_level":level,"source_level":level,"source_path":high["source_path"],
                "breakout_level":threshold,"confirmed_flip_low":reference(low),"confirmed_alternation_high":reference(high),"confirmation_rule":"first_strict_close_cross_below_flip_low_after_confirmed_alternation"}));break;}}
    }sort(&mut bearish);
    Ok(json!({"bear_to_bull_highs":highs,"bull_to_bear_lows":bear_flips,"bear_bull_alternation_lows":lows,"bull_bear_alternation_highs":bear_alternations,
        "post_alternation_bull_highs":post,"bullish_turn_signals":bullish,"bearish_turn_signals":bearish}))
}
