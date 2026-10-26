//! 资格之前的正N轧空观察：真实折线顶点→N→量价响应→冻结B，独立于买点筛选。
use super::{RuleCandle,Direction,NSetup,NOptions,PivotRef,BoxAnchorMode,MilestoneBasis,observe_n,targets,entries,squeeze,confirmation::{self,AlternationProof},config::TradingDayConfig};
use super::super::{kernel::{KernelPoint,PointKind},market_state::{regime::{self,MarketRegime,RegimePolicy,WaveBoundary},control,price_action::ShadowPolicy}};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
fn value(p:&KernelPoint)->Result<Decimal,String>{p.value.to_string().parse().map_err(|_|"N alternation anchor price is outside decimal range".into())}
fn candidate_proof(bars:&[RuleCandle],now:usize,origin:&KernelPoint,high:&KernelPoint,key:&KernelPoint,vertices:&[KernelPoint;3],
    attack:usize,level:u8,path:&str,gap:Option<&Value>,confirmed_regime:Option<MarketRegime>,resumed:bool)->Result<Option<Value>,String>{
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("candidate_proof");

    let[a,_,c]=vertices;let known=vertices.iter().map(|p|p.known_index).max().unwrap_or(0);let anchor_known=origin.known_index.max(high.known_index).max(key.known_index);
    if !(high.index<a.index&&a.index<c.index&&c.index<=attack&&attack<=now&&now<bars.len()&&anchor_known<=attack&&known<=now){return Ok(None);}
    if !matches!(confirmed_regime,Some(MarketRegime::Bull|MarketRegime::StrongBull))&&!resumed&&gap.is_none(){return Ok(None);}
    let(start,peak,end)=(origin.index,high.index,c.index);let(low,top)=(value(origin)?,value(high)?);
    if !(start<peak&&peak<end)||top<=low||bars[start+1..=peak].iter().any(|b|b.low<low){return Ok(None);}
    let bottom=(peak+1..=end).min_by_key(|&i|(bars[i].low,i)).ok_or("B interval empty")?;let minimum=bars[bottom].low;
    if minimum<low||minimum>=top||bottom>a.index||bars[bottom..=now].iter().any(|b|b.low<minimum)||bars[bottom..attack].iter().any(|b|b.close>top){return Ok(None);}
    let close_index=(peak+1..=bottom).min_by_key(|&i|(bars[i].close,i)).ok_or("B close interval empty")?;let close=bars[close_index].close;let amplitude=top-low;
    let price_path=(top-minimum)*Decimal::from(3)<=amplitude*Decimal::TWO;let time_path=bottom-peak>peak-start&&(top-close)*Decimal::TWO>amplitude;
    let deep=!price_path&&!time_path&&(top-minimum)*Decimal::from(3)>=amplitude*Decimal::TWO;
    if !(price_path||time_path||deep){return Ok(None);}if deep&&bars[attack..now].iter().map(|b|b.high).max().is_some_and(|record|bars[now].close<=record){return Ok(None);}
    let short=(top-close)*Decimal::from(3)<amplitude*Decimal::TWO&&(bottom-peak)*2<peak-start;
    let volume=gap.is_none()&&confirmed_regime.is_some()&&attack>0&&attack<now&&bars[now].close>bars[attack].close
        &&entries::squeeze_bullish_body(&bars[now],Some(&bars[now-1]))&&entries::squeeze_buy_rejection(&bars[now],Some(&bars[now-1]),Some(&bars[attack-1])).is_none();
    let breakout=(attack..=now).find(|&i|bars[i].close>top);
    if short&&!volume&&breakout.is_none(){return Ok(None);}
    let mut proof=json!({"event":"squeeze_alternation_confirmed","bar_index":now,"candidate_index":now,"trend_level":level,"source_path":path,
        "attack":attack,"n_origin":a.index,"n_neckline":vertices[1].index,"n_pullback":c.index,"regime":confirmed_regime.unwrap_or(MarketRegime::Bull),
        "a_origin_index":start,"a_high_index":peak,"b_low_index":bottom,"a_origin_price":low,"a_high_price":top,"b_low_price":minimum,
        "b_minimum_close":close,"b_minimum_close_index":close_index,"two_thirds_price":top-amplitude*Decimal::TWO/Decimal::from(3),
        "half_price":top-amplitude/Decimal::TWO,"retracement_ratio":(top-minimum)/amplitude,"a_duration":peak-start,"b_duration":bottom-peak,
        "duration_unit":"trading_bars","price_path":price_path,"time_path":time_path,"deep_price_path":deep,"anchor_known_index":anchor_known,
        "key_source_index":key.index,"key_price":value(key)?});
    if let Some(gap)=gap{proof["confirmation_source"]=json!("defended_n_consolidation_gap");proof["gap_confirmation_index"]=json!(now);
        proof["gap_previous_high"]=gap.get("gap_previous_high").cloned().unwrap_or(Value::Null);proof["gap_low"]=gap.get("gap_low").cloned().unwrap_or(Value::Null);}
    if short{proof["duration_confirmation"]=json!(if volume{"volume_bullish_n_squeeze"}else{"close_above_a_high_after_short_pullback"});}
    Ok(Some(proof))
}
pub fn confirm_n_squeeze_alternation(bars:&[RuleCandle],now:usize,origin:&KernelPoint,high:&KernelPoint,key:&KernelPoint,
    polyline:&[KernelPoint],pair:Option<(&KernelPoint,&KernelPoint)>,level:u8,path:&str,config:&TradingDayConfig,after_index:Option<usize>,allow_gap:bool)->Result<Option<AlternationProof>,String>{
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("n_confirmation");

    if now>=bars.len()||level>3||path.is_empty()||!(origin.index<high.index&&high.index<=now)||after_index.is_some_and(|i|i>=now){return Ok(None);}
    if [origin,high,key].into_iter().chain(polyline).any(|p|p.known_index>now){return Err("N alternation requires known causal points".into());}
    let origin_price=value(origin)?;
    if bars[origin.index+1..=now].iter().any(|b|b.low<origin_price){return Ok(None);}
    let earliest=(high.index+1).max(now.saturating_sub(config.structure_window));let points:Vec<_>=polyline.iter().filter(|p|p.index>=earliest&&p.is_closed()).cloned().collect();
    let mut triples=points.windows(3).filter(|v|[v[0].kind,v[1].kind,v[2].kind]==[PointKind::L,PointKind::H,PointKind::L]).map(|v|[v[0].clone(),v[1].clone(),v[2].clone()]).collect::<Vec<_>>();
    for folded in targets::folded_n_candidates(&points,earliest,Direction::Up,true){if !triples.contains(&folded){triples.push(folded);}}
    for vertices in triples{let[a,b,c]=&vertices;let known=a.known_index.max(b.known_index).max(c.known_index);
        if now-a.index>config.structure_window||now>known.saturating_add(config.pattern_ttl){continue;}
        let mother=a.index==b.index&&b.index<c.index&&a.ordinal<b.ordinal;let same=a.index<b.index&&b.index==c.index&&b.ordinal<c.ordinal;
        if !(a.index<b.index&&b.index<c.index||mother||same){continue;}let inside=same&&b.index>0&&super::teaching_inside(&bars[b.index-1],&bars[b.index]);
        let setup=NSetup{symbol:bars[0].symbol.clone(),timeframe:"1d".into(),direction:Direction::Up,origin:PivotRef{index:a.index,confirmed_index:a.known_index,ordinal:None},
            neckline:PivotRef{index:b.index,confirmed_index:b.known_index,ordinal:None},pullback:PivotRef{index:c.index,confirmed_index:c.known_index,ordinal:None},source:"lecture_causal".into(),
            box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,allow_confirmation_bar:true,allow_outside_close:true,allow_mother_impulse:mother,staged_defense:true,
            allow_mother_pullback:same&&!inside,allow_inside_pullback:inside,polyline_structure:false};
        let Ok(n)=observe_n(bars,&setup,&NOptions{timeframe:"1d".into(),milestone_basis:MilestoneBasis::Extreme,asof_index:Some(now),measurement_start:None,measure_targets:false})else{continue;};
        let Some(completion)=&n.completion else{continue;};let attack=completion.bar_index;if after_index.is_some_and(|i|attack.max(known)<=i){continue;}
        let shadow=Some(ShadowPolicy{minimum_range_fraction:config.shadow_fraction});
        if config.strict_n_attack_quality{let control=control::observe_prepared_control(bars,&setup,config.volume_lookback,shadow,None,&n)?;
            if entries::v3_positive_n_attack_rejection(&bars[attack],control.volume.as_ref().map(|v|v.increased_from_previous)).is_some(){continue;}}
        let gap=if allow_gap{squeeze::consolidation_gap(bars,attack,now,completion.defense)}else{None};
        if gap.is_some(){if let Some(proof)=candidate_proof(bars,now,origin,high,key,&vertices,attack,level,path,gap.as_ref(),None,false)?{
            if let Some(low)=polyline.iter().find(|p|p.kind==PointKind::L&&Some(p.index)==super::qualification::index(&proof,"b_low_index")&&value(p).ok()==super::qualification::price(&proof,"b_low_price")){
                return Ok(Some(AlternationProof{low:low.clone(),rule:"positive_n_consolidation_gap_after_qualified_b".into(),evidence:proof}));}}}
        let history=regime::observe_prepared_regime(bars,&setup,&RegimePolicy{shadow_policy:shadow,wave_boundary:WaveBoundary::NOrigin,local_resistance_failure:true},None,&n)?;
        let Some(frame)=history.frames.last()else{continue;};let reversal=n.anchors.as_ref().and_then(|a|regime::volume_reversal_squeeze(bars,attack,now,a.origin,completion.defense,frame,0));
        let confirmed=if reversal.is_some(){Some(MarketRegime::Bull)}else{frame.regime};
        if matches!(confirmed,Some(MarketRegime::Bull|MarketRegime::StrongBull)){
            if let Some(mut local)=confirmation::confirm_volume_breakout_alternation(bars,now,origin,high,pair,true)?{
                if attack>0&&attack<now&&bars[now].close>bars[attack].close&&entries::squeeze_buy_rejection(&bars[now],Some(&bars[now-1]),Some(&bars[attack-1])).is_none(){
                    local.rule="positive_n_and_squeeze_after_qualified_b".into();if let Some(e)=local.evidence.as_object_mut(){e.extend(json!({"confirmation_source":"positive_n_squeeze_polyline_key_breakout",
                        "bar_index":now,"candidate_index":now,"attack":attack,"n_origin":a.index,"n_neckline":b.index,"n_pullback":c.index,"source_path":path,"trend_level":level}).as_object().ok_or("N proof must be object")?.clone());}return Ok(Some(local));}}
            if let Some(mut whole)=confirmation::confirm_double_n_pullback(bars,now,attack,origin,high,polyline)?{if let Some(e)=whole.evidence.as_object_mut(){e.extend(json!({"n_origin":a.index,"n_neckline":b.index,"n_pullback":c.index,"source_path":path,"trend_level":level}).as_object().ok_or("whole N proof must be object")?.clone());}return Ok(Some(whole));}
        }
        let resumed=!matches!(confirmed,Some(MarketRegime::Bull|MarketRegime::StrongBull))&&config.squeeze_pullback_entries&&regime::observe_squeeze_resumption(bars,now,frame,0,attack,completion.defense)?.is_some();
        if let Some(proof)=candidate_proof(bars,now,origin,high,key,&vertices,attack,level,path,None,confirmed,resumed)?{
            if let Some(low)=polyline.iter().find(|p|p.kind==PointKind::L&&Some(p.index)==super::qualification::index(&proof,"b_low_index")&&value(p).ok()==super::qualification::price(&proof,"b_low_price")){
                return Ok(Some(AlternationProof{low:low.clone(),rule:"positive_n_and_squeeze_after_qualified_b".into(),evidence:proof}));}}
    }Ok(None)
}

/// 只验证已锁定 N 的外层交替条件；不再枚举候选或重跑 N/轧空历史。
pub fn confirm_single_n(bars:&[RuleCandle],now:usize,origin:&KernelPoint,high:&KernelPoint,key:&KernelPoint,
    polyline:&[KernelPoint],n:&super::single_n::SingleNData,level:u8,path:&str,after:Option<usize>)->Result<Option<AlternationProof>,String>{
    confirm_single_n_snapshot(bars,now,origin,high,key,polyline,n,crate::analysis::kernel::n_memory::NConfirmationSnapshot::of(n),level,path,after)
}
pub(crate) fn confirm_single_n_snapshot(bars:&[RuleCandle],now:usize,origin:&KernelPoint,high:&KernelPoint,key:&KernelPoint,
    polyline:&[KernelPoint],n:&super::single_n::SingleNData,snapshot:crate::analysis::kernel::n_memory::NConfirmationSnapshot,level:u8,path:&str,after:Option<usize>)->Result<Option<AlternationProof>,String>{
    if n.direction!=super::Direction::Up||now>=bars.len()||n.known_index>now{return Ok(None);}
    let (Some(attack),Some(defense),Some(squeeze))=(snapshot.attack,snapshot.defense,snapshot.squeeze)else{return Ok(None);};
    if attack>now||squeeze>now{return Ok(None);}
    if bar_low_broken(&bars[now],defense)||after.is_some_and(|i|squeeze<=i)||n.a.bar_index<=high.index{return Ok(None);}
    let make=|r:&super::single_n::NPointRef,kind,value|KernelPoint::new(crate::analysis::kernel::KernelPointData{
        index:r.bar_index,ordinal:r.ordinal,known_index:r.known_index,kind,value,time:bars[r.bar_index].timestamp.clone(),
        level:0,path:origin.path,state:crate::analysis::kernel::PointState::Confirmed,evidence:Value::Null});
    use rust_decimal::prelude::ToPrimitive;
    let vertices=[make(&n.a,PointKind::L,n.origin.to_f64().ok_or("N origin unavailable")?),
        make(&n.b,PointKind::H,n.neckline.to_f64().ok_or("N neckline unavailable")?),
        make(&n.c,PointKind::L,n.pullback.to_f64().ok_or("N pullback unavailable")?)];
    let Some(proof)=candidate_proof(bars,now,origin,high,key,&vertices,attack,level,path,None,snapshot.regime,false)? else{return Ok(None);};
    if let Some(low)=polyline.iter().find(|p|p.kind==PointKind::L&&Some(p.index)==super::qualification::index(&proof,"b_low_index")
        &&value(p).ok()==super::qualification::price(&proof,"b_low_price")){
        return Ok(Some(AlternationProof{low:low.clone(),rule:"single_n_and_squeeze_after_qualified_b".into(),evidence:proof}));
    }Ok(None)
}
fn bar_low_broken(bar:&RuleCandle,defense:Decimal)->bool{bar.low<defense}
