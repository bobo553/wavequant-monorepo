//! 讲义折线和递归整波；来源 market_structure/{lecture_drawing,polyline_lifecycle,
//! trend_levels,trend_level_upgrades,trend_structure}.py。
//! 价格坐标与首次可知坐标独立保存，动态尾点不得作为下级闭合证据。
pub(crate) mod arena;
pub(crate) mod eligibility;
pub use eligibility::QualificationInterval;
mod drawing;
mod confirmation;
mod n_confirmation;
mod exit_confirmation;
mod direction_sources;
mod lifecycle_sources;
mod local_publication;
mod publication;
mod geometry;
mod structure;
mod observations;
pub mod n_memory;
mod waves;
mod landmarks;
mod qualification_projection;
use serde::{Deserialize, Serialize};
use crate::indexed_json::Value;
pub use drawing::{polyline,PolylineCursor};
pub use confirmation::{attach_confirmations, qualify_direction, DirectionCertificate};
pub use n_confirmation::{qualify_n_direction, n_confirmation_points, trend_with_context, trend_with_qualified_context, trend_with_cursor, NDirectionCertificate};
pub use exit_confirmation::{attach_failed_breakouts, attach_source_bearish_exits, attach_bearish_structure_breaks};
pub use structure::{classify_prices, retracement, six_state, polyline_last_fall_pair};
pub use waves::{trend,TrendCursor,TrendScanStatistics};
pub use publication::attach_publication as refresh_publication;
pub use direction_sources::confirmed_decline_starts;
pub use lifecycle_sources::reconcile_lifecycle_sources;
pub use local_publication::{qualify_source_direction,certify_local_publication};
pub use geometry::{geometry,permission_geometry};
pub use landmarks::{project_landmarks,project_landmarks_with_source};
pub use qualification_projection::project_qualification;
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KernelBar { pub index: usize, pub time: String, pub open: f64, pub high: f64, pub low: f64, pub close: f64 }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PointKind { H, L }
impl PointKind {
    pub fn opposite(self) -> Self { if self == Self::H { Self::L } else { Self::H } }
    pub fn sign(self) -> f64 { if self == Self::H { 1.0 } else { -1.0 } }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction { Up, Down }
impl Direction {
    pub fn target(self) -> PointKind { if self == Self::Up { PointKind::H } else { PointKind::L } }
    pub fn opposite(self) -> Self { if self == Self::Up { Self::Down } else { Self::Up } }
    pub fn sign(self) -> f64 { self.target().sign() }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
/// 四级共用六态；Option::None 表示背景尚未形成，不属于第七种趋势。
pub enum TrendState {
    #[serde(rename = "空翻多")] BearToBull,
    #[serde(rename = "空多交替")] BearBullAlternation,
    #[serde(rename = "多头")] Bull,
    #[serde(rename = "多翻空")] BullToBear,
    #[serde(rename = "多空交替")] BullBearAlternation,
    #[serde(rename = "空头")] Bear,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PointState { Seed, Developing, Confirmed, Teaching, Reversal }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KernelPointData {
    pub index: usize, pub ordinal: usize, pub time: String, pub kind: PointKind,
    pub value: f64, pub known_index: usize, pub level: u8, pub path: usize,
    pub state: PointState, #[serde(default)] pub evidence: Value,
}
/// 所有消费者保存不可变点版本的引用；字段访问只读同一条点表记录。
#[derive(Clone)]
pub struct KernelPoint {record:std::sync::Arc<PointRecord>}
struct PointRecord {id:u64,data:KernelPointData,encoded:std::sync::OnceLock<crate::indexed_json::Value>}
static POINT_VERSION:std::sync::atomic::AtomicU64=std::sync::atomic::AtomicU64::new(1);
fn point_version()->u64{POINT_VERSION.fetch_add(1,std::sync::atomic::Ordering::Relaxed)}
impl Clone for PointRecord{fn clone(&self)->Self{Self{id:point_version(),data:self.data.clone(),encoded:std::sync::OnceLock::new()}}}
impl KernelPoint{
    pub fn new(data:KernelPointData)->Self{Self{record:std::sync::Arc::new(PointRecord{id:point_version(),data,encoded:std::sync::OnceLock::new()})}}
    pub fn reference_id(&self)->u64{self.record.id}
}
impl std::ops::Deref for KernelPoint{type Target=KernelPointData;fn deref(&self)->&Self::Target{&self.record.data}}
impl std::ops::DerefMut for KernelPoint{fn deref_mut(&mut self)->&mut Self::Target{let r=std::sync::Arc::make_mut(&mut self.record);r.id=point_version();r.encoded.take();&mut r.data}}
impl std::fmt::Debug for KernelPoint{fn fmt(&self,f:&mut std::fmt::Formatter<'_>)->std::fmt::Result{f.debug_struct("PointRef").field("id",&self.reference_id()).field("index",&self.index).field("level",&self.level).finish()}}
impl PartialEq for KernelPoint{fn eq(&self,rhs:&Self)->bool{std::sync::Arc::ptr_eq(&self.record,&rhs.record)||self.record.data==rhs.record.data}}
impl Serialize for KernelPoint{fn serialize<S:serde::Serializer>(&self,s:S)->Result<S::Ok,S::Error>{
    let v=self.record.encoded.get_or_init(||crate::indexed_json::to_value(&self.record.data).expect("finite point record"));v.serialize(s)
}}
thread_local!{static POINT_INPUTS:std::cell::RefCell<std::collections::BTreeMap<u64,std::sync::Weak<PointRecord>>>=const{std::cell::RefCell::new(std::collections::BTreeMap::new())};}
impl<'de> Deserialize<'de> for KernelPoint{fn deserialize<D:serde::Deserializer<'de>>(d:D)->Result<Self,D::Error>{
    let value=crate::indexed_json::Value::deserialize(d)?;
    if let Some(record)=POINT_INPUTS.with(|cache|cache.borrow().get(&value.id()).and_then(std::sync::Weak::upgrade)){return Ok(Self{record});}
    let data=crate::indexed_json::from_value::<KernelPointData>(value.clone()).map_err(serde::de::Error::custom)?;
    let point=Self::new(data);let _=point.record.encoded.set(value.clone());
    POINT_INPUTS.with(|cache|{let mut cache=cache.borrow_mut();if cache.len()>=65_536{cache.retain(|_,v|v.strong_count()>0);}if cache.len()<1_000_000{cache.insert(value.id(),std::sync::Arc::downgrade(&point.record));}});
    Ok(point)
}}
impl KernelPoint {
    pub fn order(&self) -> (usize, usize) { (self.index, self.ordinal) }
    pub fn is_closed(&self) -> bool { matches!(self.state, PointState::Confirmed | PointState::Teaching | PointState::Reversal) }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KernelEvent {
    pub title: String, pub known_index: usize, pub point: KernelPoint,
    pub key: Option<KernelPoint>, pub origin: Option<KernelPoint>, pub evidence: Value,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KernelFrame {
    pub level: u8, pub points: Vec<KernelPoint>, pub candidate: Option<KernelPoint>,
    /// 与 points 对齐，表示以本点结束的入段六态；首端及证据不足为 None。
    #[serde(default)] pub segment_states:Vec<Option<TrendState>>,
    #[serde(default)] pub candidate_trend_state:Option<TrendState>,
    #[serde(default)] pub candidate_state_known:Option<usize>,
    /// 正式方向许可链；原始结构和下一级来源始终使用points。
    #[serde(default)] pub publication: Vec<KernelPoint>,
    pub direction: Option<Direction>, pub structure_direction: Option<Direction>, pub state: Option<TrendState>, pub key: Option<KernelPoint>, pub anchor: Option<KernelPoint>,
    pub preceding_start: Option<KernelPoint>, pub events: Vec<KernelEvent>,
    pub blocked_at: Option<usize>, pub issues: Vec<String>,
}
impl KernelFrame {
    pub fn empty(level: u8) -> Self {
        Self { level, points: Vec::new(), candidate: None, segment_states:Vec::new(),candidate_trend_state:None,candidate_state_known:None, publication: Vec::new(), direction: None, structure_direction: None, state: None,
            key: None, anchor: None, preceding_start: None, events: Vec::new(), blocked_at: None, issues: Vec::new() }
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct KernelOptions { pub ordinary_trend_completion_enabled: bool, pub secondary_reversal_enabled: bool, pub trend_offset_enabled: bool }
impl Default for KernelOptions {
    fn default() -> Self { Self { ordinary_trend_completion_enabled: false, secondary_reversal_enabled: true, trend_offset_enabled: true } }
}
pub fn validate_bars(bars: &[KernelBar]) -> Result<(), String> {
    if bars.len() > 250_000 { return Err("market prefix exceeds 250000 bars".into()); }
    for (position, bar) in bars.iter().enumerate() {
        if bar.time.is_empty() || [bar.open, bar.high, bar.low, bar.close].iter().any(|p| !p.is_finite() || *p <= 0.0)
            || bar.low > bar.open.min(bar.close) || bar.high < bar.open.max(bar.close) {
            return Err(format!("invalid OHLC at {}", bar.index));
        }
        if position > 0 && (bars[position-1].index >= bar.index || bars[position-1].time >= bar.time) {
            return Err("bars require increasing index and time".into());
        }
    }
    Ok(())
}

/// 上级严格升级的有限范围许可证；不能凭方向或交易资格制造。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PromotionReceipt {
    pub upper_level:u8, pub target_level:u8, pub path:usize, pub known_index:usize,
    pub origin:KernelPoint, pub attack:KernelPoint, pub key:KernelPoint, pub direction:Direction,
}

/// 将已发生上级严格突破导出为下级下一来源版本可消费的证明。
pub fn promotion_receipts(frame:&KernelFrame,target_level:u8)->Result<Vec<PromotionReceipt>,String> {
    if !(1..=3).contains(&target_level) || frame.level<=target_level {return Err("promotion must target a lower trend level".into());}
    let mut receipts=Vec::new();
    for event in &frame.events {
        if event.title!="strict_known_trend_key_break" {continue;}
        let parse=|name:&str|->Result<KernelPoint,String> {
            crate::indexed_json::from_value(event.evidence.get(name).cloned().ok_or("missing strict promotion reference")?)
                .map_err(|_|"invalid strict promotion reference".into())
        };
        let mut origin=parse("origin")?;let mut attack=parse("confirmed_by")?;let mut key=parse("broken_key")?;
        let direction:Direction=crate::indexed_json::from_value(event.evidence.get("direction").cloned().ok_or("missing promotion direction")?)
            .map_err(|_|"invalid promotion direction")?;
        if key.order()>=origin.order() || origin.order()>=attack.order() || key.known_index>=attack.index
            || direction.target()!=attack.kind || origin.kind==attack.kind || key.kind!=attack.kind
            || direction.sign()*(key.value-origin.value)<=0.0 || direction.sign()*(attack.value-key.value)<=0.0
            || [key.known_index,origin.known_index,attack.known_index].iter().any(|known|*known>event.known_index) {
            return Err("invalid causal strict promotion proof".into());
        }
        origin.evidence=Value::Null;attack.evidence=Value::Null;key.evidence=Value::Null;
        receipts.push(PromotionReceipt {upper_level:frame.level,target_level,path:origin.path,known_index:event.known_index,
            origin,attack,key,direction});
    }
    Ok(receipts)
}

/// 回执仅附在真实下级来源端点；编排者应把附件计入来源版本，于下一输入变更使用。
pub fn attach_promotion_receipts(source:&mut [KernelPoint],receipts:&[PromotionReceipt])->Result<(),String> {
    for receipt in receipts {
        let compatible=|point:&KernelPoint,reference:&KernelPoint|point.order()==reference.order()
            && point.kind==reference.kind && point.value==reference.value && point.time==reference.time
            && point.known_index<=receipt.known_index;
        if !source.iter().any(|point|compatible(point,&receipt.origin)) {return Err("promotion origin missing from actual lower source".into());}
        let endpoint=source.iter_mut().find(|point|compatible(point,&receipt.attack)).ok_or("promotion endpoint missing from actual lower source")?;
        let mut receipt=receipt.clone();receipt.path=endpoint.path;
        if !endpoint.evidence.is_object() {endpoint.evidence=crate::indexed_json::json!({});}
        if endpoint.evidence.get("higher_promotions").is_none() {endpoint.evidence["higher_promotions"]=crate::indexed_json::json!([]);}
        let encoded=crate::indexed_json::to_value(receipt).map_err(|_|"promotion serialization failed")?;
        let array=endpoint.evidence["higher_promotions"].as_array_mut().ok_or("promotion receipts must be an array")?;
        if !array.contains(&encoded) {array.push(encoded);}
    }
    Ok(())
}

#[cfg(feature="qualification-timing")]
pub mod qualification_profile;
