use super::*;

/// 保存单个N的量价递推状态；历史批量观察与资格逐日观察使用同一判定。
pub(crate) struct IncrementalRegime {
    attack:usize, defense:Decimal, boundary:Decimal, direction:Direction, policy:RegimePolicy,
    measurement_start:Option<usize>, record:Decimal, rolling_all:bool,
    previous:Option<usize>, prior:Option<usize>, frame:RegimeFrame,
}

#[cfg(test)]
mod tests {
    use super::*;
    fn bars() -> Vec<RuleCandle> {
        [(9,10,9,10),(10,11,10,11),(11,12,11,12),(12,13,12,13),(13,13,11,12)]
            .into_iter().enumerate().map(|(i,(open,high,low,close))|RuleCandle {
                symbol:"fixture".into(),timestamp:format!("2020-01-{:02}",i+1),
                open:Decimal::from(open),high:Decimal::from(high),low:Decimal::from(low),close:Decimal::from(close),
                volume:Decimal::from(100),adjustment_factor:Decimal::ONE,raw_trading_active:None,raw_is_st:None,
            }).collect()
    }
    fn cursor(bars:&[RuleCandle]) -> IncrementalRegime {
        IncrementalRegime::new(bars,1,Decimal::from(9),Decimal::from(8),Direction::Up,
            RegimePolicy{shadow_policy:Some(ShadowPolicy{minimum_range_fraction:Decimal::new(5,1)}),
                wave_boundary:WaveBoundary::NOrigin,local_resistance_failure:true},None).unwrap()
    }
    #[test]
    fn historical_confirmation_is_not_a_current_permission() {
        let bars=bars();let mut cursor=cursor(&bars);
        assert_eq!(cursor.advance_to(&bars,2).unwrap().regime,None);
        assert_eq!(cursor.advance_to(&bars,3).unwrap().regime,Some(MarketRegime::StrongBull));
        let frame=cursor.advance_to(&bars,4).unwrap();
        assert_eq!(frame.regime,None);
        assert_eq!(frame.last_confirmed_index,Some(3));
        assert_eq!(frame.last_confirmed_regime,Some(MarketRegime::StrongBull));
        assert!(observe_squeeze_resumption(&bars,4,frame,0,1,Decimal::from(9)).unwrap().is_none());
    }
    #[test]
    fn repeated_observation_is_idempotent_and_cannot_rewind() {
        let bars=bars();let mut cursor=cursor(&bars);
        let first=serde_json::to_value(cursor.advance_to(&bars,3).unwrap()).unwrap();
        assert_eq!(first,serde_json::to_value(cursor.advance_to(&bars,3).unwrap()).unwrap());
        assert!(cursor.advance_to(&bars,2).is_err());
        assert!(cursor.advance_to(&bars,bars.len()).is_err());
    }
}
impl IncrementalRegime {
    pub(crate) fn new(bars:&[RuleCandle],attack:usize,defense:Decimal,boundary:Decimal,direction:Direction,
        policy:RegimePolicy,measurement_start:Option<usize>)->Result<Self,String>{
        if attack==0||attack>=bars.len(){return Err("completed N attack is not available".into());}
        if let Some(shadow)=policy.shadow_policy{shadow.validate()?;}
        let bar=&bars[attack];let record=direction.extreme(bar);
        let resistance=if policy.local_resistance_failure&&direction==Direction::Up&&measurement_start.is_none_or(|i|attack>=i){
            Some(observe_resistance(&bars[attack-1],bar,direction,policy.shadow_policy)?)
        }else{None};
        let first=resistance.as_ref().and_then(|r|(r.detected==Some(true)).then_some(attack));
        let unknown=resistance.as_ref().is_some_and(|r|r.detected.is_none());
        let frame=RegimeFrame{bar_index:attack,observed_at:bar.timestamp.clone(),phase:"await_response".into(),regime:None,
            resistance,resistance_outcome:if first.is_some(){"resistance_outcome_pending"}else{"not_yet_observed"}.into(),
            first_resistance_index:first,unknown_resistance_seen:unknown,first_defense_breach_index:None,first_wave_breach_index:None,
            continuation_level:record,close_continuation:false,rolling_virtual_defense:defense,rolling_defense_held:None,
            uninterrupted_progress:true,last_confirmed_regime:None,last_confirmed_index:None};
        Ok(Self{attack,defense,boundary,direction,policy,measurement_start,record,rolling_all:true,previous:None,prior:None,frame})
    }
    pub(crate) fn frame(&self)->&RegimeFrame{&self.frame}
    pub(crate) fn step(&mut self,bars:&[RuleCandle],i:usize)->Result<&RegimeFrame,String>{
        if i>=bars.len()||i!= (self.frame.bar_index+1).max(self.measurement_start.unwrap_or(0)){
            return Err("regime cursor requires the next available market date".into());
        }
        let dir=self.direction;let sign=dir.sign();let up=dir==Direction::Up;let window=self.policy.local_resistance_failure&&up;
        let previous=self.previous.unwrap_or(i-1);let before=self.prior.unwrap_or(i-2);
        let (bar,prev,before)=(&bars[i],&bars[previous],&bars[before]);let attack_bar=&bars[self.attack];
        let first_available=self.measurement_start==Some(i);
        let adverse=if first_available{bar.close}else{dir.adverse(bar)};
        let resistance=observe_resistance(prev,bar,dir,self.policy.shadow_policy)?;
        let eligible=!window||i==self.attack+1;
        let old=&self.frame;let mut first=old.first_resistance_index;let mut unknown=old.unknown_resistance_seen;
        if eligible&&resistance.detected==Some(true)&&first.is_none(){first=Some(i);}
        if eligible{unknown|=resistance.detected.is_none();}
        let defense=old.first_defense_breach_index.or_else(||(sign*(adverse-self.defense)<Decimal::ZERO).then_some(i));
        let wave=old.first_wave_breach_index.or_else(||(sign*(adverse-self.boundary)<Decimal::ZERO).then_some(i));
        let rolling=if up{prev.low.min(before.close)}else{prev.high.max(before.close)};
        let held=sign*(adverse-rolling)>=Decimal::ZERO;let rolling_all=self.rolling_all&&held;
        let uninterrupted=old.uninterrupted_progress&&sign*(bar.close-prev.close)>Decimal::ZERO;
        let mut continuation=sign*(bar.close-self.record)>Decimal::ZERO;
        if self.policy.local_resistance_failure&&i>=self.attack+2&&first.is_some()&&defense.is_none(){
            let attack_break=window&&bar.high>attack_bar.high&&bar.close>attack_bar.close&&strong_bullish_candle(bar);
            let response=rolling_all&&held&&(!window||old.bar_index<=self.attack+1)
                &&old.resistance.as_ref().is_some_and(|r|r.detected==Some(true));
            continuation=(continuation||attack_break||response)&&sign*(bar.close-prev.close)>Decimal::ZERO
                &&sign*(bar.close-attack_bar.close)>Decimal::ZERO&&sign*(bar.close-bar.open)>Decimal::ZERO
                &&(!eligible||resistance.detected==Some(false));
        }
        let mut phase=if i==self.attack+1{"await_confirmation"}else{"pending"};let mut regime=None;
        let mut outcome=if first.is_some(){"resistance_outcome_pending"}else if unknown{"unknown_shadow_rule"}else{"no_listed_resistance_detected"};
        if wave.is_some(){phase="wave_boundary_breached";outcome="wave_boundary_breached";}
        else{
            if first.is_some()&&defense.is_some(){outcome="n_defense_breached_wave_boundary_held";}
            if i>=self.attack+2&&continuation{
                if first.is_none()&&!unknown&&defense.is_none()&&uninterrupted&&rolling_all{
                    regime=Some(if up{MarketRegime::StrongBull}else{MarketRegime::StrongBear});
                }else if first.is_some_and(|r|r<i){
                    if defense.is_none(){regime=Some(if up{MarketRegime::Bull}else{MarketRegime::Bear});outcome="failed_to_break_n_defense_and_continuation_confirmed";}
                    else if defense.is_some_and(|d|d<i){regime=Some(if up{MarketRegime::GrindUp}else{MarketRegime::GrindDown});}
                }
                if regime.is_some(){phase="confirmed_on_this_bar";}
            }
        }
        let frame=RegimeFrame{bar_index:i,observed_at:bar.timestamp.clone(),phase:phase.into(),regime,resistance:Some(resistance),
            resistance_outcome:outcome.into(),first_resistance_index:first,unknown_resistance_seen:unknown,
            first_defense_breach_index:defense,first_wave_breach_index:wave,continuation_level:self.record,
            close_continuation:continuation,rolling_virtual_defense:rolling,rolling_defense_held:Some(held),uninterrupted_progress:uninterrupted,
            last_confirmed_regime:regime.or(old.last_confirmed_regime),last_confirmed_index:if regime.is_some(){Some(i)}else{old.last_confirmed_index}};
        self.prior=Some(previous);self.previous=Some(i);self.rolling_all=rolling_all;
        let forward=if first_available{bar.close}else{dir.extreme(bar)};
        if sign*(forward-self.record)>Decimal::ZERO{self.record=forward;}
        self.frame=frame;Ok(&self.frame)
    }
    pub(crate) fn advance_to(&mut self,bars:&[RuleCandle],now:usize)->Result<&RegimeFrame,String>{
        if now<self.frame.bar_index||now>=bars.len(){return Err("regime cursor cannot rewind or observe future bars".into());}
        for i in (self.frame.bar_index+1).max(self.measurement_start.unwrap_or(0))..=now{self.step(bars,i)?;}
        Ok(&self.frame)
    }
}
