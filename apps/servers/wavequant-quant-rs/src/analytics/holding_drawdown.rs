//! 持仓周期按真实成交建立；MAE 分母为当时平均成本，缺边界分钟只报告已观测下界。
use super::{AnalyticsResult,dec,float,date};
use crate::{analysis::trading::RuleCandle,data::minute::{MinuteBar,expected_times}};
use chrono::{DateTime,NaiveDate,NaiveDateTime,FixedOffset,Timelike,Duration};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};
pub const METRIC_VERSION:&str="holding_entry_cost_mae_cycle_v3";
pub type MinuteInputs=BTreeMap<String,BTreeMap<String,Vec<MinuteBar>>>;
fn local(value:&str)->AnalyticsResult<NaiveDateTime>{
    if let Ok(stamp)=DateTime::parse_from_rfc3339(value){return Ok(stamp.with_timezone(&FixedOffset::east_opt(8*3600).ok_or("Shanghai timezone unavailable")?).naive_local());}
    NaiveDateTime::parse_from_str(value,"%Y-%m-%dT%H:%M:%S%.f").or_else(|_|NaiveDateTime::parse_from_str(value,"%Y-%m-%d %H:%M:%S%.f"))
        .ok().or_else(||NaiveDate::parse_from_str(value,"%Y-%m-%d").ok().and_then(|d|d.and_hms_opt(0,0,0))).ok_or_else(||"invalid holding timestamp".into())
}
fn stamp(value:NaiveDateTime)->String{value.format("%Y-%m-%dT%H:%M:%S%.f").to_string()}
pub fn holding_fill_time(order:&Value)->AnalyticsResult<NaiveDateTime>{
    let explicit=order["execution_timestamp"].as_str();let value=local(explicit.or_else(||order["timestamp"].as_str()).ok_or("holding fill time missing")?)?;
    if explicit.is_some()||order["execution_model"]=="intraday_5m_next_open"{return Ok(value);}
    value.date().and_hms_opt(if order["execution_model"]=="same_day_close"{15}else{9},if order["execution_model"]=="same_day_close"{0}else{30},0).ok_or_else(||"holding fill clock unavailable".into())
}
#[derive(Clone)]
struct Segment{start:NaiveDateTime,start_price:Decimal,end:Option<NaiveDateTime>,end_price:Option<Decimal>,cost:Decimal,cost_time:NaiveDateTime,quantity:Decimal}
fn verify_minutes(rows:&[MinuteBar],bar:&RuleCandle)->bool{
    if rows.len()!=48{return false;}let factor=match float(bar.adjustment_factor){Ok(v)=>v,Err(_)=>return false};let volume=match float(bar.volume){Ok(v)=>v,Err(_)=>return false};
    for(row,time)in rows.iter().zip(expected_times()){
        let Ok(timepoint)=local(&row.timestamp)else{return false;};
        if timepoint.date().to_string()!=date(&bar.timestamp)||timepoint.hour()*60+timepoint.minute()!=time||timepoint.second()!=0||timepoint.nanosecond()!=0{return false;}
        if [row.open,row.high,row.low,row.close,row.volume].iter().any(|v|!v.is_finite())||row.low<=0.0||row.low>row.open.min(row.close)||row.high<row.open.max(row.close)||row.volume<0.0{return false;}
    }
    let high=rows.iter().map(|m|m.high).fold(f64::NEG_INFINITY,f64::max);let low=rows.iter().map(|m|m.low).fold(f64::INFINITY,f64::min);
    for(actual,expected)in[(rows[0].open,bar.open),(high,bar.high),(low,bar.low),(rows[47].close,bar.close)]{
        let Ok(expected)=float(expected)else{return false;};if (actual*factor-expected).abs()>0.015*factor{return false;}}
    (rows.iter().map(|m|m.volume).sum::<f64>()-volume).abs()<=1.0_f64.max(volume*0.00001)
}
struct Observation {drawdown:Decimal,basis:Decimal,low:Decimal,basis_time:String,low_time:String,loss:Decimal,source:String,
    missing:BTreeSet<String>,excluded:BTreeSet<String>,resolutions:BTreeSet<String>,points:Vec<Value>}
impl Observation {
    fn observe(&mut self,price:Decimal,time:String,source:&str,segment:&Segment)->AnalyticsResult<()>{
        if price<=Decimal::ZERO{return Err("invalid holding drawdown price".into());}
        let current=price/segment.cost-Decimal::ONE;
        if current<self.drawdown{self.drawdown=current;self.basis=segment.cost;self.low=price;self.basis_time=stamp(segment.cost_time);self.low_time=time.clone();self.loss=(price-self.basis)*segment.quantity;self.source=source.into();}
        let known=if source=="daily_low"{format!("{time}T15:00:00")}else{time};
        let point=json!({"timestamp":known,"value":if self.missing.first().is_some_and(|day|day.as_str()<=date(&known)){None}else{Some(float(self.drawdown)?)},"quantity":float(segment.quantity)?});
        if self.points.last().is_some_and(|p|p["timestamp"]==known){self.points.pop();}self.points.push(point);Ok(())
    }
}
fn measure(mut episode:Value,segments:&[Segment],bars:&[RuleCandle],end:Option<&str>,minutes:Option<&BTreeMap<String,Vec<MinuteBar>>>,allowed:Option<&BTreeSet<String>>)->AnalyticsResult<Value>{
    let entered=local(episode["entry_time"].as_str().ok_or("holding entry time missing")?)?;
    let exited=episode["exit_time"].as_str().map(local).transpose()?;
    let available:Vec<_>=bars.iter().filter(|b|end.is_none_or(|e|date(&b.timestamp)<=e)).collect();
    let stop=exited.unwrap_or_else(||available.last().and_then(|b|NaiveDate::parse_from_str(date(&b.timestamp),"%Y-%m-%d").ok()).and_then(|d|d.and_hms_opt(15,0,0)).unwrap_or(entered));
    let entry=dec(&episode["entry_price"]).ok_or("holding entry price missing")?;
    let mut observation=Observation{drawdown:Decimal::ZERO,basis:entry,low:entry,basis_time:stamp(entered),low_time:stamp(entered),loss:Decimal::ZERO,source:"entry_fill".into(),missing:BTreeSet::new(),excluded:BTreeSet::new(),resolutions:BTreeSet::new(),points:vec![]};
    for segment in segments{
        let begin=segment.start;let finish=segment.end.unwrap_or(stop);let begin_day=begin.date().to_string();let finish_day=finish.date().to_string();
        let visible:Vec<_>=bars.iter().filter(|b|begin_day.as_str()<=date(&b.timestamp)&&date(&b.timestamp)<=finish_day.as_str()).collect();
        if visible.first().is_none_or(|b|date(&b.timestamp)!=begin_day){observation.missing.insert(begin_day.clone());}
        if visible.last().is_none_or(|b|date(&b.timestamp)!=finish_day){observation.missing.insert(finish_day.clone());}
        observation.observe(segment.start_price,stamp(begin),"fill",segment)?;
        for bar in visible{
            let day=date(&bar.timestamp);if allowed.is_some_and(|set|!set.contains(day)){observation.excluded.insert(day.into());continue;}
            let day_value=NaiveDate::parse_from_str(day,"%Y-%m-%d").map_err(|e|e.to_string())?;
            let opening=day_value.and_hms_opt(9,30,0).ok_or("opening time unavailable")?;let closing=day_value.and_hms_opt(15,0,0).ok_or("closing time unavailable")?;
            let lower=begin.max(opening);let upper=finish.min(closing);
            if lower>=upper{if begin==closing&&lower==closing{observation.observe(bar.close,stamp(closing),"daily_close",segment)?;}continue;}
            if lower==opening&&upper==closing{observation.observe(bar.low,day.into(),"daily_low",segment)?;observation.resolutions.insert("daily_ohlc".into());continue;}
            if let Some(rows)=minutes.and_then(|m|m.get(day)).filter(|rows|verify_minutes(rows,bar)){
                observation.resolutions.insert("verified_5m".into());if lower.minute()%5!=0||upper.minute()%5!=0||lower.second()!=0||upper.second()!=0{observation.missing.insert(day.into());}
                for row in rows{let ended=local(&row.timestamp)?;let started=ended-Duration::minutes(5);if started>=lower&&ended<=upper{
                    let low:Decimal=row.low.to_string().parse().map_err(|_|"invalid minute low")?;observation.observe(low*bar.adjustment_factor,stamp(ended),"verified_5m_low",segment)?;}}
            }else{
                observation.missing.insert(day.into());if lower==opening{observation.observe(bar.open,stamp(opening),"daily_open",segment)?;}
                if upper==closing{observation.observe(bar.close,stamp(closing),"daily_close",segment)?;}
            }
        }
        if let Some(price)=segment.end_price{observation.observe(price,stamp(finish),"fill",segment)?;}
    }
    if let Some(exited)=exited{observation.points.push(json!({"timestamp":stamp(exited),"value":0.0,"quantity":0.0}));}
    let complete=observation.missing.is_empty();let observed_through=observation.points.last().map(|p|p["timestamp"].clone());
    super::merge(&mut episode,&json!({"asof":stamp(stop),"status":if exited.is_some(){"closed"}else{"open"},"metric_version":METRIC_VERSION,
        "price_basis":"causal_adjusted_equivalent","timezone":"Asia/Shanghai","max_drawdown":if complete{Some(float(observation.drawdown)?)}else{None},
        "observed_max_drawdown":float(observation.drawdown)?,"cost_price":float(observation.basis)?,"low_price":float(observation.low)?,"cost_time":observation.basis_time,
        "low_time":observation.low_time,"loss_amount":float(observation.loss)?,"low_source":observation.source,"coverage":if complete{"complete"}else{"incomplete"},
        "missing_sessions":observation.missing,"observation_scope":if allowed.is_some(){"qualified_sessions"}else{"full_holding_cycle"},"excluded_sessions":observation.excluded.len(),
        "observed_through":observed_through,"resolutions":observation.resolutions,"drawdown_curve":observation.points,
        "quantity":if exited.is_some(){0.0}else{float(segments.last().ok_or("holding segments missing")?.quantity)?}}));Ok(episode)
}
pub fn holding_drawdowns(grouped:&BTreeMap<String,Vec<RuleCandle>>,orders:&[Value],end:Option<&str>,minutes:Option<&MinuteInputs>,
    observed_dates:Option<&BTreeMap<String,BTreeSet<String>>>)->AnalyticsResult<Vec<Value>>{
    let mut fills=BTreeMap::<String,Vec<(NaiveDateTime,&Value)>>::new();
    for order in orders{if order["status"]=="filled"&&matches!(order["side"].as_str(),Some("BUY"|"SELL")){
        fills.entry(order["symbol"].as_str().ok_or("holding symbol missing")?.into()).or_default().push((holding_fill_time(order)?,order));}}
    let mut episodes=vec![];let empty=BTreeSet::new();
    for(symbol,mut fills)in fills{fills.sort_by_key(|(time,_)|*time);let bars=grouped.get(&symbol).map(Vec::as_slice).unwrap_or(&[]);
        let allowed=observed_dates.map(|m|m.get(&symbol).unwrap_or(&empty));let mut active:Option<Value>=None;let mut segments:Vec<Segment>=vec![];
        let(mut quantity,mut cost)=(Decimal::ZERO,Decimal::ZERO);let mut cost_time=None;let mut cycle=0;
        for(when,order)in fills{if end.is_some_and(|e|when.date().to_string().as_str()>e){continue;}
            if allowed.is_some_and(|days|!days.contains(&when.date().to_string())){return Err("filled holding order is outside observed dates".into());}
            let amount=dec(&order["quantity"]).filter(|v|*v>Decimal::ZERO).ok_or("invalid filled holding quantity")?;
            let price=dec(&order["price"]).filter(|v|*v>Decimal::ZERO).ok_or("invalid filled holding price")?;
            if active.is_some(){let segment=segments.last_mut().ok_or("active holding segment missing")?;segment.end=Some(when);segment.end_price=Some(price);}
            if order["side"]=="BUY"{
                if active.is_none(){cycle+=1;active=Some(json!({"symbol":symbol,"entry_time":stamp(when),"entry_order_time":order["timestamp"],"entry_price":price,"exit_time":null,"cycle_index":cycle}));segments.clear();}
                cost=if quantity.is_zero(){price}else{cost+(price-cost)*(amount/(quantity+amount))};cost_time=Some(when);quantity+=amount;
            }else{
                if active.is_none()||amount>quantity+Decimal::new(1,8){return Err("sell without matching holding".into());}
                quantity=(quantity-amount).max(Decimal::ZERO);
                if quantity<Decimal::new(1,8){quantity=Decimal::ZERO;let mut episode=active.take().ok_or("holding cycle missing")?;episode["exit_time"]=json!(stamp(when));
                    episodes.push(measure(episode,&segments,bars,end,minutes.and_then(|m|m.get(&symbol)),allowed)?);cost=Decimal::ZERO;}
            }
            if active.is_some(){segments.push(Segment{start:when,start_price:price,end:None,end_price:None,cost,cost_time:cost_time.ok_or("holding cost date missing")?,quantity});}
        }
        if let Some(episode)=active{episodes.push(measure(episode,&segments,bars,end,minutes.and_then(|m|m.get(&symbol)),allowed)?);}
    }Ok(episodes)
}
pub fn drawdown_metrics(episodes:&[Value])->Value{
    let closed:Vec<_>=episodes.iter().filter(|e|e["status"]=="closed").collect();let active:Vec<_>=episodes.iter().filter(|e|e["status"]=="open").collect();
    let select=|rows:&Vec<&Value>|rows.iter().min_by(|a,b|super::number(&a["observed_max_drawdown"]).unwrap_or(0.0).total_cmp(&super::number(&b["observed_max_drawdown"]).unwrap_or(0.0))
        .then(a["entry_time"].as_str().cmp(&b["entry_time"].as_str())).then(a["symbol"].as_str().cmp(&b["symbol"].as_str()))).map(|v|(**v).clone());
    let worst=select(&closed);let current=select(&active);let complete=closed.iter().all(|e|e["coverage"]=="complete");let current_complete=active.iter().all(|e|e["coverage"]=="complete");
    let scopes:BTreeSet<_>=episodes.iter().map(|e|e["observation_scope"].as_str().unwrap_or("full_holding_cycle")).collect();
    json!({"holding_drawdown_version":METRIC_VERSION,"holding_drawdown_observation_scope":if scopes.len()==1{scopes.first().copied().unwrap_or("full_holding_cycle")}else if scopes.is_empty(){"full_holding_cycle"}else{"mixed"},
        "holding_drawdown_excluded_sessions":episodes.iter().map(|e|e["excluded_sessions"].as_u64().unwrap_or(0)).sum::<u64>(),
        "holding_max_drawdown":if complete{worst.as_ref().map(|e|e["max_drawdown"].clone())}else{None},
        "holding_drawdown_status":if episodes.is_empty(){"no_entry_fills"}else if closed.is_empty(){"no_closed_cycles"}else if complete{"complete"}else{"incomplete"},"holding_drawdown_interval":worst,
        "holding_current_max_drawdown":if let Some(current)=current{if current_complete{current["max_drawdown"].clone()}else{Value::Null}}else if episodes.is_empty(){Value::Null}else{json!(0.0)},
        "holding_current_drawdown_status":if episodes.is_empty(){"no_entry_fills"}else if active.is_empty(){"flat"}else if current_complete{"complete"}else{"incomplete"}})
}
