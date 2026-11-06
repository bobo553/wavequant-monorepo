//! 正 N 轧空与倒 N 轧多共用的单候选状态机；只消费新增折线点和当日 K 线。
use std::collections::VecDeque;
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::analysis::{kernel::{KernelPoint,PointKind},market_state::{regime::MarketRegime,price_action::{ShadowPolicy,observe_resistance},strong_bullish_candle}};
use super::{RuleCandle,validate_bars,n_shape::{project_n_targets,ValueDomain},Direction};

#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
/// source_index 属于 path 对应的折线点数组；冻结行情坐标防止修订后误读位置。
pub struct NPointRef {pub path:usize,pub source_index:usize,pub bar_index:usize,pub ordinal:usize,pub known_index:usize}
impl NPointRef {fn new(source_index:usize,p:&KernelPoint)->Self{Self{path:p.path,source_index,bar_index:p.index,ordinal:p.ordinal,known_index:p.known_index}}}
fn upward()->Direction{Direction::Up}
fn sign(d:Direction)->Decimal{if d==Direction::Up{Decimal::ONE}else{-Decimal::ONE}}
fn extreme(b:&RuleCandle,d:Direction)->Decimal{if d==Direction::Up{b.high}else{b.low}}
fn adverse(b:&RuleCandle,d:Direction)->Decimal{if d==Direction::Up{b.low}else{b.high}}
fn ahead(a:Decimal,b:Decimal,d:Direction)->bool{sign(d)*(a-b)>Decimal::ZERO}
fn farther(a:Decimal,b:Decimal,d:Direction)->Decimal{if ahead(a,b,d){a}else{b}}
fn nearer(a:Decimal,b:Decimal,d:Direction)->Decimal{if ahead(a,b,d){b}else{a}}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct SingleNData {
    #[serde(default="upward")] pub direction:Direction,
    pub a:NPointRef,pub b:NPointRef,pub c:NPointRef,pub origin:Decimal,pub neckline:Decimal,
    pub neckline_close:Decimal,pub pullback:Decimal,pub known_index:usize,
    pub attack_index:Option<usize>,pub defense:Option<Decimal>,pub box_anchor:Option<Decimal>,
    pub squeeze_index:Option<usize>,pub regime:Option<MarketRegime>,pub targets:Option<SingleNTargets>,
}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct SingleNTargets {pub one_p:Decimal,pub two_t:Decimal,pub five_top:Option<Decimal>,pub ten_full:Option<Decimal>,pub stage:String,pub next_target:Option<Decimal>,pub one_p_reached_at:Option<usize>,pub two_t_reached_at:Option<usize>}
#[derive(Debug,Clone,Default)]
pub struct SingleNState {
    direction:Option<Direction>,start_order:Option<(usize,usize)>,start:usize,floor:usize,cursor:usize,tail:VecDeque<(usize,PointStamp)>,last:Option<usize>,previous_index:Option<usize>,source_versions:Option<[u64;3]>,
    #[cfg(test)] previous:Option<RuleCandle>,
    stage_defense:Option<Decimal>,extension_origin:Option<Decimal>,pub candidate:Option<SingleNData>,pub(crate) retired:Option<SingleNData>,pub evaluated_days:usize,pub formations:usize,pub invalidations:usize,
}
impl SingleNState {
    /// 资格折叠 N 使用同一个增量观察器；ABC 来源冻结后不再枚举历史。
    pub(crate) fn from_candidate(data:SingleNData,points:&[KernelPoint],now:usize)->Self{
        let versions=[&data.a,&data.b,&data.c].map(|p|points[p.source_index].reference_id());
        Self{direction:Some(data.direction),start:data.a.bar_index,floor:data.a.bar_index,cursor:points.len(),
            last:Some(now),previous_index:Some(now),source_versions:Some(versions),candidate:Some(data),..Self::default()}
    }
    pub(crate) fn last_day(&self)->Option<usize>{self.last}
    /// 延迟订阅只补齐当前ABC可知后的短后缀，恢复首次突破，不能把接管日当作攻击日。
    pub(crate) fn rewind_new_candidate(&mut self){
        let Some(n)=self.candidate.as_mut()else{return;};
        if self.last.is_none_or(|last|last<=n.known_index)||n.squeeze_index.is_some(){return;}
        n.attack_index=None;n.defense=None;n.box_anchor=None;n.targets=None;
        self.last=n.known_index.checked_sub(1);self.previous_index=self.last;self.stage_defense=None;
    }
    /// 未突破的回撤延伸只撤销 C；冻结 A/B 后等待新的已确认 C，不重扫历史。
    /// 已攻击、起点破位或来源修订则开始全新的搜索，不能复活旧 N。
    pub(crate) fn after_invalidation(n:&SingleNData,points:&[KernelPoint],prices:&[Decimal],bar:&RuleCandle,now:usize)->Self{
        let mut next=Self::from_origin(now,0,n.direction);
        if n.attack_index.is_some()||!ahead(adverse(bar,n.direction),n.origin,n.direction){return next;}
        for (r,value) in [(&n.a,n.origin),(&n.b,n.neckline)]{
            let Some(p)=points.get(r.source_index)else{return next;};
            if p.path!=r.path||p.index!=r.bar_index||p.ordinal!=r.ordinal||p.known_index!=r.known_index
                ||prices.get(r.source_index)!=Some(&value){return next;}
        }
        next.start=n.a.bar_index;next.start_order=Some((n.a.bar_index,n.a.ordinal));
        // 包含线可在同日先延伸 C 再突破 B；新候选必须消费当天，而非跳到下一日。
        next.cursor=points.partition_point(|p|p.index<now);next.last=now.checked_sub(1);next.previous_index=None;
        next.extension_origin=Some(n.origin);
        next.tail=[n.a.source_index,n.b.source_index].into_iter().map(|i|(i,PointStamp::of(&points[i]))).collect();
        next
    }
    pub fn new(start:usize)->Self{Self::directional(start,Direction::Up)}
    pub fn directional(start:usize,direction:Direction)->Self{Self{direction:Some(direction),start,floor:start.saturating_add(1),..Self::default()}}
    pub fn from_origin(index:usize,ordinal:usize,direction:Direction)->Self{Self{direction:Some(direction),start_order:Some((index,ordinal)),start:index,floor:index,..Self::default()}}
    pub fn restart(&mut self,start:usize){*self=Self::directional(start,self.direction.unwrap_or(Direction::Up));}
    pub fn retire(&mut self,now:usize){self.retired=self.candidate.take();self.source_versions=None;self.stage_defense=None;self.tail.clear();self.floor=now;self.invalidations+=1;}
}
#[derive(Debug,Clone)]
pub struct SingleNStep {#[cfg(test)] pub data:Option<SingleNData>,pub invalidated:bool,pub formed:bool}
#[derive(Debug,Clone,Copy,PartialEq)]
struct PointStamp{index:usize,ordinal:usize,path:usize,known:usize,kind:PointKind,value:f64}
impl PointStamp{fn of(p:&KernelPoint)->Self{Self{index:p.index,ordinal:p.ordinal,path:p.path,known:p.known_index,kind:p.kind,value:p.value}}}
#[cfg(test)]
fn price(p:&KernelPoint)->Result<Decimal,String>{p.value.to_string().parse().map_err(|_|"N point price outside decimal range".into())}
/// 冻结来源价格一次准备；当前和昨日行情只通过数组下标读取。
pub(crate) fn advance_n_indexed(state:&mut SingleNState,points:&[KernelPoint],prices:&[Decimal],bars:&[RuleCandle],now:usize)->Result<SingleNStep,String>{
    let bar=bars.get(now).ok_or("N bar outside market array")?;
    let previous=state.previous_index.or_else(||now.checked_sub(1)).and_then(|index|bars.get(index));
    let result=advance_n_core(state,points,bar,now,previous,|index|bars.get(index).map(|bar|bar.close),|index,_|prices.get(index).copied().ok_or_else(||"N point price cache missing".into()))?;
    state.previous_index=Some(now);Ok(result)
}
#[cfg(test)]
pub fn advance_n(state:&mut SingleNState,points:&[KernelPoint],bar:&RuleCandle,now:usize,previous_bar:Option<&RuleCandle>,source_close:impl Fn(usize)->Option<Decimal>)->Result<SingleNStep,String>{
    let previous=state.previous.take().or_else(||previous_bar.cloned());
    let mut result=advance_n_core(state,points,bar,now,previous.as_ref(),source_close,|_,point|price(point))?;
    state.previous=Some(bar.clone());result.data=state.candidate.clone();Ok(result)
}
/// source_close 只在新候选冻结颈线时按下标读取一次；不接受或复制历史行情数组。
fn advance_n_core(state:&mut SingleNState,points:&[KernelPoint],bar:&RuleCandle,now:usize,previous_bar:Option<&RuleCandle>,
    source_close:impl Fn(usize)->Option<Decimal>,source_price:impl Fn(usize,&KernelPoint)->Result<Decimal,String>)->Result<SingleNStep,String>{
    #[cfg(feature="qualification-timing")]
    let _profile=crate::analysis::kernel::qualification_profile::enter("single_n_step");
    if state.last.is_some_and(|last|now<=last){return Err("single N requires increasing observation dates".into());}
    validate_bars(std::slice::from_ref(bar))?;let direction=state.direction.unwrap_or(Direction::Up);
    if state.last.is_none(){state.cursor=points.partition_point(|p|state.start_order.map_or(p.index<=state.start,|order|p.order()<order));}
    state.evaluated_days+=1;state.last=Some(now);
    let mut invalidated=false;let mut formed=false;
    if let Some(n)=&state.candidate {
        let refs=[&n.a,&n.b,&n.c];let versions=refs.map(|r|points.get(r.source_index).map(KernelPoint::reference_id));
        if state.source_versions.map(|ids|ids.map(Some))!=Some(versions){
            let unchanged=[(&n.a,n.origin),(&n.b,n.neckline),(&n.c,n.pullback)].iter().all(|(r,value)|points.get(r.source_index).is_some_and(|p|
                p.path==r.path&&p.index==r.bar_index&&p.ordinal==r.ordinal&&p.known_index==r.known_index&&source_price(r.source_index,p).ok()==Some(*value)));
            if !unchanged{state.retire(now);state.cursor=0;invalidated=true;}
            else{state.source_versions=Some(versions.map(|id|id.unwrap_or(0)));}
        }
    }
    if state.tail.iter().any(|(index,stamp)|points.get(*index).is_none_or(|p|PointStamp::of(p)!=*stamp)){
        state.tail.clear();state.cursor=0;state.floor=now;state.extension_origin=None;
    }
    if state.extension_origin.is_some_and(|origin|!ahead(adverse(bar,direction),origin,direction)){
        state.tail.clear();state.floor=now;state.extension_origin=None;
    }
    if state.cursor>points.len(){state.cursor=0;state.tail.clear();state.floor=now;}
    if let Some(n)=&state.candidate {
        let observed=if n.attack_index.is_some(){bar.close}else{adverse(bar,direction)};
        if ahead(n.defense.unwrap_or(n.pullback),observed,direction){state.retire(now);invalidated=true;}
    }
    if !invalidated&&state.candidate.is_none(){
        while state.cursor<points.len(){
            let p=&points[state.cursor];if p.known_index>now{break;}state.cursor+=1;
            if state.start_order.map_or(p.index<=state.start,|order|p.order()<order)||p.index<state.floor||!p.is_closed(){continue;}
            // 冻结 A/B 等待 C 时，B 可继续创极值；同类点不能把 A 挤出三点窗口。
            if let Some((_,last))=state.tail.back().filter(|(_,last)|last.kind==p.kind){
                if p.kind.sign()*(p.value-last.value)<=0.0{continue;}
                state.tail.pop_back();
            }
            state.tail.push_back((state.cursor-1,PointStamp::of(p)));while state.tail.len()>3{state.tail.pop_front();}
            if state.tail.len()!=3{continue;}
            let (a,b,c)=(&points[state.tail[0].0],&points[state.tail[1].0],&points[state.tail[2].0]);
            let (base,peak)=if direction==Direction::Up{(PointKind::L,PointKind::H)}else{(PointKind::H,PointKind::L)};
            if [a.kind,b.kind,c.kind]!=[base,peak,base]
                ||!(a.order()<b.order()&&b.order()<c.order())||a.path!=b.path||b.path!=c.path{continue;}
            let (low,high,pullback)=(source_price(state.tail[0].0,a)?,source_price(state.tail[1].0,b)?,source_price(state.tail[2].0,c)?);
            if !(ahead(pullback,low,direction)&&ahead(high,pullback,direction)){continue;}
            let close=source_close(b.index).ok_or("N neckline bar unavailable")?;
            if close<=Decimal::ZERO||ahead(close,high,direction){return Err("N neckline close outside bar range".into());}
            state.retired=None;state.extension_origin=None;state.source_versions=Some([a.reference_id(),b.reference_id(),c.reference_id()]);
            state.candidate=Some(SingleNData{direction,a:NPointRef::new(state.tail[0].0,a),b:NPointRef::new(state.tail[1].0,b),c:NPointRef::new(state.tail[2].0,c),origin:low,neckline:high,neckline_close:close,
                pullback,known_index:a.known_index.max(b.known_index).max(c.known_index),attack_index:None,defense:None,box_anchor:None,
                squeeze_index:None,regime:None,targets:None});state.tail.clear();break;
        }
    }
    if let Some(n)=&mut state.candidate{
        if n.attack_index.is_none(){
            if ahead(n.pullback,adverse(bar,direction),direction){state.retire(now);invalidated=true;}
            else if let Some(previous)=previous_bar{
                let first=!ahead(n.neckline,extreme(bar,direction),direction)||ahead(bar.close,n.neckline_close,direction);
                if state.stage_defense.is_none()&&first{state.stage_defense=Some(nearer(previous.close,adverse(bar,direction),direction));}
                else if let Some(defense)=&mut state.stage_defense{*defense=nearer(*defense,adverse(bar,direction),direction);}
                let jointly_above=ahead(extreme(bar,direction),n.neckline,direction)&&ahead(bar.close,n.neckline_close,direction);
                let previous_joint=ahead(extreme(previous,direction),n.neckline,direction)&&ahead(previous.close,n.neckline_close,direction);
                if jointly_above&&!previous_joint&&now>=n.known_index{
                    n.attack_index=Some(now);n.defense=Some(state.stage_defense.unwrap_or(nearer(previous.close,adverse(bar,direction),direction)));
                    n.box_anchor=Some(farther(previous.close,extreme(bar,direction),direction));formed=true;state.formations+=1;
                }
            }
        }
    }
    Ok(SingleNStep{#[cfg(test)] data:None,invalidated,formed})
}

#[derive(Debug,Clone,Default)]
pub struct SqueezeState {
    attack:Option<usize>,previous_index:Option<usize>,before_index:Option<usize>,
    #[cfg(test)] legacy_bars:std::collections::BTreeMap<usize,RuleCandle>,
    last:Option<usize>,record:Option<Decimal>,record_index:usize,first_resistance:Option<usize>,unknown:bool,
    uninterrupted:bool,rolling_all:bool,prior_resistance:bool,prior_index:Option<usize>,projection:Option<super::waves::IncrementalProjection>,
}
/// 使用冻结 N 和当日行情推进轧空；成立后的N须收盘严格反破防守才失效。
pub(crate) fn advance_squeeze_indexed(state:&mut SqueezeState,n:&mut SingleNData,bars:&[RuleCandle],now:usize,shadow_fraction:Decimal)->Result<bool,String>{
    let Some(attack)=n.attack_index else{return Ok(false);};let previous_index=now.checked_sub(1).ok_or("squeeze lacks preceding market bar")?;
    let bar=bars.get(now).ok_or("squeeze outside market array")?;let previous_bar=&bars[previous_index];
    let previous=&bars[state.previous_index.unwrap_or(previous_index)];let before=&bars[state.before_index.unwrap_or(state.previous_index.unwrap_or(previous_index))];
    let attack_bar=bars.get(attack).ok_or("squeeze attack outside market array")?;
    advance_squeeze_core(state,n,bar,previous_bar,previous,before,attack_bar,now,shadow_fraction)
}
#[cfg(test)]
pub fn advance_squeeze(state:&mut SqueezeState,n:&mut SingleNData,bar:&RuleCandle,previous_bar:&RuleCandle,now:usize,shadow_fraction:Decimal)->Result<bool,String>{
    let mut market=std::mem::take(&mut state.legacy_bars);market.insert(now,bar.clone());if let Some(previous)=now.checked_sub(1){market.insert(previous,previous_bar.clone());}
    let previous=market.get(&state.previous_index.unwrap_or(now.saturating_sub(1))).unwrap_or(previous_bar);
    let before=market.get(&state.before_index.unwrap_or(now.saturating_sub(1))).unwrap_or(previous);
    let attack=market.get(&n.attack_index.unwrap_or(now)).unwrap_or(bar);
    let result=advance_squeeze_core(state,n,bar,previous_bar,previous,before,attack,now,shadow_fraction);state.legacy_bars=market;result
}
fn advance_squeeze_core(state:&mut SqueezeState,n:&mut SingleNData,bar:&RuleCandle,previous_bar:&RuleCandle,previous:&RuleCandle,before:&RuleCandle,attack_bar:&RuleCandle,now:usize,shadow_fraction:Decimal)->Result<bool,String>{
    #[cfg(feature="qualification-timing")]
    let _profile=crate::analysis::kernel::qualification_profile::enter("single_squeeze_step");
    let direction=n.direction;
    let (Some(attack),Some(defense))=(n.attack_index,n.defense)else{return Ok(false);};
    if state.attack!=Some(attack){
        let resistance=observe_resistance(previous_bar,bar,direction,Some(ShadowPolicy{minimum_range_fraction:shadow_fraction}))?;
        *state=SelfSqueeze::initial(attack,now,bar,resistance.detected,direction);
        return Ok(false);
    }
    if state.last.is_some_and(|last|now<=last){return Err("squeeze requires increasing market dates".into());}
    if ahead(defense,bar.close,direction){return Ok(false);}
    let resistance=observe_resistance(previous,bar,direction,Some(ShadowPolicy{minimum_range_fraction:shadow_fraction}))?;
    let eligible=now==attack+1;
    if eligible{if resistance.detected==Some(true)&&state.first_resistance.is_none(){state.first_resistance=Some(now);}state.unknown|=resistance.detected.is_none();}
    let rolling=nearer(adverse(previous,direction),before.close,direction);let held=!ahead(rolling,adverse(bar,direction),direction);
    state.rolling_all&=held;state.uninterrupted&=ahead(bar.close,previous.close,direction);
    let record=state.record.unwrap_or(extreme(attack_bar,direction));let mut continuation=ahead(bar.close,record,direction);
    if now>=attack+2&&state.first_resistance.is_some(){
        let body=sign(direction)*(bar.close-bar.open);let shadow=if direction==Direction::Up{bar.high-bar.open.max(bar.close)}else{bar.open.min(bar.close)-bar.low};
        let strong=if direction==Direction::Up{strong_bullish_candle(bar)}else{body>=bar.open*Decimal::new(3,2)&&body>=(bar.high-bar.low)*Decimal::new(6,1)&&shadow<=(bar.high-bar.low)*Decimal::new(2,1)};
        let attack_break=ahead(extreme(bar,direction),extreme(attack_bar,direction),direction)&&ahead(bar.close,attack_bar.close,direction)&&strong;
        let response=state.rolling_all&&held&&state.prior_index.is_some_and(|i|i<=attack+1)&&state.prior_resistance;
        continuation=(continuation||attack_break||response)&&ahead(bar.close,previous.close,direction)&&ahead(bar.close,attack_bar.close,direction)&&ahead(bar.close,bar.open,direction)&&(!eligible||resistance.detected==Some(false));
    }
    let regime=if now>=attack+2&&continuation {
        if state.first_resistance.is_none()&&!state.unknown&&state.uninterrupted&&state.rolling_all{Some(if direction==Direction::Up{MarketRegime::StrongBull}else{MarketRegime::StrongBear})}
        else if state.first_resistance.is_some_and(|i|i<now){Some(if direction==Direction::Up{MarketRegime::Bull}else{MarketRegime::Bear})}else{None}
    }else{None};
    let newly_confirmed=n.squeeze_index.is_none()&&regime.is_some();
    if let Some(regime)=regime{
        n.regime=Some(regime);
        if n.squeeze_index.is_none(){
            n.squeeze_index=Some(now);
            let box_anchor=n.box_anchor.ok_or("N box anchor missing")?;
            let targets=project_n_targets(n.origin,n.neckline,n.pullback,box_anchor,direction,ValueDomain::Price,false)?;
            if let (Some(one_p),Some(two_t))=(targets.one_p,targets.two_t){
            n.targets.get_or_insert(SingleNTargets{one_p,two_t,five_top:None,ten_full:None,stage:"await_one_p".into(),next_target:Some(one_p),one_p_reached_at:None,two_t_reached_at:None});
            state.projection=Some(super::waves::IncrementalProjection::directional(super::waves::WaveProjectionSetup{
                origin_index:n.a.bar_index,attack_index:attack,squeeze_index:now,origin:n.origin,box_anchor,two_t,defense},bar,now,farther(record,extreme(bar,direction),direction),if ahead(extreme(bar,direction),record,direction){now}else{state.record_index},direction)?);
            }
        }
    }
    if let (Some(projection),Some(targets))=(&mut state.projection,&mut n.targets){
        let update=projection.advance(bar,previous,now)?;
        let observed=if n.squeeze_index==Some(now){bar.close}else{extreme(bar,direction)};
        if targets.one_p_reached_at.is_none()&&!ahead(targets.one_p,observed,direction){targets.one_p_reached_at=Some(now);}
        if targets.two_t_reached_at.is_none()&&!ahead(targets.two_t,observed,direction){targets.two_t_reached_at=Some(now);}
        targets.stage=update.stage;targets.next_target=update.target;
        if targets.one_p_reached_at.is_none(){targets.stage="await_one_p".into();targets.next_target=Some(targets.one_p);}
        else if targets.two_t_reached_at.is_none(){targets.stage="await_two_t".into();targets.next_target=Some(targets.two_t);}
        if let Some(price)=update.five_top{targets.five_top=Some(price);}if let Some(price)=update.ten_full{targets.ten_full=Some(price);}
    }
    if ahead(extreme(bar,direction),record,direction){state.record_index=now;}state.record=Some(farther(record,extreme(bar,direction),direction));state.prior_resistance=resistance.detected==Some(true);state.prior_index=Some(now);
    state.before_index=state.previous_index;state.previous_index=Some(now);state.last=Some(now);Ok(newly_confirmed)
}
/// 一饱/二吐由已完成N的冻结攻击箱体决定，不以轧空/杀多确认作为发布门槛。
/// 只维护结构显示事实；资格仍读取 squeeze_index，五顶/十满仍由后续投射生成。
pub(crate) fn advance_structure_targets(n:&mut SingleNData,bar:&RuleCandle,now:usize)->Result<(),String>{
    let Some(attack)=n.attack_index else{return Ok(());};
    if now<attack||now<n.known_index{return Ok(());}
    if now>attack&&n.defense.is_some_and(|defense|ahead(defense,bar.close,n.direction)){return Ok(());}
    if n.targets.is_none(){
        let box_anchor=n.box_anchor.ok_or("completed N box anchor missing")?;
        let projected=project_n_targets(n.origin,n.neckline,n.pullback,box_anchor,n.direction,ValueDomain::Price,false)?;
        let (Some(one_p),Some(two_t))=(projected.one_p,projected.two_t)else{return Ok(());};
        n.targets=Some(SingleNTargets{one_p,two_t,five_top:None,ten_full:None,stage:"await_one_p".into(),next_target:Some(one_p),one_p_reached_at:None,two_t_reached_at:None});
    }
    let targets=n.targets.as_mut().ok_or("completed N targets missing")?;
    // 成立当日不推断突破前的盘中先后；以后才可使用当日有利极值。
    let observed=if now==attack{bar.close}else{extreme(bar,n.direction)};
    if targets.one_p_reached_at.is_none()&&!ahead(targets.one_p,observed,n.direction){targets.one_p_reached_at=Some(now);}
    if targets.two_t_reached_at.is_none()&&!ahead(targets.two_t,observed,n.direction){targets.two_t_reached_at=Some(now);}
    if targets.one_p_reached_at.is_none(){targets.stage="await_one_p".into();targets.next_target=Some(targets.one_p);}
    else if targets.two_t_reached_at.is_none(){targets.stage="await_two_t".into();targets.next_target=Some(targets.two_t);}
    else if n.squeeze_index.is_none(){targets.stage="await_squeeze".into();targets.next_target=None;}
    Ok(())
}

// 单独构造初始状态，避免向外暴露历史行情容器。
struct SelfSqueeze;
impl SelfSqueeze {fn initial(attack:usize,now:usize,bar:&RuleCandle,detected:Option<bool>,direction:Direction)->SqueezeState{
    SqueezeState{attack:Some(attack),previous_index:Some(now),before_index:now.checked_sub(1),#[cfg(test)] legacy_bars:Default::default(),last:Some(now),record:Some(extreme(bar,direction)),record_index:attack,
        first_resistance:(detected==Some(true)).then_some(attack),unknown:detected.is_none(),uninterrupted:true,rolling_all:true,
        prior_resistance:detected==Some(true),prior_index:Some(attack),projection:None}
}}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::kernel::{KernelPointData,PointState};
    use crate::indexed_json::Value;
    fn bar(day:usize,open:&str,high:&str,low:&str,close:&str)->RuleCandle{
        RuleCandle{symbol:"test".into(),timestamp:format!("2026-01-{day:02}"),open:open.parse().unwrap(),high:high.parse().unwrap(),
            low:low.parse().unwrap(),close:close.parse().unwrap(),volume:Decimal::from(100),adjustment_factor:Decimal::ONE,raw_is_st:None,raw_trading_active:None}
    }
    fn points()->Vec<KernelPoint>{[(1,PointKind::L,10.0),(2,PointKind::H,15.0),(3,PointKind::L,12.0)].into_iter().map(|(index,kind,value)|
        KernelPoint::new(KernelPointData{index,kind,value,ordinal:0,known_index:index,level:0,path:0,state:PointState::Confirmed,
            time:format!("2026-01-{index:02}"),evidence:Value::Null})).collect()}
    fn formed()->(SingleNState,Vec<KernelPoint>){
        let points=points();let mut state=SingleNState::new(0);
        let prev=bar(2,"14","15","12","14.5");let current=bar(3,"13","14","12","13");
        advance_n(&mut state,&points,&current,3,Some(&prev),|_|Some(Decimal::new(145,1))).unwrap();
        let attack=bar(4,"14","16","13.5","15.5");
        assert!(advance_n(&mut state,&points,&attack,4,Some(&current),|_|Some(Decimal::new(145,1))).unwrap().formed);
        (state,points)
    }
    #[test]
    fn unbroken_impulse_survives_pending_pullback_extension_in_both_directions(){
        for direction in [Direction::Up,Direction::Down]{
            let mirror=|v:Decimal|if direction==Direction::Up{v}else{Decimal::from(30)-v};
            let reflect=|mut b:RuleCandle|{let high=b.high;b.open=mirror(b.open);b.close=mirror(b.close);
                if direction==Direction::Down{b.high=mirror(b.low);b.low=mirror(high);}b};
            let mut points=points();
            if direction==Direction::Down{points=points.into_iter().map(|p|KernelPoint::new(KernelPointData{index:p.index,kind:if p.kind==PointKind::L{PointKind::H}else{PointKind::L},value:30.0-p.value,
                ordinal:p.ordinal,known_index:p.known_index,level:0,path:0,state:PointState::Confirmed,time:p.time.clone(),evidence:Value::Null})).collect();}
            let mut state=SingleNState::directional(0,direction);
            let previous=reflect(bar(2,"14","15","12","14.5"));let current=reflect(bar(3,"13","14","12","13"));
            advance_n(&mut state,&points,&current,3,Some(&previous),|_|Some(mirror(Decimal::new(145,1)))).unwrap();
            let old=state.candidate.as_ref().unwrap().clone();let extension=reflect(bar(4,"12","14","11","12"));
            assert!(advance_n(&mut state,&points,&extension,4,Some(&current),|_|None).unwrap().invalidated);
            points.push(KernelPoint::new(KernelPointData{index:4,kind:points[0].kind,value:if direction==Direction::Up{11.0}else{19.0},ordinal:0,known_index:5,level:0,path:0,state:PointState::Confirmed,time:"2026-01-04".into(),evidence:Value::Null}));
            let prices=points.iter().map(|p|price(p).unwrap()).collect::<Vec<_>>();
            let mut replacement=SingleNState::after_invalidation(&old,&points,&prices,&extension,4);
            let attack=reflect(bar(5,"14","16","12","15.5"));
            assert!(advance_n(&mut replacement,&points,&attack,5,Some(&extension),|_|Some(mirror(Decimal::new(145,1)))).unwrap().formed);
            let n=replacement.candidate.unwrap();assert_eq!((n.a.bar_index,n.b.bar_index,n.c.bar_index,n.attack_index),(1,2,4,Some(5)));
            let mut same_day_points=points[..3].to_vec();
            same_day_points.push(KernelPoint::new(KernelPointData{index:4,kind:points[0].kind,value:if direction==Direction::Up{11.0}else{19.0},ordinal:0,known_index:4,level:0,path:0,state:PointState::Confirmed,time:"2026-01-04".into(),evidence:Value::Null}));
            let outside=reflect(bar(4,"14","16","11","15.5"));
            let mut replacement=SingleNState::after_invalidation(&old,&same_day_points,&prices,&outside,4);
            assert!(advance_n(&mut replacement,&same_day_points,&outside,4,Some(&current),|_|Some(mirror(Decimal::new(145,1)))).unwrap().formed);
            assert_eq!(replacement.candidate.as_ref().unwrap().attack_index,Some(4));
            let mut attacked=old.clone();attacked.attack_index=Some(3);
            assert!(SingleNState::after_invalidation(&attacked,&points,&prices,&extension,4).tail.is_empty());
            let broken=reflect(bar(4,"11","14","9","11"));
            assert!(SingleNState::after_invalidation(&old,&points,&prices,&broken,4).tail.is_empty());
        }
    }
    #[test]
    fn completed_n_publishes_basic_targets_before_squeeze_in_both_directions(){
        for direction in [Direction::Up,Direction::Down]{
            let (state,_)=formed();let mut n=state.candidate.unwrap();n.direction=direction;
            let mirror=|v:Decimal|if direction==Direction::Up{v}else{Decimal::from(30)-v};
            if direction==Direction::Down{
                n.origin=mirror(n.origin);n.neckline=mirror(n.neckline);n.pullback=mirror(n.pullback);
                n.defense=n.defense.map(mirror);n.box_anchor=n.box_anchor.map(mirror);
            }
            let reflect=|mut b:RuleCandle|{let high=b.high;b.open=mirror(b.open);b.close=mirror(b.close);
                if direction==Direction::Down{b.high=mirror(b.low);b.low=mirror(high);}b};
            let attack=reflect(bar(4,"14","23","13.5","15.5"));
            advance_structure_targets(&mut n,&attack,3).unwrap();assert!(n.targets.is_none());
            advance_structure_targets(&mut n,&attack,4).unwrap();
            let targets=n.targets.as_ref().unwrap();
            assert_eq!(targets.one_p,mirror(22.into()));assert_eq!(targets.two_t,mirror(28.into()));
            assert_eq!(targets.one_p_reached_at,None);assert_eq!(n.squeeze_index,None);
            assert_eq!((targets.five_top,targets.ten_full),(None,None));
            advance_structure_targets(&mut n,&reflect(bar(5,"16","23","14","17")),5).unwrap();
            assert_eq!(n.targets.as_ref().unwrap().one_p_reached_at,Some(5));
            assert_eq!(n.targets.as_ref().unwrap().two_t_reached_at,None);
            let before=n.targets.clone();
            advance_structure_targets(&mut n,&reflect(bar(6,"17","29","12","12")),6).unwrap();
            assert_eq!(n.targets,before); // 防守先失效，不认当日新达标。
        }
    }
    #[test]
    fn frozen_defense_touch_survives_strict_break_retires(){
        let(mut state,points)=formed();let defense=state.candidate.as_ref().unwrap().defense.unwrap();
        assert_eq!(defense,Decimal::from(13));
        let touch=bar(5,"15","16","13","15");
        assert!(!advance_n(&mut state,&points,&touch,5,None,|_|None).unwrap().invalidated);
        let wick=bar(6,"14","15","12.9","14");
        assert!(!advance_n(&mut state,&points,&wick,6,None,|_|None).unwrap().invalidated);
        let broken=bar(7,"14","15","12.9","12.9");
        assert!(advance_n(&mut state,&points,&broken,7,None,|_|None).unwrap().invalidated);
        assert!(state.candidate.is_none());
    }
    #[test]
    fn same_day_cannot_evaluate_a_second_n(){
        let(mut state,points)=formed();let current=bar(4,"14","16","13.5","15.5");
        assert!(advance_n(&mut state,&points,&current,4,None,|_|None).is_err());
        assert_eq!(state.evaluated_days,2);
    }
    #[test]
    fn source_revision_retires_without_reusing_old_candidate(){
        let(mut state,mut points)=formed();points[2].value=12.1;
        let current=bar(5,"15","16","13.5","15");
        assert!(advance_n(&mut state,&points,&current,5,None,|_|None).unwrap().invalidated);
        assert!(state.candidate.is_none());
    }
    #[test]
    fn new_points_do_not_replace_a_live_n(){
        let(mut state,mut points)=formed();let first=state.candidate.as_ref().unwrap().a.bar_index;
        let mut p=points[1].clone();p.index=4;p.known_index=4;p.value=16.0;points.push(p);
        let mut p=points[2].clone();p.index=5;p.known_index=5;p.value=14.0;points.push(p);
        let current=bar(5,"15","16","14","15");
        advance_n(&mut state,&points,&current,5,None,|_|None).unwrap();
        assert_eq!(state.candidate.as_ref().unwrap().a.bar_index,first);
        assert_eq!(state.formations,1);
    }
}

#[cfg(test)]
mod symmetric_n_tests {
    use super::*;
    use crate::{analysis::kernel::{KernelPointData,PointState},indexed_json::Value};
    fn bar(day:usize,open:&str,high:&str,low:&str,close:&str)->RuleCandle{
        RuleCandle{symbol:"symmetric".into(),timestamp:format!("2026-01-{day:02}"),open:open.parse().unwrap(),high:high.parse().unwrap(),low:low.parse().unwrap(),close:close.parse().unwrap(),
            volume:100.into(),adjustment_factor:Decimal::ONE,raw_is_st:None,raw_trading_active:None}
    }
    #[test]
    fn inverse_n_freezes_upper_defense_and_breaks_only_strictly_above(){
        let points:Vec<_>=[(1,PointKind::H,20.0),(2,PointKind::L,15.0),(3,PointKind::H,18.0)].into_iter().map(|(index,kind,value)|
            KernelPoint::new(KernelPointData{index,kind,value,ordinal:0,known_index:index,level:0,path:0,state:PointState::Confirmed,time:format!("2026-01-{index:02}"),evidence:Value::Null})).collect();
        let mut state=SingleNState::directional(0,Direction::Down);let previous=bar(2,"16","18","15","15.5");let current=bar(3,"17","18","16","17");
        advance_n(&mut state,&points,&current,3,Some(&previous),|_|Some(Decimal::new(155,1))).unwrap();
        let attack=bar(4,"16","16.5","14","14.5");
        assert!(advance_n(&mut state,&points,&attack,4,Some(&current),|_|Some(Decimal::new(155,1))).unwrap().formed);
        assert_eq!(state.candidate.as_ref().unwrap().defense,Some(17.into()));
        assert!(!advance_n(&mut state,&points,&bar(5,"15","17","14","15"),5,None,|_|None).unwrap().invalidated);
        assert!(!advance_n(&mut state,&points,&bar(6,"16","17.1","15","16"),6,None,|_|None).unwrap().invalidated);
        assert!(advance_n(&mut state,&points,&bar(7,"16","17.1","15","17.1"),7,None,|_|None).unwrap().invalidated);
    }
}
