//! 冻结 A 波与已完成正 N 的 ABC 观察；不将候选升级为趋势或买入许可。
use super::{RuleCandle,qualification::{index,price}};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct AbcAnchor {
    pub origin_index:usize,pub high_index:usize,pub known_index:usize,
    pub origin_price:Decimal,pub high_price:Decimal,pub source_path:String,
}
/// 价格等号包含在 2/3 路径；时间及半幅收盘路径保持严格比较。
pub fn abc_pullback_evidence(bars:&[RuleCandle],anchor:&AbcAnchor,end:usize,allow_deep_pullback:bool)->Option<Value>{
    let(start,peak,low,high)=(anchor.origin_index,anchor.high_index,anchor.origin_price,anchor.high_price);
    if !(start<peak&&peak<end&&end<bars.len())||high<=low{return None;}
    if bars[start+1..=peak].iter().any(|b|b.low<low){return None;}
    let b_index=(peak+1..=end).min_by_key(|&i|(bars[i].low,i))?;let minimum=bars[b_index].low;
    if minimum<low||minimum>=high{return None;}
    let close_index=(peak+1..=b_index).min_by_key(|&i|(bars[i].close,i))?;let close=bars[close_index].close;
    let span=high-low;let a_bars=peak-start;let b_bars=b_index-peak;
    let price_path=(high-minimum)*Decimal::from(3)<=span*Decimal::TWO;
    let time_path=b_bars>a_bars&&(high-close)*Decimal::TWO>span;
    let deep=allow_deep_pullback&&(high-minimum)*Decimal::from(3)>=span*Decimal::TWO&&!price_path&&!time_path;
    if !(price_path||time_path||deep){return None;}
    let mut proof=json!({"a_origin_index":start,"a_high_index":peak,"b_low_index":b_index,
        "a_origin_price":low,"a_high_price":high,"b_low_price":minimum,"b_minimum_close":close,
        "b_minimum_close_index":close_index,"two_thirds_price":high-span*Decimal::TWO/Decimal::from(3),
        "half_price":high-span/Decimal::TWO,"retracement_ratio":(high-minimum)/span,
        "a_duration":a_bars,"b_duration":b_bars,"duration_unit":"trading_bars","price_path":price_path,"time_path":time_path});
    if deep{proof["deep_price_path"]=json!(true);}Some(proof)
}
fn kind(event:&Value)->&str{event.get("event").and_then(Value::as_str).unwrap_or("")}
/// 输入必须已经截到消费者的 asof；同一 A 同时至多保留一个活动 C。
pub fn tertiary_abc_observations(bars:&[RuleCandle],anchors:&[AbcAnchor],audit:&[Value],allow_deep_pullback:bool)->Vec<Value>{
    let mut positive=BTreeMap::new();
    for event in audit{if kind(event)=="n_completed"&&event.get("direction").and_then(Value::as_str)==Some("up"){
        if let Some(attack)=index(event,"bar_index"){positive.insert(attack,event);}
    }}
    let mut triggers=vec![];
    for event in audit{
        let regime=match kind(event){"regime_confirmation"=>match event.get("regime").and_then(Value::as_str){Some("轧空")=>"轧空",Some("强轧空")=>"强轧空",_=>continue},"squeeze_resumption_observed"=>"轧空",_=>continue};
        if let(Some(attack),Some(confirmed))=(index(event,"attack"),index(event,"bar_index")){
            if let Some(n)=positive.get(&attack){triggers.push((confirmed,attack,regime,*n));}
        }
    }
    triggers.sort_by_key(|t|(t.0,t.1));let mut out=vec![];
    for anchor in anchors{let mut busy_until=None;let mut used_lows=BTreeSet::new();
        for &(confirmed,attack,regime,n) in &triggers{
            let(Some(origin),Some(pullback),Some(known))=(index(n,"origin"),index(n,"pullback"),index(n,"known_at"))else{continue;};
            if !(anchor.high_index<origin&&origin<pullback&&pullback<=attack&&attack<=confirmed&&confirmed<bars.len())
                ||pullback==attack&&n.get("outside_close_confirmed").and_then(Value::as_bool)!=Some(true)
                ||anchor.known_index>attack||known>confirmed||busy_until.is_some_and(|i|confirmed<=i){continue;}
            let Some(mut common)=abc_pullback_evidence(bars,anchor,pullback,allow_deep_pullback)else{continue;};
            let Some(b)=index(&common,"b_low_index")else{continue;};let Some(floor)=price(&common,"b_low_price")else{continue;};
            if used_lows.contains(&b)||b>origin||bars[b..=confirmed].iter().any(|bar|bar.low<floor)
                ||bars[b..attack].iter().any(|bar|bar.close>anchor.high_price){continue;}
            used_lows.insert(b);
            for(key,value)in [("trend_level",json!(3)),("source_path",json!(anchor.source_path)),("attack",json!(attack)),
                ("n_origin",json!(origin)),("n_pullback",json!(pullback)),("candidate_index",json!(confirmed)),("regime",json!(regime)),
                ("event",json!("tertiary_c_candidate")),("bar_index",json!(confirmed))]{common[key]=value;}
            out.push(common.clone());busy_until=Some(bars.len());
            if let Some(earlier)=(attack..=confirmed).find(|&j|bars[j].close>anchor.high_price){
                let mut event=common;event["event"]=json!("tertiary_c_breakout");event["breakout_index"]=json!(earlier);out.push(event);break;
            }
            for now in confirmed..bars.len(){
                if bars[now].low<floor{let mut event=common.clone();event["event"]=json!("tertiary_c_invalidated");event["bar_index"]=json!(now);out.push(event);busy_until=Some(now);break;}
                if bars[now].close>anchor.high_price{let mut event=common.clone();event["event"]=json!("tertiary_c_breakout");event["bar_index"]=json!(now);event["breakout_index"]=json!(now);out.push(event);break;}
            }
            if busy_until==Some(bars.len()){break;}
        }
    }
    out.sort_by_key(|event|index(event,"bar_index"));out
}

/// 仅在 B 资格已经确认后接受严格最高价突破；同根双破按失效优先。
pub fn high_breakout_events(bars:&[RuleCandle],events:&[Value])->Vec<Value>{
    let mut out=vec![];
    for event in events.iter().filter(|event|kind(event)=="squeeze_alternation_confirmed"){
        let(Some(now),Some(floor),Some(peak))=(index(event,"bar_index"),price(event,"b_low_price"),price(event,"a_high_price"))else{continue;};
        if now>=bars.len(){continue;}out.push(event.clone());
        if let Some(earlier)=events.iter().find(|old|kind(old)=="squeeze_alternation_breakout"&&old.get("attack")==event.get("attack")
            &&old.get("a_high_index")==event.get("a_high_index")&&old.get("b_low_index")==event.get("b_low_index")&&index(old,"bar_index")==Some(now)){
            if let Some(i)=index(earlier,"breakout_index").filter(|i|*i<now){let mut result=earlier.clone();result["breakout_basis"]=json!("high_after_confirmed_alternation");result["breakout_price"]=json!(bars[i].high);out.push(result);continue;}
        }
        for i in now..bars.len(){
            if bars[i].low<floor{let mut result=event.clone();result["event"]=json!("squeeze_alternation_invalidated");result["bar_index"]=json!(i);out.push(result);break;}
            if bars[i].high>peak{let mut result=event.clone();result["event"]=json!("squeeze_alternation_breakout");result["bar_index"]=json!(i);result["breakout_index"]=json!(i);
                result["breakout_basis"]=json!("high_after_confirmed_alternation");result["breakout_price"]=json!(bars[i].high);out.push(result);break;}
        }
    }
    out.sort_by_key(|event|index(event,"bar_index"));out
}
