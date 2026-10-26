//! 面向前端的精简趋势状态；内部缓存及算法状态不作为HTTP契约暴露。
use std::collections::BTreeMap;
use serde::Serialize;
use rust_decimal::prelude::ToPrimitive;
use wavequant_quant_rs::{indexed_json::{Value,json},analysis::{indexed::IndexedAnalysis,kernel::{PointKind,PointState,TrendState},trading::RuleCandle},persistence::ArtifactCache};
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
fn zero(value:&usize)->bool{*value==0}
fn no(value:&bool)->bool{!*value}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct PolylinePointDto{
    bar_index:usize,kind:PointKind,ordinal:usize,known_index:usize,confirmed:bool,
    /// 以本点结束的入段六态；背景不足为 null，不能回填将来才知道的状态。
    trend_state:Option<TrendState>,
    /// 省略时与 knownIndex 相同，发展尾部可有更晚的观察时钟。
    #[serde(skip_serializing_if="Option::is_none")] trend_state_known_index:Option<usize>,
    /// 非零路径必须保留，显示层不能把中断后的折线重新连接。
    #[serde(skip_serializing_if="zero")] path:usize,
    /// 仅供上级引用的内部端点，不属于本级绘图序列。
    #[serde(skip_serializing_if="no")] reference_only:bool,
}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct TrendPointDto{source_index:usize,known_index:usize,confirmed:bool,trend_state:Option<TrendState>,#[serde(skip_serializing_if="Option::is_none")] trend_state_known_index:Option<usize>,#[serde(skip_serializing_if="zero")] path:usize,#[serde(skip_serializing_if="no")] reference_only:bool}
#[derive(Serialize)]
struct PointDataDto{polyline:Vec<PolylinePointDto>,level1:Vec<TrendPointDto>,level2:Vec<TrendPointDto>,level3:Vec<TrendPointDto>}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct QualificationDto{
    enabled:[bool;4],active:[bool;4],
    intervals:[Vec<wavequant_quant_rs::analysis::kernel::QualificationInterval>;4],
}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct StateDto{
    version:u32,current_index:usize,qualification:QualificationDto,n:super::trend_n::TrendNDto,
    /// 坐标属于本级结果数组；不得替换为K线位置或来源层位置。
    dynamic_data_index:[usize;4],enabled:[bool;4],trend_state:[Option<TrendState>;4],data:PointDataDto,
}
/// 保留原有结果顺序与首次可知时钟；只映射索引，不运行任何趋势判定。
pub fn response(indexed:&IndexedAnalysis,bars:&[RuleCandle],source:&str,_asof:&str,data_seconds:f64,analysis_seconds:f64,total_seconds:f64)->Result<(Value,Value)>{
    if indexed.state.current_index!=bars.len(){return Err(ApiError::invalid("趋势与行情前缀不一致"));}
    let mut selected:[Vec<usize>;4]=std::array::from_fn(|level|{let l=&indexed.levels[level];let mut ids=l.points.iter().map(|p|p.0).collect::<Vec<_>>();if let Some(p)=l.candidate{if !ids.contains(&p.0){ids.push(p.0);}}ids});
    let visible=selected.clone();
    // 上层引用的辅助端点也必须存在；附加引用点不改变已有本级数组位置。
    for level in (1..4).rev(){for id in selected[level].clone(){let parent=indexed.levels[level].source_indices.get(id).and_then(|p|*p).ok_or_else(||ApiError::invalid("趋势点缺少直接来源索引"))?.0;if !selected[level-1].contains(&parent){selected[level-1].push(parent);}}}
    let positions:[BTreeMap<usize,usize>;4]=std::array::from_fn(|level|selected[level].iter().enumerate().map(|(i,p)|(*p,i)).collect());
    let l=&indexed.levels[0];
    let polyline=selected[0].iter().map(|id|{
        let bar=l.bar_indices.get(*id).ok_or_else(ApiError::internal)?.0;let known=l.known_indices.get(*id).ok_or_else(ApiError::internal)?.0;
        if bar>=bars.len()||known>=bars.len(){return Err(ApiError::invalid("趋势点超过行情边界"));}
        Ok(PolylinePointDto{bar_index:bar,kind:l.kinds[*id],ordinal:l.ordinals[*id],known_index:known,confirmed:l.states[*id]!=PointState::Developing,trend_state:l.trend_states.get(*id).copied().flatten(),trend_state_known_index:l.trend_state_known_indices.get(*id).map(|i|i.0).filter(|i|*i!=known),path:l.paths[*id],reference_only:!visible[0].contains(id)})
    }).collect::<Result<Vec<_>>>()?;
    let trend=|level:usize|->Result<Vec<TrendPointDto>>{let l=&indexed.levels[level];selected[level].iter().map(|id|{
        let source=l.source_indices.get(*id).and_then(|p|*p).ok_or_else(ApiError::internal)?;
        let known=l.known_indices[*id].0;if known>=bars.len(){return Err(ApiError::invalid("趋势确认超过行情边界"));}
        Ok(TrendPointDto{source_index:*positions[level-1].get(&source.0).ok_or_else(ApiError::internal)?,known_index:known,confirmed:l.states[*id]!=PointState::Developing,trend_state:l.trend_states.get(*id).copied().flatten(),trend_state_known_index:l.trend_state_known_indices.get(*id).map(|i|i.0).filter(|i|*i!=known),path:l.paths[*id],reference_only:!visible[level].contains(id)})
    }).collect()};
    let symbol=&indexed.state.identity.symbol;let timeframe=&indexed.state.identity.timeframe;
    let rows=bars.iter().map(|b|{
        let number=|d:rust_decimal::Decimal|d.to_f64().filter(|n|n.is_finite()).ok_or_else(||ApiError::invalid("行情数值超出范围"));
        if b.adjustment_factor<=rust_decimal::Decimal::ZERO{return Err(ApiError::invalid("复权因子必须大于零"));}
        Ok(json!({"time":b.timestamp,"open":number(b.open)?,"high":number(b.high)?,"low":number(b.low)?,"close":number(b.close)?,"volume":number(b.volume)?,"factor":number(b.adjustment_factor)?,"raw_close":number(b.close/b.adjustment_factor)?}))
    }).collect::<Result<Vec<_>>>()?;
    let data_version=super::research::digest(&wavequant_quant_rs::indexed_json::to_vec(&json!(["trend-bars-v1",symbol,timeframe,source,rows]))?);
    let snapshot=json!({"schemaVersion":1,"symbol":symbol,"timeframe":timeframe,"dataVersion":data_version,"source":source,"asof":bars.last().map(|b|b.timestamp.as_str()),"priceBasis":"causal_adjusted_equivalent","bars":rows});
    // 当前启用范围保持不变；不能用配置中的交易开关冒充结构计算开关。
    let enabled=[true;4];
    let dto=StateDto{n:super::trend_n::response(indexed)?,qualification:QualificationDto{enabled:indexed.state.config.enable_secondary_reversal,active:std::array::from_fn(|level|indexed.state.config.enable_secondary_reversal[level]&&indexed.qualification_intervals[level].iter().any(|w|w.end_index.is_none())),intervals:indexed.qualification_intervals.clone()},version:indexed.state.version,current_index:indexed.state.current_index,dynamic_data_index:indexed.state.dynamic_data_index,enabled,trend_state:std::array::from_fn(|i|indexed.levels[i].state),data:PointDataDto{polyline,level1:trend(1)?,level2:trend(2)?,level3:trend(3)?}};
    let value=json!({"schemaVersion":1,"symbol":symbol,"timeframe":timeframe,"dataVersion":data_version,"complete":indexed.complete,"unavailableRules":indexed.unavailable_rules,"state":wavequant_quant_rs::indexed_json::to_value(dto)?,"timing":{"dataRequestMs":data_seconds*1000.0,"analysisMs":analysis_seconds*1000.0,"totalMs":total_seconds*1000.0}});
    Ok((value,snapshot))
}
pub fn is_response(value:&Value)->bool{value["schemaVersion"]==1&&value["state"]["data"].is_object()&&value["dataVersion"].is_string()}
pub fn snapshot(cache:&ArtifactCache,version:&str)->Result<Value>{
    if version.len()!=64||!version.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)){return Err(ApiError::invalid("data_version必须为SHA256"));}
    let value=cache.get("trend-bars",&json!({"dataVersion":version})).filter(|v|v["dataVersion"]==version).ok_or_else(||ApiError(axum::http::StatusCode::GONE,"TREND_BARS_EXPIRED","趋势行情快照已过期，请重新计算".into()))?;Ok(value)
}
