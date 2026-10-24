//! SystemResult 诊断来自实际被消费的分支，不以缺失事件代替执行结果。
use super::qualification::index;
use serde::{Deserialize,Serialize};
use crate::indexed_json::Value;
use std::collections::BTreeMap;
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct TradingDiagnostics{pub counts:BTreeMap<String,usize>}
impl TradingDiagnostics{
    pub fn increment(&mut self,name:&str){*self.counts.entry(name.into()).or_default()+=1;}
    pub fn observe_pending(&mut self,measurements:&BTreeMap<String,super::targets::TradingNMeasurement>){
        for name in ["n_await_anchors","n_forming","n_origin_breached_before_completion","n_pullback_extended_new_anchor_required"]{self.counts.remove(name);}
        for measured in measurements.values().filter(|m|m.observation.completion.is_none()){
            let name=match measured.observation.status{
                super::NStatus::AwaitAnchors=>"n_await_anchors",super::NStatus::Forming=>"n_forming",
                super::NStatus::Invalidated=>"n_origin_breached_before_completion",super::NStatus::PullbackExtended=>"n_pullback_extended_new_anchor_required",
                super::NStatus::Completed=>continue,
            };self.increment(name);
        }
    }
    pub fn consume_day(&mut self,events:&[Value],now:usize,ambiguous:bool,minor:bool,scan_allowed:bool,buy_allowed:bool,insufficient:bool){
        self.increment("rows");self.counts.insert("minor_data_available".into(),usize::from(minor));
        if ambiguous{self.increment("ambiguous_bars");}if !ambiguous&&insufficient&&scan_allowed{self.increment("insufficient_pivots_bars");}
        if !scan_allowed{self.increment("interval_skipped_days");}else if !buy_allowed{self.increment("buy_interval_skipped_days");}
        for event in events{
            let kind=event.get("event").and_then(Value::as_str).unwrap_or("");
            match kind{
                "n_candidate_observed"=>self.increment("n_candidates"),
                "n_candidate_skipped"=>if let Some(reason)=event.get("reason").and_then(Value::as_str){self.increment(reason);},
                "n_geometry_rejected"=>self.increment("rejected_n_geometry"),
                "n_attack_rejected"=>self.increment("rejected_v3_positive_n_attack"),
                "n_completed"=>if let Some(direction)=event.get("direction").and_then(Value::as_str){self.increment(&format!("completed_{direction}_n"));},
                "regime_confirmation"=>if let Some(regime)=event.get("regime").and_then(Value::as_str){self.increment(&format!("regime_{regime}"));},
                "trading_interval_opened"=>self.increment("trading_interval_count"),
                "entry_candidate_evaluated"=>self.increment("entry_candidate_evaluations"),
                "entry_rejected"|"entry_preflight_rejected"=>if let Some(reason)=event.get("reason").and_then(Value::as_str){self.increment("entry_candidate_rejections");self.increment(&format!("entry_rejected_{reason}"));
                    if reason=="insufficient_close_gross_reward_risk"{self.increment("preflight_reward_risk_rejected");}},
                "long_signal"=>if index(event,"bar_index")==Some(now)&&event.get("candidate").and_then(|c|c.get("fully_executable")).and_then(Value::as_bool)==Some(true){
                    self.increment("long_signals");let candidate=&event["candidate"];
                    let buy_point=candidate.get("evidence").and_then(|e|e.get("hierarchy")).and_then(|h|h.get("buy_point_type")).and_then(Value::as_str)
                        .or_else(||candidate.get("reason").and_then(Value::as_str).and_then(|r|r.strip_prefix("system_")).filter(|r|matches!(*r,"combined_a_pullback_breakout"|"secondary_deep_pullback_reclaim"|"secondary_resistance_reclaim"|"shallow_base_breakout")));
                    if let Some(buy_point)=buy_point{self.increment(&format!("buy_point_{buy_point}"));}
                },
                "exit_signal"=>if index(event,"bar_index")==Some(now){self.increment("exit_signals");},
                "turn_observation"=>if event.get("suspicion_index").is_some_and(|value|!value.is_null()){self.increment("turn_suspicions");},
                "washout_reattack"=>self.increment("washout_confirmed"),
                _=>if !kind.is_empty(){self.increment(kind);}
            }
        }
    }
}
