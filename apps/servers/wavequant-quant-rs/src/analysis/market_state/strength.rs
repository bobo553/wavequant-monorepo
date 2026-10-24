use crate::analysis::trading::{Direction,RuleCandle,validate_bars};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
use std::cmp::Ordering;
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
pub enum StrengthScale {#[serde(rename="exact_sixths")] ExactFractions,
    #[serde(rename="printed_16.7_33.3_50_66.7_83.3")] PrintedPercentages}
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum PivotKind { High,Low }
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ConfirmedPivot {pub index:usize,pub confirmed_index:usize,pub price:Decimal,pub kind:PivotKind}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct StrengthLevel {pub fraction:String,pub price:Decimal}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WaveStrength {pub impulse_direction:Direction,pub scale:StrengthScale,pub impulse:Decimal,pub countermove:Decimal,
    pub ratio:Decimal,pub exact_ratio:String,pub counter_strength:String,pub counter_name:String,pub original_trend_reading:String,
    pub sixth_band:Option<usize>,pub sixth_boundary:Option<String>,pub extent:String,pub height_from_wave_low:Decimal,
    pub levels:Vec<StrengthLevel>,pub rule:String}
pub fn compare_ratio(n:Decimal,d:Decimal,numerator:i64,denominator:i64)->Result<Ordering,String> {
    let left=n.checked_mul(Decimal::from(denominator)).ok_or("ratio comparison overflow")?;
    let right=d.checked_mul(Decimal::from(numerator)).ok_or("ratio comparison overflow")?;
    Ok(left.cmp(&right))
}
fn exact_fraction(n:Decimal,d:Decimal)->Result<String,String> {
    fn gcd(mut a:i128,mut b:i128)->i128 {while b!=0{let r=a%b;a=b;b=r;}a.abs()}
    let(n,d)=(n.normalize(),d.normalize());let(mut a,mut b)=(n.mantissa(),d.mantissa());
    let divisor=gcd(a,b);a/=divisor;b/=divisor;
    if n.scale()>d.scale(){let mut factor=10_i128.pow(n.scale()-d.scale());let divisor=gcd(a,factor);a/=divisor;factor/=divisor;b=b.checked_mul(factor).ok_or("exact ratio exceeds integer representation")?;}
    else{let mut factor=10_i128.pow(d.scale()-n.scale());let divisor=gcd(b,factor);b/=divisor;factor/=divisor;a=a.checked_mul(factor).ok_or("exact ratio exceeds integer representation")?;}
    Ok(if b==1 {a.to_string()}else{format!("{a}/{b}")})
}
pub fn measure_strength(origin:Decimal,extreme:Decimal,counter:Decimal,direction:Direction,scale:StrengthScale)->Result<WaveStrength,String> {
    if [origin,extreme,counter].iter().any(|p|*p<=Decimal::ZERO){return Err("positive prices required".into());}
    let sign=direction.sign();let impulse=sign*(extreme-origin);let movement=sign*(extreme-counter);
    if impulse<=Decimal::ZERO||movement<Decimal::ZERO{return Err("positive impulse and nonnegative countermove required".into());}
    let ticks=if scale==StrengthScale::ExactFractions {[(1,6),(1,3),(1,2),(2,3),(5,6)]}
        else{[(167,1000),(333,1000),(1,2),(667,1000),(833,1000)]};
    let thirds=compare_ratio(movement,impulse,ticks[1].0,ticks[1].1)?;
    let half=compare_ratio(movement,impulse,1,2)?;
    let two_thirds=compare_ratio(movement,impulse,ticks[3].0,ticks[3].1)?;
    let up=direction==Direction::Up;
    let grade=if movement==Decimal::ZERO{"no_countermove"}
        else if up {if thirds.is_lt(){"weak_countermove"}else if !half.is_lt()&&two_thirds.is_lt(){"medium_countermove"}
            else if !two_thirds.is_lt(){"strong_countermove"}else if thirds.is_eq(){"lecture_boundary_unresolved"}else{"lecture_interval_not_specified"}}
        else if two_thirds.is_gt(){"strong_countermove"}else if half.is_gt()&&two_thirds.is_lt(){"medium_countermove"}
        else if thirds.is_gt()&&half.is_lt(){"weak_countermove"}else if thirds.is_eq()||half.is_eq()||two_thirds.is_eq(){"lecture_boundary_unresolved"}
        else{"lecture_interval_not_specified"};
    let mut boundary=None;let mut above=0;let mut levels=Vec::new();
    for(n,d)in ticks {let cmp=compare_ratio(movement,impulse,n,d)?;let fraction=format!("{n}/{d}");
        if cmp.is_eq(){boundary=Some(fraction.clone());}if cmp.is_gt(){above+=1;}
        levels.push(StrengthLevel{fraction,price:extreme-sign*impulse*Decimal::from(n)/Decimal::from(d)});
    }
    let ratio=movement/impulse;
    let band=if boundary.is_some()||movement>=impulse{None}else if movement==Decimal::ZERO{Some(0)}else{Some(above+1)};
    let reading=match grade {"weak_countermove"=>"original_direction_relatively_resilient","medium_countermove"=>"intermediate_counter_pressure",
        "strong_countermove"=>"original_direction_under_strong_counter_pressure",_=>"not_assigned"};
    Ok(WaveStrength{impulse_direction:direction,scale,impulse,countermove:movement,ratio,exact_ratio:exact_fraction(movement,impulse)?,
        counter_strength:grade.into(),counter_name:if up{"回档"}else{"反弹"}.into(),original_trend_reading:reading.into(),
        sixth_band:band,sixth_boundary:boundary,extent:if movement==Decimal::ZERO{"none"}else if movement<impulse{"partial"}else if movement==impulse{"full_retracement"}else{"beyond_origin"}.into(),
        height_from_wave_low:if up{Decimal::ONE-ratio}else{ratio},levels,rule:"literal_gaps_pullback_half_inclusive_engineering_v1".into()})
}
pub fn validate_prefix(bars:&[RuleCandle],symbol:&str,end:usize)->Result<(),String> {
    if end>=bars.len(){return Err("available asof required".into());}validate_bars(&bars[..=end])?;
    if bars[end].symbol!=symbol{return Err("symbol mismatch".into());}Ok(())
}
pub fn validate_pivots(bars:&[RuleCandle],points:&[&ConfirmedPivot],end:usize)->Result<(),String> {
    for point in points {
        if point.confirmed_index<point.index||point.price<=Decimal::ZERO{return Err("invalid confirmed pivot".into());}
        if point.confirmed_index<=end {
            let bar=bars.get(point.index).ok_or("pivot source unavailable")?;
            if point.price!=if point.kind==PivotKind::High{bar.high}else{bar.low}{return Err("pivot differs from source OHLC / adjustment basis".into());}
        }
    }Ok(())
}
pub fn observe_wave_strength(bars:&[RuleCandle],points:[&ConfirmedPivot;3],symbol:&str,scale:StrengthScale,asof:usize)->Result<Option<WaveStrength>,String> {
    validate_prefix(bars,symbol,asof)?;validate_pivots(bars,&points,asof)?;
    if points.iter().any(|p|p.confirmed_index>asof){return Ok(None);}
    let[a,b,c]=points;
    if !(a.index<b.index&&b.index<c.index&&a.confirmed_index<=b.confirmed_index&&b.confirmed_index<=c.confirmed_index&&a.kind==c.kind&&a.kind!=b.kind){return Err("ordered alternating distinct-bar pivots required".into());}
    let direction=if a.kind==PivotKind::Low{Direction::Up}else{Direction::Down};let sign=direction.sign();
    if bars[a.index..=b.index].iter().any(|bar|sign*(direction.adverse(bar)-a.price)<Decimal::ZERO)
        ||bars[a.index..=c.index].iter().any(|bar|sign*(direction.extreme(bar)-b.price)>Decimal::ZERO)
        ||bars[b.index+1..=c.index].iter().any(|bar|sign*(direction.adverse(bar)-c.price)<Decimal::ZERO){return Err("supplied pivots are not extrema of stated wave legs".into());}
    Ok(Some(measure_strength(a.price,b.price,c.price,direction,scale)?))
}
