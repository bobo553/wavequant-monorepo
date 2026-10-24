//! Python SystemStrategy 的参数保持独立；草稿采用 lecture 配置而不改变兼容默认值。
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
#[derive(Debug,Clone,PartialEq,Eq,Serialize,Deserialize)]
#[serde(default)]
pub struct TradingDayConfig{
    pub pivot_mode:String,pub pivot_width:usize,pub pattern_ttl:usize,pub structure_window:usize,pub volume_lookback:usize,
    pub minimum_rvol:Decimal,pub minimum_reward_risk:Decimal,pub max_counter_ratio:Decimal,pub shadow_fraction:Decimal,
    pub strict_n_attack_quality:bool,pub volume_filter:bool,pub regime_filter:bool,pub entry_policy:String,
    pub preflight_reward_risk:bool,pub squeeze_pullback_entries:bool,
    #[serde(deserialize_with="exact_ratio")]
    pub mature_shallow_ratio:Decimal,
    pub buy_point_definition:String,
    #[serde(deserialize_with="optional_exact_ratio")]
    pub first_pullback_threshold:Option<Decimal>,pub first_pullback_basis:String,
    pub mature_shallow_inclusive:bool,pub shallow_base_breakout_enabled:bool,pub combined_a_entry_enabled:bool,
    pub secondary_reclaim_entry_enabled:bool,pub secondary_pullback_entry_enabled:bool,
    pub n_target_trend_confirmation_enabled:bool,pub ordinary_trend_completion_enabled:bool,
    /// Python 兼容资格过滤：0 不限资格，1/2/3 选择对应趋势资格；不是结构 level=0 的折线层，也不是 K 线 timeframe。
    pub buy_interval_level:u8,
    pub ten_full_breakout_window:usize,
    #[serde(deserialize_with="exact_ratio")]
    pub ten_full_retracement_ratio:Decimal,pub ten_full_retracement_anchor:String,pub ten_full_timed_half_retracement:bool,
}
fn normalized_ratio(value:crate::indexed_json::Value)->Result<Decimal,String>{
    let value=match (value).kind() {Some(crate::indexed_json::Kind::String(v))=>v.clone(),Some(crate::indexed_json::Kind::Number(v))=>v.to_string(),_=>return Err("ratio must be decimal".into())};
    match value.as_str(){"0.3333333333333333"=>Ok(Decimal::ONE/Decimal::from(3)),"0.6666666666666666"=>Ok(Decimal::TWO/Decimal::from(3)),
        _=>value.parse::<Decimal>().map_err(|e|e.to_string())}
}
fn exact_ratio<'de,D:serde::Deserializer<'de>>(deserializer:D)->Result<Decimal,D::Error>{
    normalized_ratio(crate::indexed_json::Value::deserialize(deserializer)?).map_err(serde::de::Error::custom)
}
fn optional_exact_ratio<'de,D:serde::Deserializer<'de>>(deserializer:D)->Result<Option<Decimal>,D::Error>{
    let value=crate::indexed_json::Value::deserialize(deserializer)?;if value.is_null(){Ok(None)}else{normalized_ratio(value).map(Some).map_err(serde::de::Error::custom)}
}
impl Default for TradingDayConfig{fn default()->Self{Self{
    pivot_mode:"strict_polyline".into(),pivot_width:2,pattern_ttl:60,structure_window:120,volume_lookback:20,
    minimum_rvol:Decimal::new(12,1),minimum_reward_risk:Decimal::new(15,1),max_counter_ratio:Decimal::TWO/Decimal::from(3),shadow_fraction:Decimal::new(5,1),
    strict_n_attack_quality:true,volume_filter:true,regime_filter:true,entry_policy:"transitioned_squeeze".into(),preflight_reward_risk:true,
    squeeze_pullback_entries:true,mature_shallow_ratio:Decimal::ONE/Decimal::from(3),buy_point_definition:"legacy_v2".into(),
    first_pullback_threshold:Some(Decimal::new(5,1)),first_pullback_basis:"alternation_low".into(),mature_shallow_inclusive:true,
    shallow_base_breakout_enabled:true,combined_a_entry_enabled:false,secondary_reclaim_entry_enabled:false,secondary_pullback_entry_enabled:false,
    n_target_trend_confirmation_enabled:false,ordinary_trend_completion_enabled:false,buy_interval_level:2,ten_full_breakout_window:23,
    ten_full_retracement_ratio:Decimal::TWO/Decimal::from(3),ten_full_retracement_anchor:"origin".into(),ten_full_timed_half_retracement:true,
}}}
impl TradingDayConfig{
    pub fn lecture()->Self{Self{pivot_mode:"lecture_causal".into(),entry_policy:"hierarchical_two_buy_points".into(),buy_point_definition:"whole_flip_wave_v3".into(),..Self::default()}}
    pub fn validate(&self)->Result<(),String>{
        let two_thirds=Decimal::TWO/Decimal::from(3);let half=Decimal::new(5,1);let one_third=Decimal::ONE/Decimal::from(3);
        if !["strict_polyline","confirmed_fractal_proxy","lecture_causal"].contains(&self.pivot_mode.as_str())
            ||[self.pivot_width,self.pattern_ttl,self.structure_window,self.volume_lookback,self.ten_full_breakout_window].contains(&0)
            ||[self.minimum_rvol,self.minimum_reward_risk,self.max_counter_ratio,self.shadow_fraction,self.mature_shallow_ratio].iter().any(|v|*v<Decimal::ZERO)
            ||self.max_counter_ratio<=Decimal::ZERO||self.max_counter_ratio>=Decimal::ONE||self.shadow_fraction<=Decimal::ZERO||self.shadow_fraction>Decimal::ONE
            ||self.mature_shallow_ratio<=Decimal::ZERO||self.mature_shallow_ratio>=Decimal::ONE{return Err("invalid strategy structural windows or decimal thresholds".into());}
        if !["legacy_v2","whole_flip_wave_v3"].contains(&self.buy_point_definition.as_str())
            ||self.first_pullback_threshold.is_some_and(|v|v!=half&&v!=two_thirds)
            ||!["alternation_low","minimum_close"].contains(&self.first_pullback_basis.as_str())
            ||self.buy_interval_level>3||![half,two_thirds].contains(&self.ten_full_retracement_ratio)
            ||!["origin","b_low"].contains(&self.ten_full_retracement_anchor.as_str())
            ||self.ten_full_timed_half_retracement&&(self.ten_full_retracement_ratio!=two_thirds||self.ten_full_retracement_anchor!="origin")
            ||self.buy_point_definition=="whole_flip_wave_v3"&&(self.entry_policy!="hierarchical_two_buy_points"||![one_third,half].contains(&self.mature_shallow_ratio))
            ||!["legacy_n_continuation","transitioned_squeeze","hierarchical_two_buy_points"].contains(&self.entry_policy.as_str())
            ||self.entry_policy!="legacy_n_continuation"&&!self.regime_filter
            ||self.entry_policy=="hierarchical_two_buy_points"&&self.pivot_mode!="lecture_causal"
            ||self.squeeze_pullback_entries&&(!self.regime_filter||self.entry_policy=="legacy_n_continuation"){
            return Err("incompatible strategy entry, pullback, qualification or ten-full policy".into());
        }Ok(())
    }
}
