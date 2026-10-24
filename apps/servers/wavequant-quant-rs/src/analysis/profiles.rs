//! 版本化策略配置共用入口。讲义配置只继承封存场景的执行假设，不继承收益或验证结论。
use std::collections::BTreeMap;
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use super::trading::config::TradingDayConfig;
type Result<T>=std::result::Result<T,String>;
pub const VARIANTS:[&str;10]=["strict_full","proxy_full","lecture_v1","lecture_v2","lecture_v3","lecture_v3_c50","lecture_v3_d67_c33","lecture_v3_d50_c50","lecture_v3_d67_c50","lecture_v3_close_d50_c50"];
#[derive(Debug,Clone,Copy,Serialize,Deserialize)]
#[serde(default)]
pub struct ProfileOptions {pub n_target_trend_confirmation_enabled:bool,pub ordinary_trend_completion_enabled:bool,pub buy_interval_level:u8}
impl Default for ProfileOptions {fn default()->Self{Self{n_target_trend_confirmation_enabled:false,ordinary_trend_completion_enabled:false,buy_interval_level:2}}}
#[derive(Debug,Clone,Serialize)]
pub struct StrategyProfile {
    #[serde(skip)]pub strategy:TradingDayConfig,
    #[serde(rename="strategy")]pub parameters:Value,
    pub scenarios:BTreeMap<String,Value>,pub profile_version:String,pub definition:Value,
    #[serde(flatten)]pub extra:BTreeMap<String,Value>,
}
#[derive(Debug,Clone,Copy)]
struct Thresholds {first:Option<Decimal>,second:Decimal,minimum_close:bool,inclusive:bool}
fn thresholds(variant:&str)->Result<Thresholds> {
    let half=Decimal::new(5,1);let third=Decimal::ONE/Decimal::from(3);let two_thirds=Decimal::TWO/Decimal::from(3);
    let(first,second,minimum_close,inclusive)=match variant {
        "lecture_v3"=>(None,third,false,true),"lecture_v3_c50"=>(None,half,false,false),
        "lecture_v3_d67_c33"=>(Some(two_thirds),third,false,true),"lecture_v3_d50_c50"=>(Some(half),half,false,true),
        "lecture_v3_d67_c50"=>(Some(two_thirds),half,false,true),"lecture_v3_close_d50_c50"=>(Some(half),half,true,false),
        _=>return Err("unknown whole-wave profile".into()),
    };Ok(Thresholds{first,second,minimum_close,inclusive})
}
fn merge(target:&mut Value,patch:Value)->Result<()> {
    let target=target.as_object_mut().ok_or("profile definition must be an object")?;
    let patch=patch.as_object().ok_or("profile definition must be an object")?;
    for(key,value)in patch{target.insert(key.clone(),value.clone());}Ok(())
}
fn definitions(source:&str)->Result<Value>{crate::indexed_json::from_str(source).map_err(|error|format!("invalid compiled profile definition: {error}"))}
fn scenario_execution(legacy:&Value)->Result<BTreeMap<String,Value>> {
    let scenarios=legacy.get("scenarios").and_then(Value::as_object).filter(|v|!v.is_empty()).ok_or("frozen execution scenarios are required")?;
    scenarios.iter().map(|(name,value)|{
        let execution=value.get("execution").filter(|v|v.is_object()).ok_or("frozen execution configuration is required")?;
        Ok((name.clone(),json!({"execution":execution})))
    }).collect()
}
fn research_profile(legacy:&Value)->Result<StrategyProfile> {
    let strategy=TradingDayConfig{pivot_mode:"lecture_causal".into(),..TradingDayConfig::default()};
    let mut parameters=crate::indexed_json::to_value(&strategy).map_err(|error|error.to_string())?;
    for name in ["mature_shallow_ratio","buy_point_definition","first_pullback_threshold","first_pullback_basis","mature_shallow_inclusive","secondary_reclaim_entry_enabled"]{parameters.as_object_mut().ok_or("strategy must be an object")?.remove(name);}
    Ok(StrategyProfile{strategy,parameters,scenarios:scenario_execution(legacy)?,profile_version:"lecture_causal_squeeze_v1".into(),definition:definitions(include_str!("profile_v1_definition.json"))?,extra:BTreeMap::new()})
}
fn hierarchical_profile(legacy:&Value)->Result<StrategyProfile> {
    let mut profile=research_profile(legacy)?;
    profile.strategy=TradingDayConfig{pivot_mode:"lecture_causal".into(),entry_policy:"hierarchical_two_buy_points".into(),..TradingDayConfig::default()};
    profile.parameters=crate::indexed_json::to_value(&profile.strategy).map_err(|error|error.to_string())?;
    profile.profile_version="lecture_hierarchical_two_buy_points_v2".into();
    merge(&mut profile.definition,definitions(include_str!("profile_v2_definition.json"))?)?;Ok(profile)
}
fn whole_wave_profile(legacy:&Value,variant:&str)->Result<StrategyProfile> {
    let thresholds=thresholds(variant)?;let mut profile=hierarchical_profile(legacy)?;
    for scenario in profile.scenarios.values_mut(){merge(&mut scenario["execution"],json!({
        "entry_at_close":true,"allow_add_on":true,"nonflat_limit_close_fill":true,"consolidation_entry_intraday":true,
        "staged_exit_enabled":true,"staged_exit_same_day":false,"staged_exit_intraday":true,"missing_minute_daily_fallback":true,
        "inverse_n_after_reduction":true,"inverse_n_close_reduce":true,"initial_reduction_fraction":0.65,
        "exit_on_target":false,"wave_exhaustion_exit":true,"pressure_adverse_exit":true,"trend_flip_adverse_exit":true,
        "volume_inverse_n_clear":true,"volume_down_exit":true,"small_n_reduction":true
    }))?;}
    let strategy=&mut profile.strategy;
    strategy.buy_point_definition="whole_flip_wave_v3".into();strategy.preflight_reward_risk=false;strategy.strict_n_attack_quality=false;
    strategy.first_pullback_threshold=thresholds.first;strategy.mature_shallow_ratio=thresholds.second;
    strategy.first_pullback_basis=if thresholds.minimum_close{"minimum_close"}else{"alternation_low"}.into();strategy.mature_shallow_inclusive=thresholds.inclusive;
    strategy.combined_a_entry_enabled=true;strategy.secondary_reclaim_entry_enabled=true;strategy.secondary_pullback_entry_enabled=true;
    strategy.ten_full_breakout_window=23;strategy.ten_full_retracement_ratio=Decimal::TWO/Decimal::from(3);strategy.ten_full_retracement_anchor="origin".into();strategy.ten_full_timed_half_retracement=true;strategy.minimum_rvol=Decimal::ONE;
    profile.parameters=crate::indexed_json::to_value(&profile.strategy).map_err(|error|error.to_string())?;
    profile.profile_version=format!("configurable_n_target_trend_confirmation_v163_double_volume_squeeze_45_{variant}");
    merge(&mut profile.definition,definitions(include_str!("profile_v3_definition.json"))?)?;
    profile.definition["first_buy"]=json!(if thresholds.first.is_none(){"level_2_or_3_confirmed_alternation_then_new_positive_n_then_squeeze"}else if thresholds.minimum_close{"(H0-min_close_H0_to_A)/(H0-L0)>deep_then_positive_n_then_squeeze"}else{"(H0-A)/(H0-L0)>deep_then_positive_n_then_squeeze"});
    profile.definition["second_buy"]=json!(if thresholds.inclusive{"maturity_then_H1_then_pullback_(H1-min_close)/(H1-L0)<=shallow_then_positive_n_then_squeeze"}else{"maturity_then_H1_then_pullback_(H1-min_close)/(H1-L0)<shallow_then_positive_n_then_squeeze"});
    profile.definition["first_threshold"]=json!(thresholds.first);profile.definition["second_threshold"]=json!(thresholds.second);
    if thresholds.first.is_none(){
        profile.definition["first_buy_trend_levels"]=json!([2,3]);
        profile.definition["primary_filters"]=json!(["first_buy_level_2_or_3_alternation","squeeze_regime","type2_whole_wave_ratio","rvol_1_2","gross_rr_1_5","next_open_net_rr_1_5"]);
    }
    let channels=profile.definition["channels"].as_array_mut().ok_or("profile channels missing")?;
    for channel in ["multilevel_breakout_squeeze","wave_push_gap","shallow_base_breakout","nested_alternation_breakout","combined_a_pullback_breakout","secondary_resistance_reclaim","secondary_deep_pullback_reclaim"]{channels.push(json!(channel));}
    let exits=profile.definition["exits"].as_array_mut().ok_or("profile exits missing")?;exits.retain(|rule|rule!="target_observed_then_next_open");
    exits.extend([json!("five_top_gap_upper_shadow_completed_5m_or_daily_close_clear"),json!("five_top_body_upper_shadow_completed_5m_or_daily_close_clear")]);
    let filters=profile.definition["primary_filters"].as_array_mut().ok_or("profile filters missing")?;filters.retain(|name|name!="gross_rr_1_5");
    for name in filters.iter_mut(){*name=match name.as_str(){Some("next_open_net_rr_1_5")=>json!("execution_price_net_rr_1_5"),Some("squeeze_regime")=>json!("squeeze_or_nested_alternation_breakout"),Some("rvol_1_2")=>json!("volume_gt_previous"),_=>name.clone()};}
    filters.extend([json!("alternation_before_n_or_same_n_squeeze_confirmation"),json!("known_pending_five_top_remaining_space_gt_one_original_n_box"),json!("ten_full_then_23_sessions_new_high_or_latest_b_retracement"),json!("new_daily_low_and_inverse_neckline_wick_break_requires_observation")]);
    Ok(profile)
}
/// legacy 是封存 research/report.json 的 variants；任何选项均进入序列化身份。
pub fn strategy_profile(legacy:&Value,variant:&str,options:ProfileOptions)->Result<StrategyProfile> {
    if !VARIANTS.contains(&variant){return Err("unknown strategy".into());}
    if !(1..=3).contains(&options.buy_interval_level){return Err("buy interval level must be 1, 2 or 3".into());}
    let variants=legacy.as_object().ok_or("sealed variants must be an object")?;
    let mut profile=if matches!(variant,"strict_full"|"proxy_full") {
        let mut raw=variants.get(variant).and_then(Value::as_object).cloned().ok_or("selected sealed profile is missing")?;
        let parameters=raw.remove("strategy").filter(Value::is_object).ok_or("sealed strategy parameters are missing")?;
        let strategy:TradingDayConfig=crate::indexed_json::from_value(parameters.clone()).map_err(|error|format!("invalid sealed strategy configuration: {error}"))?;
        let scenarios=crate::indexed_json::from_value(raw.remove("scenarios").ok_or("sealed execution scenarios are missing")?).map_err(|error|format!("invalid sealed scenarios: {error}"))?;
        let profile_version=raw.remove("profile_version").map(|v|v.as_str().map(str::to_owned).ok_or("invalid sealed profile version")).transpose()?.unwrap_or_else(||variant.into());
        let definition=raw.remove("definition").unwrap_or_else(||json!({}));
        StrategyProfile{strategy,parameters,scenarios,profile_version,definition,extra:raw.into_iter().collect()}
    }else{
        let strict=variants.get("strict_full").ok_or("strict execution scenarios are missing")?;
        match variant{"lecture_v1"=>research_profile(strict)?,"lecture_v2"=>hierarchical_profile(strict)?,_=>whole_wave_profile(strict,variant)?}
    };
    if !profile.definition.is_object(){return Err("profile definition must be an object".into());}
    profile.strategy.n_target_trend_confirmation_enabled=options.n_target_trend_confirmation_enabled;
    profile.strategy.ordinary_trend_completion_enabled=options.ordinary_trend_completion_enabled;
    profile.strategy.buy_interval_level=options.buy_interval_level;
    for (key,value) in [("n_target_trend_confirmation_enabled",json!(options.n_target_trend_confirmation_enabled)),("ordinary_trend_completion_enabled",json!(options.ordinary_trend_completion_enabled)),("buy_interval_level",json!(options.buy_interval_level))]{profile.parameters[key]=value.clone();profile.definition[key]=value;}
    let mut routes="same_level_key_break_or_lower_key_break_alternation_turn".to_owned();if options.n_target_trend_confirmation_enabled{routes.push_str("_or_source_n_strict_one_p_target");}
    profile.definition["trend_confirmation_routes"]=json!(routes);profile.definition["buy_interval_policy"]=json!("confirmed_alternation_until_bear_flip_or_context_invalidated");
    profile.strategy.validate()?;Ok(profile)
}


/// 原 Python StrategyConfig 的完整执行/基线参数；不丢弃尚由调度器消费的字段。
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(default,deny_unknown_fields)]
pub struct ExecutionParameters {
    pub breakout_lookback:usize,
    pub impulse_lookback:usize,
    pub atr_period:usize,
    pub rvol_lookback:usize,
    pub regime_lookback:usize,
    pub confirm_window:usize,
    pub min_confirm_bars:usize,
    pub breakout_buffer_bps:Decimal,
    pub min_rvol:Decimal,
    pub min_close_location:Decimal,
    pub max_retracement:Decimal,
    pub stop_atr_buffer:Decimal,
    pub regime_min_efficiency:Decimal,
    pub regime_strong_move_atr:Decimal,
    pub require_non_bearish_regime:bool,
    pub max_hold_bars:usize,
    pub take_profit_r:Decimal,
    pub exit_on_target:bool,
    pub commission_bps_per_side:Decimal,
    pub slippage_bps_per_side:Decimal,
    pub allow_same_day_exit:bool,
    pub initial_capital:Decimal,
    pub max_positions:usize,
    pub max_position_weight:Decimal,
    pub risk_fraction:Decimal,
    pub lot_size:usize,
    pub minimum_entry_shares:usize,
    pub liquidity_lookback:usize,
    pub max_participation:Decimal,
    pub entry_at_close:bool,
    pub allow_add_on:bool,
    pub nonflat_limit_close_fill:bool,
    pub consolidation_entry_intraday:bool,
    pub entry_ttl_bars:usize,
    pub max_entry_gap:Decimal,
    pub minimum_commission:Decimal,
    pub a_share_taxes:bool,
    pub net_reward_risk_filter:bool,
    pub staged_exit_enabled:bool,
    pub staged_exit_same_day:bool,
    pub staged_exit_intraday:bool,
    pub missing_minute_daily_fallback:bool,
    pub inverse_n_after_reduction:bool,
    pub inverse_n_close_reduce:bool,
    pub wave_exhaustion_exit:bool,
    pub wave_exhaustion_reduction:Decimal,
    pub wave_exhaustion_min_range:Decimal,
    pub wave_exhaustion_min_shadow:Decimal,
    pub wave_engulf_min_body:Decimal,
    pub pressure_adverse_exit:bool,
    pub trend_flip_adverse_exit:bool,
    pub pressure_lookback:usize,
    pub pressure_body_min_fraction:Decimal,
    pub pressure_volume_ratio:Decimal,
    pub volume_inverse_n_clear:bool,
    pub volume_down_exit:bool,
    pub small_n_reduction:bool,
    pub small_body_max_fraction:Decimal,
    pub small_body_lookback:usize,
    pub initial_reduction_fraction:Decimal,
}
impl Default for ExecutionParameters {fn default()->Self{Self{
    breakout_lookback:20,
    impulse_lookback:20,
    atr_period:14,
    rvol_lookback:20,
    regime_lookback:20,
    confirm_window:5,
    min_confirm_bars:1,
    breakout_buffer_bps:Decimal::new(0,1),
    min_rvol:Decimal::new(12,1),
    min_close_location:Decimal::new(65,2),
    max_retracement:Decimal::new(5,1),
    stop_atr_buffer:Decimal::new(25,2),
    regime_min_efficiency:Decimal::new(25,2),
    regime_strong_move_atr:Decimal::new(25,1),
    require_non_bearish_regime:true,
    max_hold_bars:20,
    take_profit_r:Decimal::new(20,1),
    exit_on_target:true,
    commission_bps_per_side:Decimal::new(30,1),
    slippage_bps_per_side:Decimal::new(50,1),
    allow_same_day_exit:false,
    initial_capital:Decimal::new(10000000,1),
    max_positions:5,
    max_position_weight:Decimal::new(2,1),
    risk_fraction:Decimal::new(1,2),
    lot_size:100,
    minimum_entry_shares:1,
    liquidity_lookback:20,
    max_participation:Decimal::new(1,2),
    entry_at_close:false,
    allow_add_on:false,
    nonflat_limit_close_fill:false,
    consolidation_entry_intraday:false,
    entry_ttl_bars:1,
    max_entry_gap:Decimal::new(5,2),
    minimum_commission:Decimal::new(50,1),
    a_share_taxes:true,
    net_reward_risk_filter:true,
    staged_exit_enabled:false,
    staged_exit_same_day:false,
    staged_exit_intraday:false,
    missing_minute_daily_fallback:false,
    inverse_n_after_reduction:false,
    inverse_n_close_reduce:false,
    wave_exhaustion_exit:false,
    wave_exhaustion_reduction:Decimal::new(8,1),
    wave_exhaustion_min_range:Decimal::new(8,2),
    wave_exhaustion_min_shadow:Decimal::new(2,1),
    wave_engulf_min_body:Decimal::new(5,2),
    pressure_adverse_exit:false,
    trend_flip_adverse_exit:false,
    pressure_lookback:120,
    pressure_body_min_fraction:Decimal::new(5,2),
    pressure_volume_ratio:Decimal::new(20,1),
    volume_inverse_n_clear:false,
    volume_down_exit:false,
    small_n_reduction:false,
    small_body_max_fraction:Decimal::new(1,2),
    small_body_lookback:10,
    initial_reduction_fraction:Decimal::new(5,1),
}}}
impl ExecutionParameters {
    pub fn validate(&self)->Result<()> {
        if [self.pressure_lookback,self.small_body_lookback,self.breakout_lookback,self.impulse_lookback,self.atr_period,
            self.rvol_lookback,self.regime_lookback,self.confirm_window,self.min_confirm_bars,self.max_hold_bars,self.max_positions,
            self.lot_size,self.minimum_entry_shares,self.liquidity_lookback,self.entry_ttl_bars].contains(&0){return Err("execution window and quantity parameters must be positive integers".into());}
        if self.min_confirm_bars>self.confirm_window{return Err("min_confirm_bars must not exceed confirm_window".into());}
        if [self.small_body_max_fraction,self.max_position_weight,self.risk_fraction,self.max_participation].iter().any(|v|*v<=Decimal::ZERO||*v>Decimal::ONE){return Err("execution fractions must be in (0,1]".into());}
        if [self.pressure_body_min_fraction,self.wave_exhaustion_reduction,self.wave_exhaustion_min_range,self.wave_exhaustion_min_shadow,
            self.wave_engulf_min_body,self.max_retracement,self.initial_reduction_fraction].iter().any(|v|*v<=Decimal::ZERO||*v>=Decimal::ONE){return Err("execution risk fractions must be in (0,1)".into());}
        if [self.min_close_location,self.regime_min_efficiency].iter().any(|v|*v<Decimal::ZERO||*v>Decimal::ONE){return Err("execution location/efficiency must be in [0,1]".into());}
        if self.pressure_volume_ratio<Decimal::ONE{return Err("pressure_volume_ratio must be at least 1".into());}
        if [self.initial_capital,self.take_profit_r,self.regime_strong_move_atr].iter().any(|v|*v<=Decimal::ZERO){return Err("capital, reward ratio and strong move must be positive".into());}
        if [self.min_rvol,self.breakout_buffer_bps,self.stop_atr_buffer,self.minimum_commission,self.commission_bps_per_side,
            self.slippage_bps_per_side,self.max_entry_gap].iter().any(|v|*v<Decimal::ZERO){return Err("execution thresholds and costs must be nonnegative".into());}
        if self.slippage_bps_per_side>=Decimal::from(10_000)||self.commission_bps_per_side>=Decimal::from(10_000){return Err("execution cost rates must be below 10000 bps".into());}Ok(())
    }
}
pub fn execution_parameters(profile:&StrategyProfile,scenario:&str)->Result<ExecutionParameters> {
    let raw=profile.scenarios.get(scenario).and_then(|v|v.get("execution")).filter(|v|v.is_object()).ok_or("unknown execution scenario")?;
    let parameters:ExecutionParameters=crate::indexed_json::from_value(raw.clone()).map_err(|error|format!("invalid execution parameters: {error}"))?;
    parameters.validate()?;Ok(parameters)
}
/// 账户与持仓风险的公共配置。调度器另从 execution_parameters 读取成交时钟及信号参数。
pub fn execution_configuration(profile:&StrategyProfile,scenario:&str)->Result<(crate::execution::BacktestConfig,super::trading::risk::HoldingRiskPolicy)> {
    use crate::execution::{BacktestConfig,FeeConfig};
    use super::trading::risk::{HoldingRiskPolicy,RiskConfig};
    let source=execution_parameters(profile,scenario)?;
    let account=BacktestConfig{
        initial_capital:source.initial_capital,max_positions:source.max_positions,max_position_weight:source.max_position_weight,
        risk_fraction:source.risk_fraction,lot_size:u64::try_from(source.lot_size).map_err(|_|"lot_size out of range")?,
        minimum_entry_shares:u64::try_from(source.minimum_entry_shares).map_err(|_|"minimum_entry_shares out of range")?,
        liquidity_lookback:source.liquidity_lookback,max_participation:source.max_participation,slippage_bps:source.slippage_bps_per_side,
        take_profit_r:source.take_profit_r,max_entry_gap:source.max_entry_gap,entry_ttl_bars:source.entry_ttl_bars,
        allow_add_on:source.allow_add_on,allow_same_day_exit:source.allow_same_day_exit,nonflat_limit_close_fill:source.nonflat_limit_close_fill,
        net_reward_risk_filter:source.net_reward_risk_filter,
        fees:FeeConfig{commission_bps:source.commission_bps_per_side,minimum_commission:source.minimum_commission,a_share_taxes:source.a_share_taxes},
        max_hold_bars:source.max_hold_bars,exit_on_target:source.exit_on_target,
    };
    let risk=HoldingRiskPolicy{
        risk:RiskConfig{pressure_lookback:source.pressure_lookback,pressure_body_min_fraction:source.pressure_body_min_fraction,
            pressure_volume_ratio:source.pressure_volume_ratio,wave_exhaustion_reduction:source.wave_exhaustion_reduction,
            wave_exhaustion_min_range:source.wave_exhaustion_min_range,wave_exhaustion_min_shadow:source.wave_exhaustion_min_shadow,
            wave_engulf_min_body:source.wave_engulf_min_body},
        volume_down_exit:source.volume_down_exit,small_n_reduction:source.small_n_reduction,
        small_body_max_fraction:source.small_body_max_fraction,small_body_lookback:source.small_body_lookback,
        inverse_n_close_reduce:source.inverse_n_close_reduce,wave_exhaustion_exit:source.wave_exhaustion_exit,
        pressure_adverse_exit:source.pressure_adverse_exit,trend_flip_adverse_exit:source.trend_flip_adverse_exit,
        staged_exit_enabled:source.staged_exit_enabled,staged_exit_same_day:source.staged_exit_same_day,
        staged_exit_intraday:source.staged_exit_intraday,initial_reduction_fraction:source.initial_reduction_fraction,
    };
    account.validate().map_err(|error|error.to_string())?;Ok((account,risk))
}
