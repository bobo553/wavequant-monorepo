//! 共享N的只读显示投影；行情和各级趋势不在这里重新计算。
use serde::Serialize;
use rust_decimal::{Decimal,prelude::ToPrimitive};
use wavequant_quant_rs::analysis::{indexed::IndexedAnalysis,trading::Direction};
use crate::presentation::error::ApiError;
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct PointDto{bar_index:usize,ordinal:usize}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct TargetDto{known_index:usize,prices:[Option<f64>;4],reached_at:[Option<usize>;2]}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct RecordDto{direction:Direction,points:[PointDto;3],known_index:usize,attack_index:Option<usize>,squeeze_index:Option<usize>,defense:Option<f64>,observed_through:Option<usize>,invalidated_index:Option<usize>,targets:Vec<TargetDto>}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct SpanDto{n_index:usize,path:usize,origin_index:usize,start_index:usize,end_index:Option<usize>}
#[derive(Serialize)]
pub(super) struct TrendNDto{records:Vec<RecordDto>,spans:[Vec<SpanDto>;4]}
fn number(value:Option<Decimal>)->Result<Option<f64>,ApiError>{value.map(|value|value.to_f64().filter(|v|v.is_finite()&&*v>0.0).ok_or_else(||ApiError::invalid("N目标数值超出范围"))).transpose()}
pub(super) fn response(indexed:&IndexedAnalysis)->Result<TrendNDto,ApiError>{
    let mut spans:[Vec<SpanDto>;4]=std::array::from_fn(|_|Vec::new());
    let records=indexed.n_records.iter().enumerate().map(|(index,record)|{
        let n=&record.data;
        for usage in &record.usages{
            let level=spans.get_mut(usize::from(usage.level)).ok_or_else(||ApiError::invalid("N引用级别无效"))?;
            level.push(SpanDto{n_index:index,path:usage.path,origin_index:usage.origin_index,start_index:usage.start_index,end_index:usage.end_index});
        }
        let targets=record.target_history.iter().map(|snapshot|{
            let mut prices=[None;4];for (output,price) in prices.iter_mut().zip(snapshot.prices){*output=number(price)?;}
            Ok(TargetDto{known_index:snapshot.known_index,prices,reached_at:snapshot.reached_at})
        }).collect::<Result<Vec<_>,ApiError>>()?;
        Ok(RecordDto{direction:n.direction,points:[&n.a,&n.b,&n.c].map(|p|PointDto{bar_index:p.bar_index,ordinal:p.ordinal}),known_index:n.known_index,
            attack_index:n.attack_index,squeeze_index:n.squeeze_index,defense:number(n.defense)?,observed_through:record.observed_through,invalidated_index:record.invalidated_index,targets})
    }).collect::<Result<Vec<_>,ApiError>>()?;
    for level in &mut spans{level.sort_by_key(|span|(span.path,span.start_index));}
    Ok(TrendNDto{records,spans})
}
