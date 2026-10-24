//! n_trend_confirmation.py：真实来源 N 的首次完成冻结与严格一饱方向资格。
use super::{Direction,KernelPoint,PointKind};
use super::super::trading::{self,BoxAnchorMode,MilestoneBasis,NCompletion,NOptions,NSetup,PivotRef,RuleCandle};
use rust_decimal::{Decimal,prelude::ToPrimitive};
use serde::{Deserialize,Serialize};
use crate::indexed_json::{Value,json};
use std::str::FromStr;

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct NDirectionCertificate {
    pub known_index:usize, pub direction:Direction, pub origin:KernelPoint,
    pub neckline:KernelPoint,pub pullback:KernelPoint,pub completion:NCompletion,
    pub target:Decimal,pub defense:Decimal,pub confirmed_by:Value,
    pub confirmation_rule:String,
}
fn price(point:&KernelPoint)->Result<Decimal,String> {
    Decimal::from_str(&point.value.to_string()).map_err(|_|"N anchor exceeds decimal price range".into())
}
fn anchor(point:&KernelPoint,bars:&[RuleCandle],end:usize)->bool {
    if !point.is_closed() || point.index>point.known_index || point.known_index>end || point.index>=bars.len()
        || !point.value.is_finite() || point.value<=0.0 {return false;}
    let bar=&bars[point.index];
    let observed=if point.kind==PointKind::H {bar.high}else{bar.low};
    price(point).is_ok_and(|value|value==observed)
        && (point.time==bar.timestamp || point.time.get(..10)==bar.timestamp.get(..10))
}
fn setup(a:&KernelPoint,b:&KernelPoint,c:&KernelPoint,bars:&[RuleCandle],direction:Direction)->Result<Option<NSetup>,String> {
    if !(a.order()<b.order() && b.order()<c.order() && a.known_index<=b.known_index && b.known_index<=c.known_index) {return Ok(None);}
    let up=direction==Direction::Up;let sign=if up {Decimal::ONE}else{-Decimal::ONE};
    let retracement=sign*(price(c)?-price(a)?);let impulse=sign*(price(b)?-price(a)?);
    if retracement<=Decimal::ZERO || retracement>=impulse {return Ok(None);}
    let mut mother_impulse=false;let mut mother_pullback=false;let mut inside_pullback=false;
    if a.index==b.index {
        let bar=&bars[a.index];
        mother_impulse=up && a.index>0 && bar.close>bar.open && bar.high>bars[a.index-1].high && bar.low<bars[a.index-1].low;
        if !mother_impulse {return Ok(None);}
    }
    if b.index==c.index {
        let bar=&bars[b.index];
        if !up || b.index==0 || bar.close>=bar.open {return Ok(None);}
        let previous=&bars[b.index-1];
        mother_pullback=bar.high>previous.high && bar.low<previous.low;
        inside_pullback=!mother_pullback && previous.close!=previous.open && trading::teaching_inside(previous,bar);
        if !mother_pullback && !inside_pullback {return Ok(None);}
    }
    let reference=|point:&KernelPoint|PivotRef {index:point.index,confirmed_index:point.known_index,ordinal:Some(point.ordinal)};
    Ok(Some(NSetup {symbol:bars[a.index].symbol.clone(),timeframe:"1d".into(),direction:if up {trading::Direction::Up}else{trading::Direction::Down},
        origin:reference(a),neckline:reference(b),pullback:reference(c),source:"lecture_causal".into(),
        box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,allow_confirmation_bar:true,allow_outside_close:true,
        allow_mother_impulse:mother_impulse,staged_defense:true,allow_mother_pullback:mother_pullback,
        allow_inside_pullback:inside_pullback,polyline_structure:false}))
}

pub fn qualify_n_direction(source:&[KernelPoint],origin_position:usize,bars:&[RuleCandle],asof:usize,direction:Direction)
    ->Result<Option<NDirectionCertificate>,String> {
    if asof>=bars.len() {return Err("N confirmation requires an available market prefix".into());}
    trading::validate_bars(&bars[..=asof])?;
    let Some(base)=source.get(origin_position).filter(|point|anchor(point,bars,asof) && point.kind==direction.target().opposite()) else{return Ok(None);};
    let references:Vec<_>=source.iter().map(|point|anchor(point,bars,asof).then_some(point)).collect();
    let visible:Vec<_>=references.iter().flatten().copied().collect();
    if visible.windows(2).any(|pair|pair[0].order()>=pair[1].order() || pair[0].known_index>pair[1].known_index) {return Ok(None);}
    let up=direction==Direction::Up;let sign=if up {Decimal::ONE}else{-Decimal::ONE};
    let a=price(base)?;
    let failed=(base.index+1..=asof).find(|index|sign*((if up {bars[*index].low}else{bars[*index].high})-a)<Decimal::ZERO);
    let Some(mut search_end)=failed.map_or(Some(asof),|index|index.checked_sub(1)) else{return Ok(None);};
    let mut best:Option<NDirectionCertificate>=None;
    for (b_position,reference) in references.iter().enumerate().skip(origin_position+1) {
        let Some(b)=reference else{continue;};
        if b.known_index>search_end {break;}
        if b.kind!=direction.target() || sign*(price(b)?-a)<=Decimal::ZERO {continue;}
        let mut first_completed:Option<(&KernelPoint,NCompletion)>=None;
        let mut completion_end=search_end;
        for reference in references.iter().skip(b_position+1) {
            let Some(c)=reference else{continue;};
            if c.known_index>completion_end {break;}
            if c.kind==direction.target() && sign*(price(c)?-price(b)?)>=Decimal::ZERO {break;}
            if c.kind!=base.kind {continue;}
            if sign*(price(c)?-a)<=Decimal::ZERO {break;}
            let Some(formation)=setup(base,b,c,bars,direction)? else{continue;};
            // 失败的来源折叠只表示该 A/B/C 不是完整 N，禁止重锚修补。
            let observed=trading::observe_n(bars,&formation,&NOptions {timeframe:"1d".into(),milestone_basis:MilestoneBasis::Extreme,
                asof_index:Some(completion_end),measurement_start:None,measure_targets:true});
            let Ok(observed)=observed else{continue;};
            if let Some(completion)=observed.completion {
                if first_completed.is_none() || completion.bar_index<completion_end {
                    completion_end=completion.bar_index;first_completed=Some((c,completion));
                }
            }
        }
        let Some((c,completion))=first_completed else{continue;};
        let box_anchor=if up {completion.virtual_high}else{completion.virtual_low};
        let target=Decimal::TWO*box_anchor-a;
        if target<=Decimal::ZERO {continue;}
        for index in completion.bar_index..=search_end {
            let bar=&bars[index];
            if index>completion.bar_index {
                let adverse=if up {bar.low}else{bar.high};
                if sign*(adverse-a)<Decimal::ZERO || sign*(adverse-completion.defense)<Decimal::ZERO {break;}
            }
            let closing=index==completion.bar_index;
            let observed=if closing {bar.close}else if up {bar.high}else{bar.low};
            if sign*(observed-target)<=Decimal::ZERO {continue;}
            let proof=NDirectionCertificate {known_index:index,direction,origin:base.clone(),neckline:(*b).clone(),pullback:c.clone(),
                defense:completion.defense,completion:completion.clone(),target,
                confirmed_by:json!({"index":index,"ordinal":0,"time":bar.timestamp,"kind":if closing {"K"}else if up {"H"}else{"L"},
                    "value":observed.to_f64(),"available_at":index,"close":bar.close.to_f64(),
                    "market_high":bar.high.to_f64(),"market_low":bar.low.to_f64(),
                    "target_basis":if closing {"completion_close"}else{"market_extreme"}}),
                confirmation_rule:"source_n_strict_one_p_target".into()};
            if best.as_ref().is_none_or(|old|index<old.known_index) {best=Some(proof);}search_end=index;break;
        }
    }
    Ok(best)
}


/// n_trend_reversals.py：N 达标原点与随后真实原点失守的整波结束。
/// 调用者将返回点与本级原节点按首次已知日合并，不能改写更早的普通证书。
pub fn n_confirmation_points(certificate:&NDirectionCertificate,source:&[KernelPoint],bars:&[RuleCandle],asof:usize,level:u8)
    ->Result<Vec<KernelPoint>,String> {
    if !(1..=3).contains(&level) || certificate.origin.level+1!=level || asof>=bars.len() {return Err("N promotion needs the directly lower level and a market prefix".into());}
    let mut origin=certificate.origin.clone();origin.level=level;origin.state=super::PointState::Reversal;
    origin.known_index=certificate.known_index;
    origin.evidence=json!({"confirmation_rule":"source_n_strict_one_p_target","n_target_confirmation":certificate,
        "source":certificate.origin,"wave_direction_after":certificate.direction,"price_body":certificate.origin.evidence.get("price_body")});
    let mut result=vec![origin];
    let up=certificate.direction==Direction::Up;let sign=if up {Decimal::ONE}else{-Decimal::ONE};
    let a=price(&certificate.origin)?;
    let failure=(certificate.known_index+1..=asof).find(|index|sign*((if up {bars[*index].low}else{bars[*index].high})-a)<Decimal::ZERO);
    if let Some(failure)=failure {
        let peak=(certificate.origin.index+1..failure).max_by(|left,right| {
            let a=if up {bars[*left].high}else{-bars[*left].low};
            let b=if up {bars[*right].high}else{-bars[*right].low};
            a.cmp(&b).then_with(||right.cmp(left))
        });
        if let Some(index)=peak {
            let endpoint=source.iter().find(|point|anchor(point,bars,asof) && point.index==index
                && point.kind==certificate.direction.target() && point.order()>certificate.origin.order());
            if let Some(endpoint)=endpoint {
                let mut point=endpoint.clone();point.level=level;point.state=super::PointState::Reversal;
                point.known_index=point.known_index.max(failure);
                point.evidence=json!({"confirmation_rule":"n_origin_breach_confirms_wave_endpoint","source":endpoint,
                    "broken_key":certificate.origin,"direction_before":certificate.direction,"direction_after":certificate.direction.opposite(),
                    "wave_direction_after":certificate.direction.opposite(),
                    "confirmed_by":{"index":failure,"time":bars[failure].timestamp,"available_at":failure,
                        "kind":if up {"L"}else{"H"},"value":if up {bars[failure].low.to_f64()}else{bars[failure].high.to_f64()}},
                    "price_body":endpoint.evidence.get("price_body")});
                result.push(point);
            }
        }
    }
    Ok(result)
}


/// 兼容直接调用；高层调度应提供下级实际许可链作为N证书掩码。
pub fn trend_with_context(source:&[KernelPoint],level:u8,asof:usize,options:&super::KernelOptions,
    bars:&[RuleCandle],n_target_enabled:bool)->Result<super::KernelFrame,String>{
    trend_with_qualified_context(source,None,level,asof,options,bars,n_target_enabled)
}
/// 统一结构只计算一次。普通与N方向许可证随后在独立publication链生产，不能改写结构。
pub fn trend_with_qualified_context(source:&[KernelPoint],qualified_source:Option<&[KernelPoint]>,level:u8,asof:usize,options:&super::KernelOptions,
    bars:&[RuleCandle],n_target_enabled:bool)->Result<super::KernelFrame,String>{
    trend_with_cursor(&mut super::TrendCursor::default(),source,qualified_source,level,asof,options,bars,n_target_enabled)
}

pub fn trend_with_cursor(cursor:&mut super::TrendCursor,source:&[KernelPoint],qualified_source:Option<&[KernelPoint]>,level:u8,asof:usize,options:&super::KernelOptions,
    bars:&[RuleCandle],n_target_enabled:bool)->Result<super::KernelFrame,String>{
    let mut frame=cursor.advance(source,level,asof,options)?;
    super::publication::attach_publication(&mut frame,source,qualified_source,bars,asof,n_target_enabled)?;
    Ok(frame)
}
