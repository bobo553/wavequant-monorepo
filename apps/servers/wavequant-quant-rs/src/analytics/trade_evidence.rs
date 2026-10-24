//! 成交账本与决策证据只连接已有时点，不反向创造信号或成交。
use super::{AnalyticsResult,float,date,merge};
use crate::analysis::trading::RuleCandle;
use crate::execution::{StrategyBacktest,Side};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

pub fn ledger(result:&StrategyBacktest,grouped:&BTreeMap<String,Vec<RuleCandle>>,signals:&[Value],audit:&[Value])->AnalyticsResult<(Vec<Value>,Vec<Value>,Value)>{
    let mut orders=vec![];let mut active=BTreeMap::<String,String>::new();let mut quantities=BTreeMap::<String,rust_decimal::Decimal>::new();let mut serial=0;
    for order in &result.orders{
        let local=order.timestamp.with_timezone(&chrono::FixedOffset::east_opt(8*3600).ok_or("Shanghai offset unavailable")?);
        let when=local.to_rfc3339();let bars=grouped.get(&order.symbol).ok_or("order symbol has no source bars")?;
        let bar=bars.iter().find(|bar|date(&bar.timestamp)==date(&when)).ok_or("order date has no source bar")?;
        let mut row=crate::indexed_json::to_value(order).map_err(|e|e.to_string())?;
        merge(&mut row,&crate::indexed_json::to_value(&order.evidence).map_err(|e|e.to_string())?);
        row["timestamp"]=json!(when);row["side"]=json!(if order.side==Side::Buy{"BUY"}else{"SELL"});
        row["execution_model"]=crate::indexed_json::to_value(order.mode).map_err(|e|e.to_string())?;
        row["price_basis"]=json!("causal_adjusted_equivalent");row["adjustment_factor"]=json!(float(bar.adjustment_factor)?);
        if let Some(price)=order.price{row["price"]=json!(float(price)?);row["raw_price"]=json!(float(price/bar.adjustment_factor)?);}
        if let Some(quantity)=order.quantity{row["quantity"]=json!(float(quantity)?);row["raw_shares"]=json!(float(quantity*bar.adjustment_factor)?);}
        if let Some(fee)=order.fee{row["fee"]=json!(float(fee)?);}
        let signal_stamp=row["signal_timestamp"].as_str();
        let signal=signals.iter().find(|s|s["timestamp"].as_str()==signal_stamp&&s["side"].as_str()==Some(if order.side==Side::Buy{"LONG"}else{"EXIT"}));
        let mut evidence=vec![];
        if let Some(signal)=signal{let index=signal["bar_index"].as_u64().ok_or("signal bar index missing")?;
            for event in audit.iter().filter(|e|e["timestamp"].as_str()==signal_stamp&&matches!(e["event"].as_str(),Some("long_signal"|"long_transition_evidence"|"exit_signal"))){
                let bar_index=event["bar_index"].as_u64().ok_or("decision evidence index missing")?;
                if bar_index.max(event["known_at"].as_u64().unwrap_or(bar_index))>index{continue;}
                let mut item=event.clone();let keys:Vec<_>=item.as_object().into_iter().flat_map(|m|m.keys()).filter(|k|k.ends_with("_index")||k.as_str()=="attack"||k.as_str()=="eligibility_frozen_at").cloned().collect();
                for key in keys{if let Some(i)=item[&key].as_u64().filter(|i|*i<=index).and_then(|i|usize::try_from(i).ok()).filter(|i|*i<bars.len()){
                    item[format!("{key}_date")]=json!(date(&bars[i].timestamp));}}
                evidence.push(item);
            }
            if let Some(trigger)=signal.get("trigger_timestamp"){row["trigger_timestamp"]=trigger.clone();}
        }
        row["decision_evidence"]=json!(evidence);
        // 具体条件字段必须来自策略当时的证明；缺失不推算为通过。
        if !row.get("entry_conditions").is_some(){row["entry_conditions"]=json!([]);row["entry_conditions_status"]=json!("not_supplied_by_strategy");}
        if order.status=="filled"{
            let quantity=order.quantity.ok_or("filled order quantity missing")?;
            if order.side==Side::Buy&&!active.contains_key(&order.symbol){serial+=1;active.insert(order.symbol.clone(),format!("{}-trade-{serial}",order.symbol));}
            row["trade_id"]=json!(active.get(&order.symbol));
            let held=quantities.entry(order.symbol.clone()).or_default();
            if order.side==Side::Buy{*held+=quantity;}else{if quantity>*held{return Err("sell fill exceeds matching analytics holding".into());}*held-=quantity;if held.is_zero(){active.remove(&order.symbol);}}
        }
        orders.push(row);
    }
    let mut positions=vec![];
    for position in result.positions.values(){let mut row=crate::indexed_json::to_value(position).map_err(|e|e.to_string())?;
        if let Some(id)=active.get(&position.symbol){row["trade_id"]=json!(id);}positions.push(row);}
    let mut counts=BTreeMap::<String,usize>::new();let mut rejections=BTreeMap::<String,usize>::new();
    for event in audit{if let Some(kind)=event["event"].as_str(){*counts.entry(kind.into()).or_default()+=1;
        if matches!(kind,"entry_rejected"|"entry_preflight_rejected"){*rejections.entry(event["reason"].as_str().unwrap_or("unknown").into()).or_default()+=1;}}}
    Ok((orders,positions,json!({"events":counts,"rejection_reasons":rejections})))
}
pub fn result_markers(result:&Value)->AnalyticsResult<Vec<Value>>{
    let mut markers=vec![];
    for(i,order)in result["orders"].as_array().into_iter().flatten().enumerate(){let mut row=order.clone();
        row["id"]=json!(format!("stock-order-{i}"));row["time"]=json!(date(order["timestamp"].as_str().ok_or("order timestamp missing")?));
        row["kind"]=json!(if order["status"]=="filled"{"fill"}else{"order"});row["stop"]=order["stop_price"].clone();row["target"]=order["target_price"].clone();
        row["signal_time"]=json!(date(order["signal_timestamp"].as_str().unwrap_or("")));row["source"]=json!("single_stock_backtest");markers.push(row);}
    for(i,signal)in result["signals"].as_array().into_iter().flatten().enumerate(){
        let evidence:Vec<_>=result["audit"].as_array().into_iter().flatten().filter(|e|e["timestamp"]==signal["timestamp"] &&
            (e["event"]=="long_transition_evidence"||e["event"]=="long_signal"&&e["channel"].as_str().is_some_and(|channel|signal["reason"]==format!("system_{channel}")))).cloned().collect();
        markers.push(json!({"id":format!("stock-signal-{i}"),"time":signal.get("time").cloned().unwrap_or_else(||json!(date(signal["timestamp"].as_str().unwrap_or("")))),
            "kind":"signal","side":signal["side"],"price":signal["reference_price"],"reason":signal["reason"],"regime":signal["regime"],"rvol":signal["rvol"],
            "stop":if signal["side"]=="LONG"{signal["invalidation_price"].clone()}else{Value::Null},
            "target":if signal["side"]=="LONG"{signal["target_price"].clone()}else{Value::Null},"source":"single_stock_backtest","decision_evidence":evidence}));}
    markers.sort_by(|a,b|a["time"].as_str().cmp(&b["time"].as_str()));Ok(markers)
}
pub fn ensure_single_stock(result:&StrategyBacktest,symbol:&str)->AnalyticsResult<()>{
    let mut symbols=BTreeSet::new();for order in &result.orders{symbols.insert(order.symbol.as_str());}for trade in &result.trades{symbols.insert(trade.symbol.as_str());}
    for s in result.positions.keys(){symbols.insert(s.as_str());}for s in result.marks.keys(){symbols.insert(s.as_str());}
    if symbols.iter().any(|s|*s!=symbol){return Err("stock view requires an independent single-stock account".into());}Ok(())
}
