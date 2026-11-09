//! 区间门控后的买卖流程；卖点独立于买点，实际撮合仍由账户负责。
use super::*;

#[derive(Debug,PartialEq,Eq)]
pub(super) enum Route { Active, Liquidate, Skip }
pub(super) fn route(in_window:bool,has_position:bool,held_window_closed:bool,pending_cleanup:bool)->Route {
    if pending_cleanup {Route::Skip}
    else if !in_window||held_window_closed {if has_position {Route::Liquidate}else{Route::Skip}}
    else {Route::Active}
}
pub(super) fn prioritize_exits(decisions:&mut Vec<StrategyDecision>){
    if decisions.iter().any(|d|matches!(d,StrategyDecision::Exit(_))){
        decisions.retain(|d|!matches!(d,StrategyDecision::Enter(_)|StrategyDecision::RaiseStop(_)));
    }
}
#[derive(Default)]
pub(super) struct FlowOutput {
    pub decisions:Vec<StrategyDecision>,pub audit:Vec<Value>,pub minute_fallbacks:Vec<Value>,pub unavailable:BTreeSet<String>,
}
struct DailyTradeFlow<'a>{
    state:&'a mut SymbolState,config:&'a SystemConfig,position:Option<&'a BacktestPosition>,
    has_minutes:bool,pending_full:bool,audit:Vec<Value>,minute_fallbacks:Vec<Value>,unavailable:BTreeSet<String>,
}
pub(super) fn execute_daily(state:&mut SymbolState,o:&BacktestObservation,position:Option<&BacktestPosition>,
    config:&SystemConfig,daily:&mut trading::day::TradingDayEvaluation,frozen:Option<&[Value]>,has_minutes:bool,pending_full:bool)->ExecutionResult<FlowOutput>{
    let mut flow=DailyTradeFlow{state,config,position,has_minutes,pending_full,audit:vec![],minute_fallbacks:vec![],unavailable:BTreeSet::new()};
    let bars=flow.state.bars.clone();let now=o.bar_index;
    let frozen_exit=if let Some(rows)=frozen{let(entries,exit)=frozen_daily(rows,&bars[now],now,config)?;daily.entry_candidates=entries;exit}else{None};
    let(mut decisions,minute_audit)=observe_minutes(flow.state,&bars,o,position,config,has_minutes)?;
    for mut event in minute_audit{event["symbol"]=json!(o.candle.symbol);flow.audit.push(event);}
    let strategy_exit=if frozen.is_some(){frozen_exit}else{current_strategy_exit(&daily.events,now)};
    let has_strategy_exit=strategy_exit.is_some();
    flow.evaluate_sell(o,&bars,daily,strategy_exit,&mut decisions)?;
    prioritize_exits(&mut decisions);
    if !has_strategy_exit&&!decisions.iter().any(|d|matches!(d,StrategyDecision::Exit(_)|StrategyDecision::Enter(_))){
        flow.evaluate_buy(o,&bars,daily,&mut decisions)?;
    }
    bind_entry_windows(flow.state,config.analyzer.trading.buy_interval_level,&mut decisions);
    Ok(FlowOutput{decisions,audit:flow.audit,minute_fallbacks:flow.minute_fallbacks,unavailable:flow.unavailable})
}
impl DailyTradeFlow<'_>{
    fn evaluate_sell(&mut self,o:&BacktestObservation,bars:&[RuleCandle],daily:&trading::day::TradingDayEvaluation,
        strategy_exit:Option<RuleDecision>,decisions:&mut Vec<StrategyDecision>)->ExecutionResult<()>{
        let symbol=&o.candle.symbol;let now=o.bar_index;let state=&mut *self.state;let has_minutes=self.has_minutes;
        let global=daily.frames.first().map(|frame|frame.exit_decisions.as_slice()).unwrap_or(&[]);
        if let Some(position)=self.position{
            let mut context=HoldingRiskContext::from_position(position);
            context.positive_n_index=state.analyzer.trading_state().risk.positive_n_index;
            context.trading_eligible=true;context.pending_full_exit=self.pending_full;
            context.wave_events=owned_wave_events(&state.analyzer.trading_state().events,position,now);
            context.c_equal_events=context.wave_events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("wave_c_equal_target")).cloned().collect();
            context.target_risk=state.analyzer.trading_state().entry_risk_state().history.get(&now).and_then(|r|r.target.clone()).map(|mut decision|{
                if decision.fraction<Decimal::ONE{decision.fraction=self.config.holding.risk.wave_exhaustion_reduction;
                    decision.evidence.insert("exit_fraction".into(),json!(decision.fraction));
                    if decision.cumulative{decision.evidence.insert("exit_target_fraction".into(),json!(decision.fraction));}}
                decision
            });
            context.daily_fallback=self.config.parameters.staged_exit_intraday&&!has_minutes&&self.config.parameters.missing_minute_daily_fallback;
            if context.daily_fallback{
                let fallback=json!({"symbol":symbol,"date":bars[now].timestamp,"reason":"minute_history_missing","execution_model":"same_day_close","purpose":"staged_exit"});
                self.minute_fallbacks.push(fallback.clone());self.audit.push(json!({"event":"minute_daily_fallback","bar_index":now,"evidence":fallback}));
            }
            let (low,high)=state.extrema.observe(position,o,has_minutes)?;
            context.hard_exit=execution::hard_exit_decision(position,o,&self.config.execution,low,high)?;
            if strategy_exit.as_ref().is_some_and(|d|d.reason.split('|').any(|r|r=="mother_child_inverse_n_low_break")){
                context.mother_child_inverse=intraday::mother_child_inverse_n_break(&bars,now);
            }
            if self.config.parameters.volume_inverse_n_clear&&now>0&&strategy_exit.as_ref().is_some_and(|d|d.reason.split('|').any(|r|r=="inverse_n_risk_exit")){
                let reference=bars[..now].iter().rev().find(|b|b.close<b.open);
                let volume_bearish=trading::entries::is_volume_bearish(&bars[now],bars.get(now-1),reference);
                if bars[now].volume>bars[now-1].volume||volume_bearish{context.volume_inverse=Some(RuleDecision::new("volume_inverse_n_clear",now,Decimal::ONE)
                    .with("execution_model","same_day_close").with("observed_volume",bars[now].volume).with("previous_volume",bars[now-1].volume));}
            }
            let evaluation=risk::evaluate_holding_risks(&bars,now,global,&context,&mut state.holding,&self.config.holding).map_err(failure)?;
            self.unavailable.extend(evaluation.unavailable_rules);
            let mut chosen=risk::resolve_exit_precedence(&evaluation.decisions,&context).map_err(failure)?;
            if chosen.is_none()&&!context.pending_full_exit{chosen=strategy_exit.map(strategy_execution);}
            if let Some(mut rule)=chosen{
                if rule.reason=="inverse_n_risk_exit"&&(self.config.parameters.inverse_n_close_reduce
                    ||self.config.parameters.inverse_n_after_reduction&&state.holding.staged.breakdown_index.is_some()&&position.quantity<position.initial_quantity){
                    rule.reason=if self.config.parameters.inverse_n_close_reduce{"inverse_n_close_reduce_90"}else{"inverse_n_after_reduction_90"}.into();
                    rule.fraction=Decimal::new(9,1);rule.cumulative=true;rule.evidence.insert("inverse_observed_index".into(),json!(now));
                    if self.config.parameters.inverse_n_close_reduce{rule.evidence.insert("execution_model".into(),json!("same_day_close"));}
                }
                let execution_model=mode(rule.evidence.get("execution_model").and_then(Value::as_str).unwrap_or("next_open"),false).map_err(failure)?;
                decisions.push(StrategyDecision::Exit(ExitDecision{symbol:symbol.clone(),known_at:o.at,bar_index:now,mode:execution_model,rule,remaining_quantity:None}));
            }
        }
        Ok(())
    }
    fn evaluate_buy(&mut self,o:&BacktestObservation,bars:&[RuleCandle],daily:&trading::day::TradingDayEvaluation,
        decisions:&mut Vec<StrategyDecision>)->ExecutionResult<()>{
        let symbol=&o.candle.symbol;let now=o.bar_index;
            for candidate in &daily.entry_candidates{
                if !candidate.fully_executable{self.audit.push(json!({"event":"entry_not_executable","symbol":symbol,"bar_index":now,"candidate":candidate,"unavailable_rules":daily.unavailable_rules}));continue;}
                let mut proof=evidence(&candidate.evidence);proof.insert("signal_index".into(),json!(now));
                proof.insert("qualification_index".into(),json!(candidate.qualification_index));proof.insert("trend_level".into(),json!(candidate.trend_level));proof.insert("source_path".into(),json!(candidate.source_path));
                if let Some(position)=self.position{if candidate.stop>position.stop&&candidate.stop<bars[now].close{
                    decisions.push(StrategyDecision::RaiseStop(StopDecision{symbol:symbol.clone(),known_at:o.at,stop:candidate.stop}));}
                    if !self.config.parameters.allow_add_on{continue;}
                }
                let execution_model=mode(&candidate.execution_model,self.config.parameters.entry_at_close).map_err(failure)?;
                if execution_model==ExecutionMode::Intraday5mNextOpen{return Err(failure("close candidate cannot authorize an earlier minute entry"));}
                let required=proof.get("minimum_reward_risk").and_then(price).unwrap_or(self.config.analyzer.trading.minimum_reward_risk);
                decisions.push(StrategyDecision::Enter(EntryDecision{symbol:symbol.clone(),known_at:o.at,bar_index:now,mode:execution_model,
                    reason:candidate.reason.clone(),reference_price:candidate.price,stop_price:candidate.stop,target_price:candidate.target,
                    minimum_reward_risk:Some(required),rvol:proof.get("rvol").and_then(price),rejection:None,evidence:proof}));
                break;
            }
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn outside_window_only_liquidates_existing_position(){
        assert_eq!(route(false,false,false,false),Route::Skip);
        assert_eq!(route(false,true,false,false),Route::Liquidate);
        assert_eq!(route(true,true,true,false),Route::Liquidate);
        assert_eq!(route(true,true,false,true),Route::Skip);
    }
    #[test] fn existing_position_in_window_does_not_require_a_buy_signal(){
        assert_eq!(route(true,true,false,false),Route::Active);
        assert_eq!(route(true,false,false,false),Route::Active);
    }
    #[test] fn simultaneous_exit_suppresses_buy_and_stop_raise(){
        let at=DateTime::parse_from_rfc3339("2026-11-06T15:00:00+08:00").unwrap().with_timezone(&Utc);
        let entry=StrategyDecision::Enter(EntryDecision{symbol:"sh.601086".into(),known_at:at,bar_index:1,
            mode:ExecutionMode::SameDayClose,reason:"fixture_buy".into(),reference_price:Decimal::from(10),
            stop_price:Decimal::from(8),target_price:None,minimum_reward_risk:None,rvol:None,rejection:None,evidence:BTreeMap::new()});
        let exit=StrategyDecision::Exit(ExitDecision{symbol:"sh.601086".into(),known_at:at,bar_index:1,
            mode:ExecutionMode::SameDayClose,rule:RuleDecision::new("fixture_sell",1,Decimal::ONE),remaining_quantity:None});
        let raise=StrategyDecision::RaiseStop(StopDecision{symbol:"sh.601086".into(),known_at:at,stop:Decimal::from(9)});
        let mut decisions=vec![entry,raise,exit];prioritize_exits(&mut decisions);
        assert_eq!(decisions.len(),1);assert!(matches!(decisions[0],StrategyDecision::Exit(_)));
    }
}
