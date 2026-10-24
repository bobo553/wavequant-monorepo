//! bull_alternation_confirmation.py 的量价及实体跳空确认通道。
use super::{RuleCandle,entries,ratio};
use super::super::kernel::{KernelPoint,PointKind};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct AlternationProof {pub low:KernelPoint,pub rule:String,pub evidence:Value}
fn dec(point:&KernelPoint)->Result<Decimal,String>{point.value.to_string().parse::<Decimal>().map_err(|e|e.to_string())}
fn shallow(origin:Decimal,high:Decimal,close:Decimal,a:usize,b:usize)->bool {
    high>origin && (high-close)*Decimal::from(3)<(high-origin)*Decimal::TWO && u128::from(b as u64)*2<u128::from(a as u64)
}
pub fn confirm_volume_breakout_alternation(bars:&[RuleCandle],now:usize,origin:&KernelPoint,high:&KernelPoint,
    pair:Option<(&KernelPoint,&KernelPoint)>,confirmed_n_squeeze:bool)->Result<Option<AlternationProof>,String> {
    if !(origin.index<high.index&&high.index<now&&now<bars.len())||origin.known_index.max(high.known_index)>now{return Ok(None);}
    let (bar,previous)=(&bars[now],&bars[now-1]);let doubled=entries::double_volume_bullish_breakout(bar,previous);
    if confirmed_n_squeeze {
        if !entries::squeeze_bullish_body(bar,Some(previous))||bar.volume<=previous.volume||entries::squeeze_buy_rejection(bar,Some(previous),None).is_some(){return Ok(None);}
    }else if !doubled&&!entries::volume_high_open_rising(bar,previous){return Ok(None);}
    let Some((key,low))=pair else{return Ok(None);};let known=key.known_index.max(low.known_index);
    let (a,h,k,l)=(dec(origin)?,dec(high)?,dec(key)?,dec(low)?);
    if known>=now||!(high.index<=key.index&&key.index<low.index&&low.index<now&&a<l&&l<h)||bar.close<=k{return Ok(None);}
    if bars[origin.index+1..=now].iter().any(|b|b.low<a)||bars[low.index+1..=now].iter().any(|b|b.low<l){return Ok(None);}
    let resistance=bars[low.index..now].iter().map(|b|b.high).max().unwrap_or(k).max(k);
    if bar.close<=resistance{return Ok(None);}
    let Some(minimum_close)=bars[high.index+1..=low.index].iter().map(|b|b.close).min()else{return Ok(None);};
    if !confirmed_n_squeeze&&shallow(a,h,minimum_close,high.index-origin.index,low.index-high.index)&&bar.close<=h{return Ok(None);}
    Ok(Some(AlternationProof{low:low.clone(),rule:"volume_bullish_polyline_last_fall_high_breakout".into(),evidence:json!({
        "confirmation_source":if doubled{"double_volume_bullish_breakout"}else{"volume_high_open_rising"},"candidate_index":now,
        "polyline_key_index":key.index,"polyline_key_price":k,"polyline_key_known_index":known,"b_low_index":low.index,"b_low_price":l,
        "b_minimum_close":minimum_close,"breakout_resistance":resistance,"confirmation_close":bar.close,"volume":bar.volume,
        "previous_volume":previous.volume,"volume_multiple":bar.volume/previous.volume})}))
}
pub fn confirm_body_gap_alternation(bars:&[RuleCandle],now:usize,origin:&KernelPoint,high:&KernelPoint,
    polyline:&[KernelPoint],pair:Option<(&KernelPoint,&KernelPoint)>)->Result<Option<AlternationProof>,String>{
    if !(origin.index<high.index&&high.index<now&&now<bars.len())||origin.known_index.max(high.known_index)>now{return Ok(None);}
    let (bar,previous)=(&bars[now],&bars[now-1]);
    if !entries::medium_bullish_body(bar)||previous.volume<=Decimal::ZERO||bar.volume<=previous.volume||bar.open<=previous.open.max(previous.close){return Ok(None);}
    let (a,h)=(dec(origin)?,dec(high)?);
    if bars[origin.index+1..=now].iter().any(|b|b.low<a){return Ok(None);}
    let Some(low_index)=(high.index+1..now).min_by_key(|&i|(bars[i].low,i))else{return Ok(None);};
    let Some(low)=polyline.iter().find(|p|p.kind==PointKind::L&&p.index==low_index&&p.known_index<=now
        &&p.value.to_string().parse::<Decimal>().ok()==Some(bars[p.index].low))else{return Ok(None);};
    let l=dec(low)?;if !(a<l&&l<h){return Ok(None);}
    let mut resistance=bars[low.index..now].iter().map(|b|b.high).max().ok_or("pullback resistance unavailable")?;
    if let Some((key,bottom))=pair{if key.known_index.max(bottom.known_index)>=now{return Ok(None);}resistance=resistance.max(dec(key)?);}
    if bar.close<=resistance{return Ok(None);}
    let minimum_close=bars[high.index+1..=low.index].iter().map(|b|b.close).min().ok_or("pullback close unavailable")?;
    if shallow(a,h,minimum_close,high.index-origin.index,low.index-high.index)&&bar.close<=h{return Ok(None);}
    Ok(Some(AlternationProof{low:low.clone(),rule:"body_gap_volume_bullish_breakout_after_defended_low".into(),evidence:json!({
        "confirmation_source":"body_gap_volume_bullish_breakout","candidate_index":now,"a_origin_index":origin.index,"a_origin_price":a,
        "a_high_index":high.index,"a_high_price":h,"b_low_index":low.index,"b_low_price":l,"b_minimum_close":minimum_close,
        "a_duration":high.index-origin.index,"b_duration":low.index-high.index,"gap_previous_body_high":previous.open.max(previous.close),
        "gap_open":bar.open,"breakout_resistance":resistance,"confirmation_close":bar.close,"volume":bar.volume,"previous_volume":previous.volume,
        "body_fraction":(bar.close-bar.open)/bar.open,"body_range_fraction":(bar.close-bar.open)/(bar.high-bar.low)})}))
}
pub fn confirm_double_n_pullback(bars:&[RuleCandle],now:usize,attack:usize,origin:&KernelPoint,high:&KernelPoint,polyline:&[KernelPoint])
    ->Result<Option<AlternationProof>,String>{
    if !(origin.index<high.index&&high.index<attack&&attack<now&&now<bars.len())||origin.known_index.max(high.known_index)>attack{return Ok(None);}
    let(n,previous,bar)=(&bars[attack],&bars[attack-1],&bars[now]);
    if previous.volume<=Decimal::ZERO||n.volume<Decimal::TWO*previous.volume||n.close<=n.open||bar.close<=n.close
        ||!entries::squeeze_bullish_body(bar,Some(&bars[now-1]))||entries::squeeze_buy_rejection(bar,Some(&bars[now-1]),Some(previous)).is_some(){return Ok(None);}
    let bottom=(high.index+1..=now).min_by_key(|&i|(bars[i].low,i)).ok_or("pullback bottom unavailable")?;
    let Some(low)=polyline.iter().find(|p|p.kind==PointKind::L&&p.index==bottom&&p.known_index<=now
        &&p.value.to_string().parse::<Decimal>().ok()==Some(bars[bottom].low))else{return Ok(None);};
    let(a,h,l)=(dec(origin)?,dec(high)?,dec(low)?);
    if low.index>=attack||h<=a||!(a<l&&l<h)||(h-l)*Decimal::from(3)>(h-a)*Decimal::TWO
        ||bars[origin.index+1..=now].iter().any(|b|b.low<a)||bars[high.index+1..attack].iter().any(|b|b.close>h){return Ok(None);}
    Ok(Some(AlternationProof{low:low.clone(),rule:"double_volume_positive_n_squeeze_whole_flip_pullback".into(),evidence:json!({
        "confirmation_source":"double_volume_positive_n_squeeze","attack":attack,"candidate_index":now,"bar_index":now,
        "a_origin_index":origin.index,"a_origin_price":a,"a_high_index":high.index,"a_high_price":h,"b_low_index":low.index,"b_low_price":l,
        "retracement_ratio":(h-l)/(h-a),"two_thirds_price":h-(h-a)*ratio(2,3),"counter_basis":"minimum_low_over_whole_flip",
        "counter_operator":"<=","attack_volume_multiple":n.volume/previous.volume,"confirmation_close":bar.close})}))
}
