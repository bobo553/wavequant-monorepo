//! 五顶十满线只引用已产生的 wave_projection 审计，保持原可知时钟。
use std::collections::BTreeMap;
use wavequant_quant_rs::indexed_json::{Value,json};
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
fn scope(event:&Value)->String{event["n_id"].as_str().or_else(||event["target_source_id"].as_str()).map(|id|format!("n:{id}")).unwrap_or_else(||json!([event.get("attack").or_else(||event.get("bar_index")),event["window_start"],event["source_path"]]).to_string())}
pub fn levels(audit:&[Value],bars:&[Value],retirements:&BTreeMap<String,usize>)->Result<BTreeMap<String,BTreeMap<String,Value>>>{
    let mut result=BTreeMap::<String,BTreeMap<String,Value>>::new();
    for row in audit {
        let Some(i)=row["bar_index"].as_u64().and_then(|i|usize::try_from(i).ok()).filter(|i|*i<bars.len())else{continue;};
        let time=bars[i]["time"].as_str().ok_or_else(ApiError::internal)?;let identity=scope(row);let kind=row["event"].as_str().unwrap_or("");
        if kind=="long_signal"&&row["wave_entry_path"]=="two_t_strong_a_resistance_rebreak"&&!row["wave_five_top_target"].is_null(){
            if retirements.get(&identity).is_some_and(|retired|*retired<=i){continue;}
            let number=|key:&str|row[key].as_f64().or_else(||row[key].as_str().and_then(|value|value.parse::<f64>().ok()));
            let span=number("wave_entry_two_t").zip(number("wave_a_origin")).map(|(top,bottom)|top-bottom);
            result.entry(identity).or_default().insert("five_top".into(),json!({"stage":"five_top","price":row["wave_five_top_target"],"status":"观察","mode":"强A再攻击","available_at":time,"projection_span":span,"a_origin":row["wave_a_origin"],"a_high":row["wave_a_high"],"b_low":row["wave_b_low"]}));continue;
        }
        if !kind.starts_with("wave_projection_"){continue;}
        let levels=result.entry(identity).or_default();if let Some(reached)=row["reached_stage"].as_str().and_then(|stage|levels.get_mut(stage)){reached["status"]=json!("已满足");}
        let Some(stage)=row["target_stage"].as_str().filter(|stage|matches!(*stage,"five_top"|"ten_full"))else{continue;};
        if !row["target"].is_null(){let at=levels.get(stage).filter(|prior|prior["price"]==row["target"]).and_then(|prior|prior["available_at"].as_str()).unwrap_or(time).to_owned();
            levels.insert(stage.into(),json!({"stage":stage,"price":row["target"],"status":"推演中","mode":if row["state"]=="stacking"{"叠箱"}else{"堆箱"},"available_at":at,"projection_span":row["projection_span"],"a_origin":row["a_origin"],"a_high":row["a_high"],"b_low":row["b_low"]}));
        }else if matches!(row["state"].as_str(),Some("pullback"|"invalidated")){if let Some(level)=levels.get_mut(stage){level["status"]=json!(if row["state"]=="pullback"{"回调暂停"}else{"已失效"});}}
    }Ok(result)
}
pub fn numeric_prices(value:&mut Value){
    match (value).kind_mut() {Some(wavequant_quant_rs::indexed_json::Kind::Array(rows))=>for row in rows{numeric_prices(row)},Some(wavequant_quant_rs::indexed_json::Kind::Object(fields))=>for(key,value)in fields{
        if matches!(key.as_str(),"price"|"value"|"open"|"high"|"low"|"close"|"volume"|"reference_price"|"key_price"|"stop"|"target"|"defense"|"equal_wave"|"one_p"|"two_t"|"box_anchor"|"a_origin"|"a_high"|"b_low"|"projection_span"|"retracement"|"rvol"|"two_thirds_price"|"half_price"){
            if let Some(number)=value.as_str().and_then(|v|v.parse::<f64>().ok()).filter(|v|v.is_finite()){*value=json!(number);}
        }numeric_prices(value);
    },_=>{}}
}
