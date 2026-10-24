//! 交易候选只消费已经冻结的N，复用量价状态并保留实际可知日。
use super::{RuleCandle,Direction,targets::{TradingNMeasurement,trading_n_identity},config::TradingDayConfig,entries};
use super::super::market_state::{regime::{self,RegimeObservation,RegimePolicy,WaveBoundary},control::{self,ControlBarObservation},price_action::ShadowPolicy};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct PreparedN{
    pub identity:String,pub measurement:TradingNMeasurement,pub regime:RegimeObservation,pub control:ControlBarObservation,
    pub epoch:usize,pub n_level:u8,pub attack_quality_warning:Option<String>,
}
impl PreparedN{
    pub fn attack(&self)->usize{self.measurement.observation.completion.as_ref().map_or(self.measurement.source.known_at,|c|c.bar_index)}
    pub fn known_at(&self)->usize{self.measurement.source.known_at.max(self.attack())}
}
pub fn prepare_candidates(bars:&[RuleCandle],measurements:&BTreeMap<String,TradingNMeasurement>,config:&TradingDayConfig,
    published:&mut BTreeSet<String>)->Result<(Vec<PreparedN>,BTreeMap<String,TradingNMeasurement>,Vec<Value>),String>{
    let mut candidates=vec![];let mut admitted=BTreeMap::new();let mut events=vec![];
    let mut ordered=measurements.iter().collect::<Vec<_>>();
    ordered.sort_by_key(|(_,m)|(m.source.known_at,m.setup.origin.index,m.setup.neckline.index,m.setup.pullback.index));
    for(key,m)in ordered{
        let Some(completion)=&m.observation.completion else{continue;};let attack=completion.bar_index;
        let shadow=Some(ShadowPolicy{minimum_range_fraction:config.shadow_fraction});
        let control=control::observe_prepared_control(bars,&m.setup,config.volume_lookback,shadow,Some(m.source.known_at),&m.observation)?;
        let whole=config.buy_point_definition=="whole_flip_wave_v3";
        let warning=if whole&&m.setup.direction==Direction::Up{entries::v3_positive_n_attack_rejection(&bars[attack],control.volume.as_ref().map(|v|v.increased_from_previous))}else{None};
        let identity=trading_n_identity(m).ok_or("prepared N identity missing")?;
        // 同作用域、方向及完成日冻结首个 N；子母棒内部回调保留给整波底部归属竞争。
        if !m.setup.allow_inside_pullback{
            let claim=format!("attack-candidate:{}:{:?}:{attack}",m.source.scope(),m.setup.direction);let owner=format!("{claim}:{identity}");
            if published.contains(&claim)&&!published.contains(&owner){continue;}
            published.insert(claim);published.insert(owner);
        }
        if config.strict_n_attack_quality{if let Some(reason)=warning{
            if published.insert(format!("attack-rejected:{identity}")){events.push(json!({"event":"n_attack_rejected","bar_index":attack,"known_at":m.source.known_at,
                "n_id":identity,"direction":"up","reason":reason,"previous_volume":attack.checked_sub(1).map(|i|bars[i].volume),"attack_volume":bars[attack].volume,
                "open":bars[attack].open,"high":bars[attack].high,"low":bars[attack].low,"close":bars[attack].close,"source_path":m.source.source_path,"qualification_window_start":m.source.window_start}));}
            continue;
        }}
        let regime=regime::observe_prepared_regime(bars,&m.setup,&RegimePolicy{shadow_policy:shadow,wave_boundary:WaveBoundary::NOrigin,local_resistance_failure:whole&&m.setup.direction==Direction::Up},
            Some(m.source.known_at),&m.observation)?;
        if !whole||m.setup.direction==Direction::Down{
            if published.insert(format!("completion:{identity}")){
                let targets=m.observation.targets.as_ref().ok_or("completed N target evidence missing")?;
                events.push(json!({"event":"n_completed","bar_index":attack,"known_at":m.source.known_at.max(attack),"direction":m.setup.direction,
                    "n_id":identity,"origin":m.setup.origin.index,"neckline":m.setup.neckline.index,"pullback":m.setup.pullback.index,
                    "defense":completion.defense,"counter_ratio":m.observation.anchors.as_ref().map(|a|a.retracement_ratio),"n_level":0,
                    "box_anchor":targets.box_anchor,"equal_wave":targets.equal_wave,"one_p":targets.one_p,"two_t":targets.two_t,
                    "source_path":m.source.source_path,"qualification_window_start":m.source.window_start,"window_start":m.source.known_at,
                    "outside_close_confirmed":m.setup.allow_outside_close&&m.setup.pullback.index==attack}));
            }
            if let Some(broken)=m.observation.first_defense_breach_index.into_iter().chain(m.observation.first_origin_breach_index).min(){
                if published.insert(format!("invalidated:{identity}")){events.push(json!({"event":"n_invalidated","bar_index":broken,
                    "known_at":broken.max(m.source.known_at),"attack":attack,"n_id":identity,"direction":m.setup.direction,
                    "reason":"n_defense_or_origin_broken","source_path":m.source.source_path,"qualification_window_start":m.source.window_start}));}
            }
        }
        admitted.insert(key.clone(),m.clone());candidates.push(PreparedN{identity,measurement:m.clone(),regime,control,epoch:m.source.points[0].path,n_level:0,attack_quality_warning:warning.map(str::to_string)});
    }Ok((candidates,admitted,events))
}

/// 底部归属和历史派生状态已确定后才发布量价状态，非主 N 不得产生策略审计。
pub fn observe_candidate_regimes(bars:&[RuleCandle],candidates:&[PreparedN],config:&TradingDayConfig,
    published:&mut BTreeSet<String>)->Result<Vec<Value>,String>{
    let mut events=vec![];
    for candidate in candidates{
        let m=&candidate.measurement;let identity=&candidate.identity;let attack=candidate.attack();let now=m.observation.asof_index;
        let completion=m.observation.completion.as_ref().ok_or("prepared candidate completion missing")?;
        if let Some(frame)=candidate.regime.frames.iter().find(|frame|frame.bar_index==now){
            if let Some(classification)=frame.regime{if published.insert(format!("regime:{identity}:{now}")){
                events.push(json!({"event":"regime_confirmation","bar_index":now,"known_at":now,"attack":attack,
                    "n_id":identity,"direction":m.setup.direction,"regime":classification,"regime_evidence":frame,
                    "source_path":m.source.source_path,"qualification_window_start":m.source.window_start}));
            }}
            if frame.regime.is_none()&&config.squeeze_pullback_entries&&m.setup.direction==Direction::Up{
                if let Some(proof)=regime::observe_squeeze_resumption(bars,now,frame,0,attack,completion.defense)?{
                    if published.insert(format!("resumption:{identity}:{now}")){events.push(json!({"event":"squeeze_resumption_observed",
                        "bar_index":now,"known_at":now,"attack":attack,"n_id":identity,"direction":"up","regime":proof.regime,
                        "proof":proof,"source_path":m.source.source_path,"qualification_window_start":m.source.window_start}));}
                }
            }
        }
    }Ok(events)
}
