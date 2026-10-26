//! 图表只投影统一分析器已生成的结构与可知事件，不修线、不重新选择交易结构。
use std::collections::{BTreeMap,BTreeSet};
use wavequant_quant_rs::indexed_json::{Value,json};
use wavequant_quant_rs::analysis::{analyzer::AnalysisResponse,kernel::{self,KernelFrame,KernelPoint,PointState}};
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
const CHART_EVENTS:[&str;24]=["tertiary_c_candidate","tertiary_c_breakout","tertiary_c_invalidated","n_completed","strict_structure_interrupted","entry_rejected","entry_preflight_rejected","n_geometry_rejected","long_signal","exit_signal","long_transition_evidence","n_invalidated","n_target_source_retired","trading_interval_opened","trading_interval_closed","a_wave_confirmed","a_wave_upgraded","a_wave_extended","a_wave_top_confirmed","a_wave_invalidated","b_wave_updated","c_wave_started","c_wave_extended","c_wave_completed"];
fn index(value:&Value,field:&str)->Option<usize>{value.get(field).and_then(Value::as_u64).and_then(|v|usize::try_from(v).ok())}
fn date(bars:&[Value],i:usize)->Result<&str>{bars.get(i).and_then(|row|row["time"].as_str()).ok_or_else(||ApiError::invalid("chart evidence index is outside visible bars"))}
fn price(bar:&Value,field:&str)->Result<f64>{bar[field].as_f64().filter(|value|value.is_finite()).ok_or_else(||ApiError::invalid("invalid chart price"))}
fn clock(value:&mut Value,bars:&[Value])->Result<()>{
    match (value).kind_mut() {
        Some(wavequant_quant_rs::indexed_json::Kind::Object(fields))=>{for(name,value)in fields {if matches!(name.as_str(),"available_at"|"confirmed_at") {if let Some(i)=value.as_u64().and_then(|i|usize::try_from(i).ok()){*value=json!(date(bars,i)?);continue;}}clock(value,bars)?;}},
        Some(wavequant_quant_rs::indexed_json::Kind::Array(values))=>for value in values {clock(value,bars)?;},_=>{},
    }Ok(())
}
fn compact(point:&KernelPoint,bars:&[Value])->Result<Value>{Ok(json!({"time":date(bars,point.index)?,"value":point.value,"kind":point.kind,"confirmed_at":date(bars,point.known_index)?,"index":point.index,"ordinal":point.ordinal}))}
fn geometry(frame:&KernelFrame,source:Option<&KernelFrame>,bars:&[Value],enabled:bool)->Result<Value>{
    let mut value=kernel::geometry(frame);clock(&mut value,bars)?;
    let all=value["strokes"].as_array().ok_or_else(ApiError::internal)?.clone();let mut confirmed=Vec::new();let mut developing=Vec::new();
    for mut stroke in all {let rows=stroke["points"].as_array().ok_or_else(ApiError::internal)?.clone();
        let fixed=rows.iter().filter(|row|row["state"]!="developing").cloned().collect::<Vec<_>>();
        if fixed.len()!=rows.len(){developing.push(stroke.clone());}
        if !fixed.is_empty(){stroke["points"]=json!(fixed);confirmed.push(stroke);}
    }
    value["strokes"]=json!(confirmed);value["developing_strokes"]=json!(developing);
    value["confirmed_wave_count"]=json!(frame.points.len());value["n_target_trend_confirmation_enabled"]=json!(enabled);
    value["scope"]=json!("structural_trend_points_with_independent_strategy_permissions");
    if let Some(source)=source{let mut source_value=kernel::geometry(source);clock(&mut source_value,bars)?;value["source_strokes"]=source_value["strokes"].clone();}
    value["structural_points"]=wavequant_quant_rs::indexed_json::to_value(&frame.points)?;
    Ok(value)
}
fn scope(event:&Value)->String{event["n_id"].as_str().or_else(||event["target_source_id"].as_str()).map(|id|format!("n:{id}")).unwrap_or_else(||json!([event.get("attack").or_else(||event.get("bar_index")),event["window_start"],event["source_path"]]).to_string())}
fn structural_key(event:&Value,exclusive:bool)->String{let mut fields=vec![event["origin"].clone(),event["neckline"].clone(),event["pullback"].clone(),event["bar_index"].clone()];if !exclusive{fields.extend([event["qualification_window_start"].clone(),event["source_path"].clone()]);}json!(fields).to_string()}
pub fn project(response:&AnalysisResponse,bars:&[Value],audit:&[Value],engine:&str)->Result<Value>{
    if bars.is_empty()||response.state.current_index!=bars.len(){return Err(ApiError::invalid("analysis and chart prefixes differ"));}
    if wavequant_quant_rs::analysis::analyzer::STRUCTURE_ONLY{return project_polyline(response,bars,engine);}
    let cutoff=bars.len()-1;let config=&response.state.config.trading;let exclusive=config.buy_point_definition=="whole_flip_wave_v3";
    let mut chart=Vec::new();let mut counts=BTreeMap::<String,u64>::new();
    for row in audit {let Some(kind)=row["event"].as_str()else{continue;};let Some(i)=index(row,"bar_index")else{continue;};let known=index(row,"known_at").unwrap_or(i).max(i);if known>cutoff{continue;}
        *counts.entry(kind.into()).or_default()+=1;
        let kind=match kind{"n_structure_completed"=>"n_completed","n_structure_invalidated"=>"n_invalidated",kind=>kind};
        if CHART_EVENTS.contains(&kind){let mut row=row.clone();row["event"]=json!(kind);chart.push(row);}
    }
    let mut trading=BTreeMap::new();let mut source_aliases=BTreeMap::<String,BTreeSet<String>>::new();
    for(position,row)in chart.iter_mut().enumerate(){if row["event"]!="n_completed"{continue;}let key=structural_key(row,exclusive);
        if row["direction"]=="up"{if let Some(id)=row["n_id"].as_str(){source_aliases.entry(key.clone()).or_default().insert(id.into());}}
        if row["structural_target"]!=true{trading.entry(key).or_insert(position);if exclusive&&row["direction"]=="up"{row["structural_target_selected"]=json!(false);}}
    }
    let mut aliases=BTreeMap::<String,String>::new();let mut duplicates=BTreeSet::new();
    for position in 0..chart.len(){let mut row=chart[position].clone();if row["event"]!="n_completed"||row["structural_target"]!=true||(exclusive&&row["structural_target_selected"]!=true){continue;}
        let key=structural_key(&row,exclusive);if exclusive{row["structural_target_aliases"]=json!(source_aliases.get(&key).cloned().unwrap_or_default());chart[position]=row.clone();}
        if let Some(prior)=trading.get(&key).copied(){if let(Some(old),Some(new))=(chart[prior]["n_id"].as_str(),row["n_id"].as_str()){aliases.insert(new.into(),old.into());}else{continue;}
            let target=&mut chart[prior];target["structural_target"]=json!(true);target["known_at"]=json!(index(target,"known_at").or_else(||index(target,"bar_index")).unwrap_or(0).max(index(&row,"known_at").or_else(||index(&row,"bar_index")).unwrap_or(0)));
            for field in ["equal_wave","one_p","two_t","box_anchor","scope_low_index","scope_low_ordinal","real_break","virtual_break"]{if let Some(value)=row.get(field){target[field]=value.clone();}}
            if let Some(fields)=row.as_object(){for(field,value)in fields{if field.starts_with("structural_")&&field!="structural_target"{target[field]=value.clone();}}}duplicates.insert(position);
        }
    }
    let chart=chart.into_iter().enumerate().filter_map(|(i,mut row)|{if duplicates.contains(&i){return None;}if let Some(alias)=row["n_id"].as_str().and_then(|id|aliases.get(id)){row["n_id"]=json!(alias);}Some(row)}).collect::<Vec<_>>();
    let mut invalidations=BTreeMap::<String,(usize,Value)>::new();let mut retirements=BTreeMap::<String,usize>::new();let mut closures=BTreeMap::<String,usize>::new();
    for row in &chart{if row["event"]=="n_invalidated"{if let(Some(id),Some(i))=(row["n_id"].as_str(),index(row,"bar_index")){let entry=invalidations.entry(id.into()).or_insert((i,row["reason"].clone()));if i<entry.0{*entry=(i,row["reason"].clone());}}}}
    for row in audit{if row["event"]=="n_target_source_retired"{if let Some(i)=index(row,"bar_index").filter(|i|*i<=cutoff){retirements.entry(scope(row)).and_modify(|old|*old=(*old).min(i)).or_insert(i);if row["reason"]=="trading_eligibility_closed"{closures.entry(scope(row)).and_modify(|old|*old=(*old).min(i)).or_insert(i);}}}}
    let extensions=super::theory_extensions::levels(audit,bars,&retirements)?;
    let mut events=Vec::new();let mut shapes=Vec::new();
    for row in chart {let i=index(&row,"bar_index").ok_or_else(ApiError::internal)?;let known=index(&row,"known_at").unwrap_or(i).max(i);let mut event=row.clone();let mut levels=Vec::new();
        event["id"]=json!(format!("rule-{}",events.len()));event["available_at"]=json!(date(bars,known)?);event["time"]=json!(date(bars,i)?);event["price"]=json!(price(&bars[if row["structural_target"]==true{i}else{known}],"close")?);
        for(field,title)in[("key","末跌高"),("key_price","冻结末跌高"),("bottom","冻结波段低点"),("stop","信号失效参考"),("target","测幅目标（非保证）"),("wave_c_0618_target","C 浪 B+0.618×A（非保证）"),("wave_c_1618_target","C 浪扩展 1.618×A（非保证）"),("wave_c_2618_target","C 浪扩展 2.618×A（非保证）")]{if let Some(value)=row.get(field).filter(|v|!v.is_null()){levels.push(json!({"name":title,"price":value}));}}
        if row["event"]=="n_completed" {
            let up=row["direction"]=="up";let selected=!exclusive||!up||row["structural_target_selected"]==true;
            let id=row["n_id"].as_str();if let Some(id)=id{event["id"]=json!(format!("n:{id}"));}
            let invalidation=id.and_then(|id|invalidations.get(id));if let Some((i,reason))=invalidation{event["n_invalidated_at"]=json!(date(bars,*i)?);event["n_invalidation_reason"]=reason.clone();}
            let mut coords=Vec::new();for(k,field)in["origin","neckline","pullback","bar_index"].iter().enumerate(){let j=index(&row,field).ok_or_else(||ApiError::invalid("N chart event lacks an actual structural coordinate"))?;if j>i{return Err(ApiError::invalid("future N coordinate"));}coords.push(json!({"time":date(bars,j)?,"value":price(&bars[j],if (k%2==0)==up{"low"}else{"high"})?}));}
            event["shape"]=json!(coords);
            for(field,target)in[("reformed_from","reformed_from_date"),("reformed_root","reformed_root_date"),("target_decline_index","target_decline_date"),("target_bottom_index","target_bottom_date"),("target_source_attack","target_source_date")]{if let Some(j)=index(&row,field){event[target]=json!(date(bars,j)?);}}
            let mut retired=if row["target_primary"]==false{closures.get(&scope(&row)).copied()}else{retirements.get(&scope(&row)).copied()};if let Some((i,_))=invalidation{retired=Some(retired.map_or(*i,|old|old.min(*i)));}
            let valid_until=retired.and_then(|i|i.checked_sub(1)).map(|i|date(bars,i)).transpose()?;
            levels.push(json!({"name":"颈线（高低点）","price":coords[1]["value"]}));let neckline=index(&row,"neckline").ok_or_else(ApiError::internal)?;levels.push(json!({"name":"颈线（前波收盘）","price":bars[neckline]["close"]}));
            if let Some(defense)=row.get("defense"){levels.push(json!({"name":if up{"轧空低"}else{"杀多高"},"price":defense}));}
            for(field,title)in[("equal_wave","等浪投影"),("one_p","1P 投影"),("two_t","2T 投影")]{if !selected||(up&&row["target_eligible"]==false&&row["structural_target"]!=true){continue;}let Some(value)=row.get(field).filter(|v|!v.is_null())else{continue;};let mut level=json!({"name":title,"price":value});
                if up&&field!="equal_wave"{level["stage"]=json!(field);level["display_name"]=json!(if field=="one_p"{"一饱（正 N）"}else{"二吐（正 N）"});level["anchor_at"]=coords[2]["time"].clone();level["available_at"]=event["available_at"].clone();if row["structural_target"]==true{level["measurement_at"]=event["time"].clone();}if let Some(until)=valid_until{level["valid_until"]=json!(until);}}
                levels.push(level);
            }
            if selected&&row["target_primary"]!=false&&(!up||row["target_eligible"]!=false){if let Some(extra)=extensions.get(&scope(&row)){for value in extra.values(){let mut guide=value.clone();guide["name"]=json!(format!("{}（{} · {}）",if value["stage"]=="five_top"{"五顶"}else{"十满"},value["mode"].as_str().unwrap_or(""),value["status"].as_str().unwrap_or("")));if let Some(until)=valid_until{guide["valid_until"]=json!(until);}levels.push(guide);}}}
            if invalidation.is_none()&&selected{let mut shape=json!({"points":coords,"direction":row["direction"],"available_at":event["available_at"],"defense":row["defense"],"attack_time":event["time"],"n_id":row["n_id"]});if let Some(fields)=row.as_object(){for(key,value)in fields{if key.starts_with("structural_"){shape[key]=value.clone();}}}shapes.push(shape);}
        }
        event["levels"]=json!(levels);events.push(event);
    }
    let mut points=Vec::new();let mut segments=BTreeMap::<usize,Vec<Value>>::new();for point in &response.levels[0].points{if !matches!(point.state,PointState::Developing)&&point.known_index<=cutoff{let value=compact(point,bars)?;segments.entry(point.path).or_default().push(value.clone());points.push(value);}}
    let mut result=json!({"asof":date(bars,cutoff)?,"points":points,"polyline_segments":segments.into_iter().map(|(path,points)|json!({"id":format!("epoch-{path}"),"points":points})).collect::<Vec<_>>(),"events":events,"shapes":shapes.into_iter().rev().take(5).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>(),"counts":counts,"interrupted":response.levels[0].blocked_at==Some(cutoff),"event_counts":counts,"computed_from":"rust_current_engine_on_selected_prefix","engine_sha256":engine,"strategy_pivot_mode":config.pivot_mode,"trading_observation_policy":if exclusive{"qualified_windows_v1"}else{"legacy_v1"},"n_target_trend_confirmation_enabled":response.state.config.n_target_trend_confirmation_enabled,"ordinary_trend_completion_enabled":response.state.config.kernel.ordinary_trend_completion_enabled,"complete":response.complete,"unavailable_rules":response.unavailable_rules});
    let trading=response.daily_trading.as_ref().ok_or_else(||ApiError::invalid("analysis prefix lacks its daily trading observation"))?;
    result["counts"]=wavequant_quant_rs::indexed_json::to_value(&trading.counts)?;result["trading_applicable"]=json!(trading.trading_applicable);
    result["note"]=json!(if !trading.trading_applicable{"非日线周期仅展示已计算趋势结构，不执行日线买卖规则。"}else if config.pivot_mode=="lecture_causal"{"正 N 使用已确认技术折线的低高低和最高价突破，同日点保留明确顺序；结构目标与交易资格独立。"}else{"折线保留各历史结构段的已确认点；旧严格内外包未解处断开，代理版使用独立分形规则。"});
    let candles=bars.iter().map(|bar|{let decimal=|field:&str|price(bar,field)?.to_string().parse::<rust_decimal::Decimal>().map_err(|_|ApiError::invalid("chart number exceeds decimal range"));Ok(wavequant_quant_rs::analysis::trading::RuleCandle{symbol:response.state.identity.symbol.clone(),timestamp:bar["time"].as_str().ok_or_else(ApiError::internal)?.into(),open:decimal("open")?,high:decimal("high")?,low:decimal("low")?,close:decimal("close")?,volume:decimal("volume")?,adjustment_factor:decimal("factor")?,raw_is_st:None,raw_trading_active:None})}).collect::<Result<Vec<_>>>()?;
    for(level,name)in["lecture_drawing","reversal_trends","secondary_trends","tertiary_trends"].into_iter().enumerate(){
        let source=level.checked_sub(1).map(|source|&response.levels[source]);let mut projected=geometry(&response.levels[level],source,bars,response.state.config.n_target_trend_confirmation_enabled)?;
        if level>0{let landmarks=kernel::project_landmarks_with_source(&response.levels[level],&candles,audit,cutoff,source).map_err(ApiError::invalid)?;if let Some(fields)=landmarks.as_object(){for(key,value)in fields{projected[key]=value.clone();}}}
        if level>0{let qualification=kernel::project_qualification(&response.levels[level],source,audit,&candles,cutoff).map_err(ApiError::invalid)?;if let Some(fields)=qualification.as_object(){for(key,value)in fields{if key=="abc_observations"{if !exclusive&&trading.trading_applicable{result["events"].as_array_mut().ok_or_else(ApiError::internal)?.extend(value.as_array().ok_or_else(ApiError::internal)?.iter().cloned());}}else{projected[key]=value.clone();}}}}
        for(field,developing)in[("strokes",false),("developing_strokes",true)]{if let Some(strokes)=projected[field].as_array_mut(){for stroke in strokes {stroke["kind"]=json!(format!("{}{}",["lecture","reversal","secondary","tertiary"][level],if developing{"-developing"}else{""}));stroke["leg_confirmation_policy"]=json!("high_low_point_geometry_v113");if developing{stroke["display_only"]=json!(true);}}}}
        result[name]=projected;
    }
    result["structural_n_policy"]=json!(if exclusive{"level_one_successful_bottom_n_v2"}else{"confirmed_polyline_fixed_eligibility_origin_v1"});
    if exclusive&&config.pivot_mode=="lecture_causal"{result["n_target_policy"]=json!("level_one_floor_n_exclusive_v3");result["a_wave_policy"]=json!("one_p_break_confirmed_a_origin_lifetime_v1");}
    super::theory_extensions::numeric_prices(&mut result);
    Ok(result)
}

/// 仅投影最终折线；没有运行交易，就不能要求每日交易观察或补造资格记录。
fn project_polyline(response:&AnalysisResponse,bars:&[Value],engine:&str)->Result<Value>{
    let cutoff=bars.len()-1;let frame=&response.levels[0];
    let mut points=Vec::new();let mut segments=BTreeMap::<usize,Vec<Value>>::new();
    for point in &frame.points{
        if point.is_closed()&&point.known_index<=cutoff{
            let value=compact(point,bars)?;segments.entry(point.path).or_default().push(value.clone());points.push(value);
        }
    }
    let mut drawing=geometry(frame,None,bars,false)?;
    for(field,developing)in[("strokes",false),("developing_strokes",true)]{
        if let Some(strokes)=drawing[field].as_array_mut(){for stroke in strokes{
            stroke["kind"]=json!(if developing{"lecture-developing"}else{"lecture"});
            if developing{stroke["display_only"]=json!(true);}
        }}
    }
    let point_count=points.len();
    let mut result=json!({"asof":date(bars,cutoff)?,"points":points,
        "polyline_segments":segments.into_iter().map(|(path,points)|json!({"id":format!("epoch-{path}"),"points":points})).collect::<Vec<_>>(),
        "lecture_drawing":drawing,"events":[],"shapes":[],"counts":{"rows":bars.len(),"polyline_points":point_count},"event_counts":{},
        "trading_applicable":false,"calculation_mode":"third_level_only","computed_from":"rust_polyline_array",
        "engine_sha256":engine,"complete":response.complete,"unavailable_rules":response.unavailable_rules,
        "interrupted":frame.blocked_at==Some(cutoff),"strategy_pivot_mode":response.state.config.trading.pivot_mode,
        "note":"计算折线及一、二、三级趋势；交易判断保持停用。"});
    for (index,field) in ["reversal_trends","secondary_trends","tertiary_trends"].into_iter().enumerate(){
        let enabled=true;
        let mut empty=if enabled{geometry(&response.levels[index+1],Some(&response.levels[index]),bars,false)?}else{geometry(&KernelFrame::empty((index+1) as u8),None,bars,false)?};
        empty["enabled"]=json!(enabled);
        if !enabled{empty["source_strokes"]=json!([]);}
        for(field,developing)in[("strokes",false),("developing_strokes",true)]{if let Some(strokes)=empty[field].as_array_mut(){for stroke in strokes{stroke["kind"]=json!(format!("{}{}",["reversal","secondary","tertiary"][index],if developing{"-developing"}else{""}));if developing{stroke["display_only"]=json!(true);}}}}
        for name in ["post_alternation_bull_highs","bullish_turn_signals","bear_to_bull_highs","qualification_events",
            "abc_observations","key_transitions","alternation_segments"]{empty[name]=json!([]);}
        empty["key_transition_count"]=json!(0);
        result[field]=empty;
    }
    Ok(result)
}

#[cfg(test)]
mod polyline_tests{
    use super::*;
    use wavequant_quant_rs::analysis::analyzer::{AnalysisRequest,TrendAnalyzer};
    #[test]
    fn final_four_levels_project_without_a_daily_trading_observation(){
        let request:AnalysisRequest=wavequant_quant_rs::indexed_json::from_value(json!({"identity":{"symbol":"sz.002724","source_version":"fixture",
            "adjustment":"raw","timeframe":"1d"},"bars":[
            {"index":0,"time":"2020-01-02","open":10.0,"high":12.0,"low":9.0,"close":11.0,"volume":"1000"},
            {"index":1,"time":"2020-01-03","open":11.0,"high":14.0,"low":10.0,"close":13.0,"volume":"1000"}]})).unwrap();
        let mut analyzer=TrendAnalyzer::new(request).unwrap();
        while analyzer.advance_polyline_index().unwrap(){}
        analyzer.finish_polyline_array().unwrap();
        for level in 1..=3{analyzer.finish_level_array(level).unwrap();}
        analyzer.finish_qualification_arrays(None).unwrap();let response=analyzer.response();
        assert!(response.daily_trading.is_none());
        let bars=vec![json!({"time":"2020-01-02"}),json!({"time":"2020-01-03"})];
        let result=project(&response,&bars,&[],"fixture-engine").unwrap();
        assert_eq!(result["calculation_mode"],"third_level_only");assert_eq!(result["trading_applicable"],false);
        for field in ["reversal_trends","secondary_trends","tertiary_trends"]{
            assert_eq!(result[field]["enabled"],true);
            for array in ["strokes","developing_strokes","events","issues","structural_points","qualification_events","abc_observations"]{
                assert!(result[field][array].as_array().unwrap().is_empty());
            }
            assert!(result[field]["state"].is_null());
        }
    }
}
