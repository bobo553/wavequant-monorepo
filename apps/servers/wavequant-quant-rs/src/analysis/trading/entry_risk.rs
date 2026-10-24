//! 两吐抵抗、五顶剩余空间和十满回调：共享收盘事件历史，不重算趋势。
use super::{RuleCandle,RuleDecision,Direction,ratio,exits,config::TradingDayConfig,qualification::{index as idx,price}};
use crate::analysis::market_state::price_action::{observe_resistance,ShadowPolicy};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

#[derive(Debug,Clone,Serialize,Deserialize)]
struct Goal {attack:usize,origin:usize,known:usize,target:Decimal,box_height:Decimal,defense:Decimal}
#[derive(Debug,Clone,Serialize,Deserialize)]
struct Pause {attack:usize,origin:usize,reached:usize,peak:Decimal,anchor:Decimal,anchor_source:String,threshold:Decimal,half:Decimal}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct EntryRisks {pub target:Option<RuleDecision>,pub five_top:Option<RuleDecision>,pub ten_full:Option<RuleDecision>,pub events:Vec<Value>}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct EntryRiskState {
    pub start_index:usize,pub processed_days:usize,pub reduction_fraction:Option<Decimal>,bars:Vec<RuleCandle>,last:Option<usize>,events:Vec<Value>,config:Option<TradingDayConfig>,
    pub history:BTreeMap<usize,EntryRisks>,invalidated:BTreeMap<String,usize>,bearish:Option<usize>,previous_volume_bearish:bool,
    five_active:BTreeMap<String,Goal>,five_invalidated:BTreeSet<String>,recent_b:BTreeMap<String,Decimal>,pauses:BTreeMap<String,Pause>,
    resolved:BTreeSet<String>,minimum_closes:BTreeMap<String,Decimal>,corrections:BTreeMap<String,(Decimal,usize,Decimal)>,
}
impl EntryRiskState {
    pub fn new(start_index:usize)->Self{Self{start_index,..Self::default()}}
    fn reset(&mut self){self.last=self.start_index.checked_sub(1);self.history.clear();self.invalidated.clear();self.five_active.clear();self.five_invalidated.clear();
        self.recent_b.clear();self.pauses.clear();self.resolved.clear();self.minimum_closes.clear();self.corrections.clear();
        self.bearish=(0..self.start_index).rev().find(|i|self.bars[*i].close<self.bars[*i].open);
        self.previous_volume_bearish=if let Some(previous)=self.start_index.checked_sub(1){let earlier=(0..previous).rev().find(|i|self.bars[*i].close<self.bars[*i].open);
            volume_bearish(&self.bars,previous,earlier)}else{false};}
}
fn key(attack:usize,origin:Option<usize>)->String{format!("{attack}:{}",origin.map(|i|i.to_string()).unwrap_or_default())}
fn day(bars:&[RuleCandle],i:usize)->String{bars[i].timestamp.get(..10).unwrap_or(&bars[i].timestamp).into()}
fn positive(event:&Value,field:&str)->Option<Decimal>{price(event,field).filter(|v|*v>Decimal::ZERO)}
fn volume_bearish(bars:&[RuleCandle],index:usize,last:Option<usize>)->bool{
    let bar=&bars[index];bar.close<bar.open && [index.checked_sub(1),last].into_iter().flatten().any(|i|i<index && bars[i].volume>Decimal::ZERO && bar.volume>bars[i].volume)
}
fn upper(bar:&RuleCandle)->Decimal{bar.high-bar.open.max(bar.close)}
fn long_shadow(bar:&RuleCandle)->bool{bar.high>bar.low&&upper(bar)*Decimal::TWO>=bar.high-bar.low&&upper(bar)>=(bar.close-bar.open).abs()}
fn patterns(bars:&[RuleCandle],previous:usize,index:usize)->Result<Vec<String>,String>{
    Ok(observe_resistance(&bars[previous],&bars[index],Direction::Up,Some(ShadowPolicy{minimum_range_fraction:ratio(1,2)}))?.reasons)
}
fn decision(index:usize,reason:&str,fraction:Decimal,evidence:Value,cumulative:bool)->RuleDecision{
    let mut d=RuleDecision::new(reason,index,fraction);d.cumulative=cumulative;
    if let Some(map)=evidence.as_object(){d.evidence.extend(map.clone());}
    d.evidence.insert("execution_model".into(),json!("same_day_close"));
    d.evidence.insert("exit_fraction".into(),json!(fraction));
    if cumulative{d.evidence.insert("exit_target_fraction".into(),json!(fraction));}d
}
fn observed(bars:&[RuleCandle],index:usize)->Value{let b=&bars[index];json!({"observed_open":b.open,"observed_high":b.high,"observed_low":b.low,
    "observed_close":b.close,"observed_volume":b.volume})}
fn extend(target:&mut Value,other:&Value){if let(Some(t),Some(o))=(target.as_object_mut(),other.as_object()){for(k,v)in o{t.insert(k.clone(),v.clone());}}}
fn body_reversal(bars:&[RuleCandle],index:usize,bearish:Option<usize>)->Option<Value>{
    if index==0||!volume_bearish(bars,index,bearish){return None;}
    let (bar,prev)=(&bars[index],&bars[index-1]);let reference=bearish.filter(|i|*i<index).map(|i|&bars[i]);
    let doji=prev.open==prev.close&&bar.open>=prev.close&&bar.close<prev.low;
    let engulf=prev.open!=prev.close&&bar.open>=prev.open.max(prev.close)&&bar.close<=prev.open.min(prev.close);
    if !doji&&!engulf{return None;}
    let mut proof=observed(bars,index);extend(&mut proof,&json!({"body_reversal_pattern":if doji{"doji_body_cover_close_below_low"}else{"bearish_body_engulf"},
        "previous_is_doji":prev.open==prev.close,"close_below_previous_low":bar.close<prev.low,
        "previous_open":prev.open,"previous_high":prev.high,"previous_low":prev.low,"previous_close":prev.close,"previous_volume":prev.volume,
        "engulfed_date":day(bars,index-1),"bearish_reference_date":bearish.map(|i|day(bars,i)),"bearish_reference_volume":reference.map(|b|b.volume),
        "volume_above_previous":prev.volume>Decimal::ZERO&&bar.volume>prev.volume,
        "volume_above_last_bearish":reference.is_some_and(|b|b.volume>Decimal::ZERO&&bar.volume>b.volume)}));Some(proof)
}
fn next_clear(bars:&[RuleCandle],index:usize,bearish:Option<usize>,mut evidence:Value,reason:&str)->Option<RuleDecision>{
    if index==0||!volume_bearish(bars,index,bearish){return None;}let(bar,prev)=(&bars[index],&bars[index-1]);
    if bar.low>=prev.low||bar.close>=prev.close{return None;}
    extend(&mut evidence,&observed(bars,index));extend(&mut evidence,&json!({"bearish_reference_date":bearish.map(|i|day(bars,i)),
        "bearish_reference_volume":bearish.map(|i|bars[i].volume),"previous_volume":prev.volume,"previous_low":prev.low,"previous_close":prev.close}));
    Some(decision(index,reason,Decimal::ONE,evidence,false))
}
fn full(risk:&Option<RuleDecision>)->bool{risk.as_ref().is_some_and(|d|d.fraction==Decimal::ONE)}
fn midpoint_clear(bars:&[RuleCandle],index:usize,previous_volume_bearish:bool)->Option<RuleDecision>{
    if index==0||!previous_volume_bearish{return None;}let(bar,prev)=(&bars[index],&bars[index-1]);
    if prev.close>=prev.open||prev.volume<=Decimal::ZERO||bar.volume<=prev.volume{return None;}
    let midpoint=(prev.open+prev.close)/Decimal::TWO;
    if upper(bar)<=(bar.close-bar.open).abs()||bar.close>=midpoint{return None;}
    let mut evidence=observed(bars,index);extend(&mut evidence,&json!({"target_trigger":"volume_upper_gt_body_below_previous_volume_bearish_midpoint",
        "bearish_reference_date":day(bars,index-1),"previous_volume_bearish":true,"previous_open":prev.open,"previous_close":prev.close,
        "previous_volume":prev.volume,"bearish_body_midpoint":midpoint,"upper_shadow":upper(bar),"candle_body":(bar.close-bar.open).abs(),
        "decision_timestamp":format!("{}T15:00:00",day(bars,index))}));
    Some(decision(index,"volume_shadow_below_bearish_midpoint_clear",Decimal::ONE,evidence,false))
}
fn holding_day(bars:&[RuleCandle],index:usize,events:&[Value],state:&mut EntryRiskState,independent:bool)->Result<Option<RuleDecision>,String>{
    let previous_bearish=state.bearish;let reversal=body_reversal(bars,index,previous_bearish);
    let shadow=exits::volume_shadow_next_decline(bars,index,None).or_else(||midpoint_clear(bars,index,state.previous_volume_bearish));
    let volume=volume_bearish(bars,index,previous_bearish);state.previous_volume_bearish=volume;
    let bar=&bars[index];if bar.close<bar.open{state.bearish=Some(index);}
    for event in events.iter().filter(|e|idx(e,"bar_index")==Some(index)&&e["event"]=="wave_projection_invalidated"){
        if let Some(attack)=idx(event,"attack"){let key=key(attack,idx(event,"origin_index"));state.invalidated.entry(key).and_modify(|old|*old=(*old).min(index)).or_insert(index);}}
    let mut risk=None;let mut body_risk=None;let mut body_source=None;
    for event in events {
        let kind=event["event"].as_str().unwrap_or("");
        if kind!="wave_projection_ready" && !(matches!(kind,"wave_n_target_reached"|"wave_projection_target_reached")&&event["reached_stage"]=="two_t"){continue;}
        let(Some(reached),Some(attack),Some(target))=(idx(event,"bar_index"),idx(event,"attack"),positive(event,"two_t").or_else(||positive(event,"reached_target")))else{continue;};
        if attack>reached||reached>index{continue;}let origin=idx(event,"origin_index");
        let observed_price=if reached==attack||reached==state.start_index{bars[reached].close}else{bars[reached].high};if observed_price<target{continue;}
        let invalidated=state.invalidated.get(&key(attack,origin)).copied().unwrap_or(usize::MAX);if invalidated<=reached{continue;}
        if index>reached.saturating_add(2)&&((reversal.is_none()&&!volume)||index>=invalidated){continue;}
        let mut base=json!({"attack":attack,"wave_n_date":day(bars,attack),"wave_reached_stage":"two_t","wave_reached_price":target,
            "wave_reached_date":day(bars,reached),"execution_model":"same_day_close"});
        if let Some(origin)=origin.filter(|i|*i<=reached){base["wave_n_origin_date"]=json!(day(bars,origin));base["wave_n_origin_price"]=json!(bars[origin].low);}
        if volume&&reached<=index&&index<invalidated&&!full(&risk){let mut evidence=base.clone();extend(&mut evidence,&observed_values(bars,index,previous_bearish));
            evidence["target_trigger"]=json!("reached_two_t_volume_bearish");risk=Some(decision(index,"wave_two_t_volume_bearish_reduce",state.reduction_fraction.unwrap_or(ratio(8,10)),evidence,true));}
        let source=(reached,attack,origin.map(|i|i as i128).unwrap_or(-1));
        if reached<index&&index<invalidated&&body_source.is_none_or(|prior|source>prior){if let Some(reversal)=&reversal{
            body_source=Some(source);let mut evidence=base.clone();extend(&mut evidence,reversal);
            evidence["target_trigger"]=json!(if reversal["previous_is_doji"]==true{"post_two_t_doji_body_cover_close_below_low_volume_clear"}else{"post_two_t_bearish_body_engulf_volume_gt_last_bearish_or_previous"});
            body_risk=Some(decision(index,"wave_two_t_body_volume_clear",Decimal::ONE,evidence,false));}}
        if index==reached+1&&bars[reached].high>bars[reached].low&&Decimal::from(5)*upper(&bars[reached])>Decimal::TWO*(bars[reached].high-bars[reached].low){
            let mut evidence=base.clone();extend(&mut evidence,&json!({"target_trigger":"first_two_t_reach_upper_gt_40pct_next_session",
                "previous_upper_shadow_fraction":upper(&bars[reached])/(bars[reached].high-bars[reached].low),"target_confirmation_patterns":patterns(bars,reached,index)?}));
            if !full(&risk){if let Some(clear)=next_clear(bars,index,previous_bearish,evidence,"wave_two_t_next_volume_clear"){risk=Some(clear);}}}
        for warning_index in reached.max(1)..(reached+2).min(index+1){
            if invalidated<=warning_index{break;}if index!=warning_index&&index!=warning_index+1{continue;}
            let warning=&bars[warning_index];if !long_shadow(warning){continue;}
            let mut warning_patterns=patterns(bars,warning_index-1,warning_index)?;
            if !warning_patterns.iter().any(|p|p=="long_upper_shadow"){warning_patterns.push("long_upper_shadow".into());}
            let mut evidence=base.clone();extend(&mut evidence,&observed(bars,warning_index));extend(&mut evidence,&json!({"target_warning_index":warning_index,
                "target_warning_date":day(bars,warning_index),"target_warning_low":warning.low,"target_warning_close":warning.close,
                "wave_upper_shadow_fraction":upper(warning)/(warning.high-warning.low),"target_resistance_patterns":warning_patterns,"previous_close":bars[warning_index-1].close}));
            if index==warning_index{if !full(&risk){risk=Some(decision(index,"wave_two_t_resistance_reduce",state.reduction_fraction.unwrap_or(ratio(8,10)),evidence,true));}continue;}
            let confirmation=patterns(bars,warning_index,index)?;
            if !confirmation.is_empty()&&!full(&risk){let mut reduction=evidence.clone();extend(&mut reduction,&observed(bars,index));
                reduction["target_confirmation_patterns"]=json!(confirmation);reduction["previous_close"]=json!(warning.close);
                risk=Some(decision(index,"wave_two_t_resistance_reduce",state.reduction_fraction.unwrap_or(ratio(8,10)),reduction,true));}
            evidence["target_confirmation_patterns"]=json!(confirmation);
            if let Some(clear)=next_clear(bars,index,previous_bearish,evidence,"wave_two_t_resistance_volume_clear"){risk=Some(clear);}
        }
    }
    if !full(&risk){if let Some(body)=body_risk{risk=Some(body);}}
    if independent&&!full(&risk){if let Some(mut evidence)=reversal{evidence["target_trigger"]=json!(if evidence["previous_is_doji"]==true{"doji_body_cover_close_below_low_volume_gt_previous_or_last_bearish"}else{"bearish_body_engulf_volume_gt_previous_or_last_bearish"});
        risk=Some(decision(index,"bearish_body_volume_clear",Decimal::ONE,evidence,false));}}
    if independent&&!full(&risk){if let Some(shadow)=shadow{risk=Some(shadow);}}Ok(risk)
}
fn observed_values(bars:&[RuleCandle],index:usize,bearish:Option<usize>)->Value{
    let mut value=observed(bars,index);extend(&mut value,&json!({"previous_volume":index.checked_sub(1).map(|i|bars[i].volume),
        "bearish_reference_date":bearish.map(|i|day(bars,i)),"bearish_reference_volume":bearish.map(|i|bars[i].volume)}));value
}

fn five_top_day(bars:&[RuleCandle],index:usize,events:&[Value],state:&mut EntryRiskState)->Option<RuleDecision>{
    if let Some(known)=index.checked_sub(1){let mut dated:Vec<_>=events.iter().filter(|e|idx(e,"bar_index")==Some(known)).collect();
        dated.sort_by_key(|e|(e["event"]=="wave_projection_target_reached",e["event"]=="wave_projection_invalidated"));
        for event in dated{let(Some(attack),Some(origin))=(idx(event,"attack"),idx(event,"origin_index"))else{continue;};
            if !(origin<attack&&attack<=known){continue;}let identity=key(attack,Some(origin));
            if event["state"]=="invalidated"{state.five_invalidated.insert(identity.clone());}
            if state.five_invalidated.contains(&identity){state.five_active.remove(&identity);continue;}
            let values=(positive(event,"target"),positive(event,"box_height"),positive(event,"defense"));
            if event["target_stage"]!="five_top"||!matches!(event["state"].as_str(),Some("stacking"|"pushing")){
                state.five_active.remove(&identity);continue;}
            match values{(Some(target),Some(box_height),Some(defense))if defense<target=>{
                state.five_active.insert(identity,Goal{attack,origin,known,target,box_height,defense});},_=>{state.five_active.remove(&identity);}}
        }}
    let broken:Vec<_>=state.five_active.iter().filter(|(_,goal)|bars[index].low<goal.defense).map(|(key,_)|key.clone()).collect();
    for identity in broken{state.five_invalidated.insert(identity.clone());state.five_active.remove(&identity);}
    let goal=state.five_active.values().filter(|goal|goal.target-bars[index].close<=goal.box_height)
        .min_by(|a,b|a.target.cmp(&b.target).then(b.attack.cmp(&a.attack)))?;
    Some(decision(index,"wave_five_top_entry_too_close",Decimal::ZERO,json!({"attack":goal.attack,
        "wave_n_date":day(bars,goal.attack),"wave_n_origin_date":day(bars,goal.origin),"wave_five_top_target":goal.target,
        "wave_box_height":goal.box_height,"reference_price":bars[index].close,"wave_remaining_reward":goal.target-bars[index].close,
        "wave_remaining_boxes":(goal.target-bars[index].close)/goal.box_height,"wave_target_known_date":day(bars,goal.known)}),false))
}
fn ten_full_day(bars:&[RuleCandle],index:usize,events:&[Value],state:&mut EntryRiskState,config:&TradingDayConfig)->Option<RuleDecision>{
    for event in events.iter().filter(|e|idx(e,"bar_index")==Some(index)){
        let(Some(attack),Some(origin))=(idx(event,"attack"),idx(event,"origin_index"))else{continue;};if !(origin<attack&&attack<=index){continue;}
        let identity=key(attack,Some(origin));if let Some(low)=positive(event,"b_low"){state.recent_b.insert(identity.clone(),low);}
        if event["event"]!="wave_projection_target_reached"||event["reached_stage"]!="ten_full"||state.pauses.contains_key(&identity)||state.resolved.contains(&identity){continue;}
        let origin_price=positive(event,"a_origin");let previous_b=state.recent_b.get(&identity).copied();
        let base=if config.ten_full_retracement_anchor=="origin"{origin_price}else{previous_b.or(origin_price)};
        let Some(base)=base.filter(|base|*base<bars[index].high)else{continue;};let peak=bars[index].high;
        let anchor_source=if config.ten_full_retracement_anchor=="origin"{"origin"}else if previous_b.is_some(){"b_low"}else{"origin_no_b"};
        state.pauses.insert(identity,Pause{attack,origin,reached:index,peak,anchor:base,anchor_source:anchor_source.into(),
            threshold:peak-(peak-base)*config.ten_full_retracement_ratio,half:peak-(peak-base)/Decimal::TWO});
    }
    if let Some(previous)=index.checked_sub(1){let bar=&bars[previous];let mut resolved=vec![];
        for(identity,pause)in &state.pauses{
            if previous<=pause.reached{continue;}let elapsed=previous-pause.reached;
            let minimum=state.minimum_closes.entry(identity.clone()).or_insert(bar.close);*minimum=(*minimum).min(bar.close);
            let correction=state.corrections.entry(identity.clone()).or_insert((bar.low,previous,*minimum));
            if bar.low<correction.0{*correction=(bar.low,previous,*minimum);}
            let b_duration=correction.1-pause.reached;let a_duration=pause.reached-pause.origin;
            if correction.0<=pause.threshold || config.ten_full_timed_half_retracement&&b_duration>a_duration&&correction.2<=pause.half
                || elapsed<=config.ten_full_breakout_window&&bar.high>pause.peak{resolved.push(identity.clone());}
        }
        for identity in resolved{state.pauses.remove(&identity);state.resolved.insert(identity);}
    }
    let pause=state.pauses.values().max_by_key(|p|(p.reached,p.attack))?;
    let correction=state.corrections.get(&key(pause.attack,Some(pause.origin)));
    Some(decision(index,"wave_ten_full_pullback_pending",Decimal::ZERO,json!({"attack":pause.attack,"wave_n_date":day(bars,pause.attack),
        "wave_n_origin_date":day(bars,pause.origin),"wave_ten_full_reached_date":day(bars,pause.reached),"wave_ten_full_high":pause.peak,
        "wave_retracement_anchor":pause.anchor,"wave_retracement_anchor_source":pause.anchor_source,"wave_retracement_ratio":config.ten_full_retracement_ratio,
        "wave_retracement_threshold":pause.threshold,"wave_half_retracement_threshold":pause.half,"wave_timed_half_retracement":config.ten_full_timed_half_retracement,
        "wave_a_duration":pause.reached-pause.origin,"wave_b_duration":correction.map_or(0,|c|c.1-pause.reached),
        "wave_b_low":correction.map(|c|c.0),"wave_b_low_date":correction.map(|c|day(bars,c.1)),"wave_b_minimum_close":correction.map(|c|c.2),
        "wave_breakout_window":config.ten_full_breakout_window,"wave_breakout_sessions_elapsed":index-pause.reached}),false))
}
fn accepted(event:&Value)->bool{matches!(event["event"].as_str(),Some("wave_projection_ready"|"wave_projection_invalidated"|"wave_projection_stack"|
    "wave_projection_push"|"wave_projection_pullback"|"wave_projection_target_reached"|"wave_n_target_reached"))}
fn same_bar(a:&RuleCandle,b:&RuleCandle)->bool{a.symbol==b.symbol&&a.timestamp==b.timestamp&&a.open==b.open&&a.high==b.high&&a.low==b.low&&a.close==b.close&&a.volume==b.volume&&a.adjustment_factor==b.adjustment_factor&&a.raw_trading_active==b.raw_trading_active&&a.raw_is_st==b.raw_is_st}
pub fn evaluate_entry_risks(bars:&[RuleCandle],index:usize,events:&[Value],state:&mut EntryRiskState,config:&TradingDayConfig)->Result<EntryRisks,String>{
    if index>=bars.len()||state.start_index>index||state.last.is_some_and(|last|index<last){return Err("entry risk requires a nondecreasing available source date".into());}
    config.validate()?;
    if state.config.as_ref().is_some_and(|old|old!=config){return Err("entry risk policy changed inside source history".into());}
    if bars.len()<state.bars.len()||state.bars.iter().zip(bars).any(|(a,b)|!same_bar(a,b)){return Err("entry risk market changed; create new request state".into());}
    if state.bars.len()<bars.len(){super::validate_bars(&bars[state.bars.len().saturating_sub(1)..])?;state.bars.extend_from_slice(&bars[state.bars.len()..]);}
    let values:Vec<_>=events.iter().filter(|e|accepted(e)&&idx(e,"bar_index").is_some_and(|i|state.start_index<=i&&i<=index)).cloned().collect();
    let consumed:Vec<_>=state.events.iter().filter(|e|state.last.is_some_and(|last|idx(e,"bar_index").is_some_and(|i|i<=last))).collect();
    let revised:Vec<_>=values.iter().filter(|e|state.last.is_some_and(|last|idx(e,"bar_index").is_some_and(|i|i<=last))).collect();
    if state.config.is_none()||consumed!=revised{state.reset();}
    state.config=Some(config.clone());state.events=values.clone();
    for now in state.last.map_or(state.start_index,|i|i+1)..=index{
        let target=holding_day(bars,now,&values,state,true)?;let five_top=five_top_day(bars,now,&values,state);let ten_full=ten_full_day(bars,now,&values,state,config);
        let mut output=EntryRisks{target,five_top,ten_full,events:vec![]};
        for(channel,risk)in[("target",&output.target),("five_top",&output.five_top),("ten_full",&output.ten_full)]{if let Some(risk)=risk{
            output.events.push(json!({"event":"entry_risk_observed","bar_index":now,"timestamp":bars[now].timestamp,"channel":channel,"reason":risk.reason,"decision":risk}));}}
        state.history.insert(now,output);state.last=Some(now);state.processed_days+=1;
    }
    state.history.get(&index).cloned().ok_or_else(||"entry risk history missing".into())
}
pub fn holding_resistance_history(bars:&[RuleCandle],events:&[Value],reduction_fraction:Decimal)->Result<BTreeMap<usize,RuleDecision>,String>{
    resistance_history(bars,events,reduction_fraction,true)
}
pub fn two_t_resistance_history(bars:&[RuleCandle],events:&[Value],reduction_fraction:Decimal)->Result<BTreeMap<usize,RuleDecision>,String>{
    resistance_history(bars,events,reduction_fraction,false)
}
fn resistance_history(bars:&[RuleCandle],events:&[Value],reduction_fraction:Decimal,independent:bool)->Result<BTreeMap<usize,RuleDecision>,String>{
    if reduction_fraction<=Decimal::ZERO||reduction_fraction>=Decimal::ONE{return Err("reduction fraction must be between zero and one".into());}
    super::validate_bars(bars)?;let mut state=EntryRiskState::default();state.reduction_fraction=Some(reduction_fraction);let mut result=BTreeMap::new();
    let values:Vec<_>=events.iter().filter(|e|accepted(e)&&idx(e,"bar_index").is_some_and(|i|i<bars.len())).cloned().collect();
    for index in 0..bars.len(){if let Some(risk)=holding_day(bars,index,&values,&mut state,independent)?{result.insert(index,risk);}}Ok(result)
}
pub fn five_top_entry_history(bars:&[RuleCandle],events:&[Value])->Result<BTreeMap<usize,RuleDecision>,String>{
    super::validate_bars(bars)?;let mut state=EntryRiskState::default();let mut result=BTreeMap::new();
    for index in 0..bars.len(){if let Some(risk)=five_top_day(bars,index,events,&mut state){result.insert(index,risk);}}Ok(result)
}
pub fn ten_full_entry_history(bars:&[RuleCandle],events:&[Value],config:&TradingDayConfig)->Result<BTreeMap<usize,RuleDecision>,String>{
    config.validate()?;super::validate_bars(bars)?;let mut state=EntryRiskState::default();let mut result=BTreeMap::new();
    for index in 0..bars.len(){if let Some(risk)=ten_full_day(bars,index,events,&mut state,config){result.insert(index,risk);}}Ok(result)
}
