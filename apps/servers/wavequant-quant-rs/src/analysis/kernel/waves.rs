//! trend_level_upgrades.py：共同整波初始边界、组合结束及严格旧 key 突破。
//! 各层只读直接下级已结束点；方向事实不单独授予递归身份。
use super::{Direction, KernelFrame, KernelOptions, KernelPoint, PointKind, PointState, TrendState, classify_prices};
use crate::indexed_json::Value;
use std::collections::BTreeMap;
use super::{KernelBar,arena::{PointArena,PointId,PointView,RefFrame,RefEvent,EventProof,Evidence,Fact,Proof}};

fn slot(kind: PointKind) -> usize { if kind == PointKind::H {0} else {1} }
#[derive(Clone)]
struct RangeExtremes { size: usize, tree: Vec<Option<usize>>, kind: PointKind }
impl RangeExtremes {
    fn reserve(&mut self,length:usize,points:&[PointId],pool:&PointArena,bars:&[KernelBar],processed:usize) {
        if length<=self.size{return;}
        let mut grown=Self::new(length,self.kind);
        for index in 0..processed {if pool.view(points[index],bars).kind==self.kind{grown.insert(index,points,pool,bars);}}
        *self=grown;
    }
    fn new(length: usize, kind: PointKind) -> Self {
        let size = length.max(1).next_power_of_two();
        Self { size, tree:vec![None; size*2], kind }
    }
    fn choose(&self, left: Option<usize>, right: Option<usize>, points: &[PointId],pool:&PointArena,bars:&[KernelBar]) -> Option<usize> {
        match (left,right) {
            (Some(a),Some(b)) => Some(if self.kind.sign()*(pool.view(points[b],bars).value-pool.view(points[a],bars).value)>0.0 { b } else { a }),
            (a,None) => a, (None,b) => b,
        }
    }
    fn insert(&mut self, index: usize, points: &[PointId],pool:&PointArena,bars:&[KernelBar]) {
        let mut cursor = self.size+index; self.tree[cursor] = Some(index); cursor /= 2;
        while cursor>0 { self.tree[cursor] = self.choose(self.tree[cursor*2], self.tree[cursor*2+1],points,pool,bars); cursor/=2; }
    }
    fn query(&self, first: usize, last: usize, points: &[PointId],pool:&PointArena,bars:&[KernelBar]) -> Option<usize> {
        let (mut left,mut right)=(self.size+first,self.size+last);
        let (mut before,mut after)=(None,None);
        while left<right {
            if left%2==1 { before=self.choose(before,self.tree[left],points,pool,bars); left+=1; }
            if right%2==1 { right-=1; after=self.choose(self.tree[right],after,points,pool,bars); }
            left/=2; right/=2;
        }
        self.choose(before,after,points,pool,bars)
    }
}
fn publish(pool:&mut PointArena,bars:&[KernelBar],point:PointId,level:u8,known:usize,rule:&'static str,proof:Proof,
    closed:&mut BTreeMap<(usize,usize),PointId>,events:&mut Vec<RefEvent>,key:Option<PointId>,origin:Option<PointId>){
    let proof=pool.proof(proof);let mut node=pool.nodes[point.0].clone();
    node.level=level;node.known_index=known;node.state=PointState::Reversal;node.source=Some(point);
    node.evidence=Evidence::Published{rule,proof};node.strict_breaks=None;let published=pool.push(node);
    let order=pool.view(point,bars).order();
    if closed.get(&order).is_none_or(|prior|known<pool.view(*prior,bars).known_index){closed.insert(order,published);}
    if rule=="strict_known_trend_key_break"{
        if let Some(retained)=closed.get_mut(&order){let mut node=pool.nodes[retained.0].clone();node.strict_breaks=Some(pool.append_strict(node.strict_breaks,proof));*retained=pool.push(node);}
    }
    events.push(RefEvent{title:rule,known_index:known,point:published,key,origin,proof:EventProof::Shared(proof)});
}
#[derive(Clone)]
struct PathState {
    frame:RefFrame,extremes:[RangeExtremes;2],closed:BTreeMap<(usize,usize),PointId>,
    keys:[BTreeMap<usize,PointId>;2],recent:[Vec<usize>;2],latest:[Option<usize>;2],preceding:Vec<Option<usize>>,
    owned:Option<Direction>,origin:Option<usize>,extreme:Option<usize>,pending_origin:Option<usize>,pending_extreme:Option<usize>,
    marker:[Option<usize>;2],known:usize,body_rejection:Option<(usize,usize,usize)>,latest_upgrade:Option<usize>,
}
impl PathState {
    fn new(level:u8,length:usize)->Self{Self{frame:RefFrame::empty(level),
        extremes:[RangeExtremes::new(length,PointKind::H),RangeExtremes::new(length,PointKind::L)],
        closed:BTreeMap::new(),keys:[BTreeMap::new(),BTreeMap::new()],recent:[Vec::new(),Vec::new()],latest:[None,None],
        preceding:Vec::new(),owned:None,origin:None,extreme:None,pending_origin:None,pending_extreme:None,
        marker:[None,None],known:0,body_rejection:None,latest_upgrade:None}}
}
/// 计数仅覆盖趋势状态机，不把分组、六态扫描误计为已跳过。
#[derive(Default,Clone,Debug,serde::Serialize)]
pub struct TrendScanStatistics{pub calls:usize,pub scanned_points:usize,pub reused_prefix_points:usize,pub checkpoint_resumes:usize,pub cold_starts:usize}
#[derive(Clone)]
struct Checkpoint{position:usize,state:PathState}
#[derive(Default)]
struct PathCursor {source:Vec<PointId>,state:Option<PathState>,result:Option<RefFrame>,checkpoint:Option<Checkpoint>}
impl PathCursor {
    fn advance(&mut self,pool:&mut PointArena,bars:&[KernelBar],source:&[PointId],offset:usize,level:u8,options:&KernelOptions,stats:&mut TrendScanStatistics)->Result<RefFrame,String>{
        stats.calls+=1;
        if options.trend_offset_enabled&&self.source==source{if let Some(frame)=&self.result{stats.reused_prefix_points+=source.len();return Ok(frame.clone());}}
        // 只保存一个偏移前状态；恢复前必须核对整个保留前缀，早期修订不能被偏移隐藏。
        let checkpoint=self.checkpoint.take().filter(|c|options.trend_offset_enabled&&c.position<=offset
            &&c.position<=self.source.len()&&c.position<=source.len()&&self.source[..c.position]==source[..c.position]);
        let (saved,processed)=if options.trend_offset_enabled&&source.starts_with(&self.source)&&self.state.is_some(){
            (self.state.take(),self.source.len())
        }else if let Some(c)=checkpoint.as_ref(){stats.checkpoint_resumes+=1;(Some(c.state.clone()),c.position)}else{(None,0)};
        if saved.is_none(){stats.cold_starts+=1;}
        let checkpoint_at=(options.trend_offset_enabled&&offset>=processed).then_some(offset);
        stats.scanned_points+=source.len()-processed;stats.reused_prefix_points+=processed;
        self.result=None;
        let(frame,state,new_checkpoint)=path_trend(pool,bars,source,level,options,saved,processed,checkpoint_at)?;
        self.source.clear();self.source.extend_from_slice(source);self.state=Some(state);
        self.checkpoint=new_checkpoint.or(checkpoint);self.result=Some(frame.clone());Ok(frame)
    }
}
fn path_trend(pool:&mut PointArena,bars:&[KernelBar],source:&[PointId], level: u8, options: &KernelOptions,saved:Option<PathState>,processed:usize,checkpoint_at:Option<usize>) -> Result<(RefFrame,PathState,Option<Checkpoint>),String> {
    macro_rules! at {($i:expr)=>{pool.view(source[$i],bars)}}
    let PathState{mut frame,mut extremes,mut closed,mut keys,mut recent,mut latest,mut preceding,
        mut owned,mut origin,mut extreme,mut pending_origin,mut pending_extreme,mut marker,
        mut known,mut body_rejection,mut latest_upgrade}=saved.unwrap_or_else(||PathState::new(level,source.len()));
    for tree in &mut extremes{tree.reserve(source.len(),source,pool,bars,processed);}
    preceding.resize(source.len(),None);
    let mut promotions:Vec<super::PromotionReceipt>=Vec::new();
    for id in source {
        let point=pool.view(*id,bars);
        if let Some(raw)=pool.imported(*id).and_then(|point|point.evidence.get("higher_promotions")) {
            let parsed:Vec<super::PromotionReceipt>=crate::indexed_json::from_value(raw.clone()).map_err(|_|"invalid higher promotion receipts")?;
            for receipt in parsed {
                if receipt.target_level!=level || receipt.upper_level<=level || receipt.upper_level>3
                    || receipt.path!=point.path || receipt.attack.order()!=point.order()
                    || receipt.attack.value!=point.value || receipt.attack.kind!=point.kind
                    || receipt.known_index<point.known_index {return Err("promotion does not belong to this actual source path".into());}
                promotions.push(receipt);
            }
        }
    }
    let mut checkpoint=None;
    macro_rules! save_checkpoint {()=>{PathState{frame:frame.clone(),extremes:extremes.clone(),closed:closed.clone(),keys:keys.clone(),recent:recent.clone(),latest,preceding:preceding.clone(),owned,origin,extreme,pending_origin,pending_extreme,marker,known,body_rejection,latest_upgrade}}}
    let mut cursor_position=processed;
    while cursor_position<source.len(){
        let cursor=cursor_position;if checkpoint_at==Some(cursor){checkpoint=Some(Checkpoint{position:cursor,state:save_checkpoint!()});}cursor_position+=1;let attack=at!(cursor);
        known=known.max(attack.known_index);
        let kind=slot(attack.kind); let opposite_kind=1-kind;
        preceding[cursor]=latest[opposite_kind]; latest[kind]=Some(cursor);
        let old_extreme=extreme;
        extremes[kind].insert(cursor,source,pool,bars);
        recent[kind].push(cursor); if recent[kind].len()>2 { recent[kind].remove(0); }
        if owned.is_none() && closed.is_empty() {
            match (pending_origin,pending_extreme) {
                (Some(start),Some(end)) => {
                    if attack.kind==at!(end).kind {
                        if attack.kind.sign()*(attack.value-at!(end).value)>0.0 { pending_extreme=Some(cursor); }
                    } else if attack.kind.sign()*(attack.value-at!(start).value)>0.0 {
                        pending_origin=Some(end); pending_extreme=Some(cursor);
                    }
                }
                _ => if let Some(previous)=preceding[cursor] { pending_origin=Some(previous); pending_extreme=Some(cursor); },
            }
        }
        if owned == Some(Direction::Up) && level > 1 && attack.kind == PointKind::H {
            if let (Some(peak),Some(counter)) = (extreme,recent[1].last().copied()) {
                if peak < counter && counter < cursor && rejects_wick_indices(pool,bars,source[peak],source[counter],source[cursor]) {
                    body_rejection = Some((peak,counter,cursor)); continue;
                }
            }
        }
        if let Some((peak,counter,rebound)) = body_rejection {
            if owned != Some(Direction::Up) { body_rejection = None; }
            else if attack.kind == PointKind::H && attack.value > at!(peak).value {
                if rejects_wick_indices(pool,bars,source[peak],source[counter],source[cursor]) { body_rejection=Some((peak,counter,cursor)); continue; }
                body_rejection=None;
            } else if attack.kind == PointKind::L && cursor > rebound && attack.value < at!(counter).value {
                let prior_origin=origin.map(|i|source[i]);
                let evidence=vec![("anchor",Fact::Point(source[peak])),("counter",Fact::Point(source[counter])),("rebound",Fact::Point(source[rebound])),
                    ("endpoint",Fact::Point(source[cursor])),("available_index",Fact::Number(known)),("direction_after",Fact::Static("down"))];
                publish(pool,bars,source[peak],level,known,"lower_bear_flip_close_rejection_then_lower_low",evidence,
                    &mut closed,&mut frame.events,None,prior_origin);
                let published=*closed.get(&at!(peak).order()).ok_or("missing body reversal")?;
                keys[slot(pool.view(published,bars).kind)].insert(peak,published);
                frame.preceding_start=prior_origin;
                origin=Some(peak); extreme=Some(cursor); owned=Some(Direction::Down); body_rejection=None;
                continue;
            }
            if body_rejection.is_some() { continue; }
        }
        let mut highs=[0.0;2];let mut lows=[0.0;2];
        for(i,p)in recent[0].iter().enumerate(){highs[i]=at!(*p).value;}
        for(i,p)in recent[1].iter().enumerate(){lows[i]=at!(*p).value;}
        let current=classify_prices(&highs[..recent[0].len()],&lows[..recent[1].len()])?;
        let proof_positions=recent[0].iter().chain(&recent[1]);

        if owned.is_none() {
            if let Some(direction)=current {
                if let (Some(start),Some(end))=(pending_origin,pending_extreme) {
                    if at!(end).kind==direction.target() && proof_positions.clone().all(|p|*p>=start)
                        && (level==1 || end>start+1) {
                        let evidence=vec![("origin",Fact::Point(source[start])),("endpoint",Fact::Point(source[end])),("confirmed_by",Fact::Point(source[cursor])),
                            ("direction",Fact::Direction(direction)),("definition",Fact::Points(proof_positions.clone().map(|i|source[*i]).collect())),
                            ("initial_boundary",Fact::Bool(true)),("interior",Fact::OptionalPoint((end>start+1).then(||source[start+1])))];
                        publish(pool,bars,source[start],level,known,"ordinary_initial_whole_wave",evidence,&mut closed,&mut frame.events,None,None);
                        let published=*closed.get(&at!(start).order()).ok_or("missing published origin")?;
                        keys[slot(pool.view(published,bars).kind)].insert(start,published);
                        owned=Some(direction); origin=Some(start); extreme=Some(end);
                        pending_origin=None; pending_extreme=None;
                    }
                }
            }
        }
        if let Some(direction)=owned {
            if attack.kind==direction.target() && extreme.is_none_or(|old| direction.sign()*(attack.value-at!(old).value)>0.0) { extreme=Some(cursor); }
            if let (Some(reversed),Some(peak))=(current,extreme) {
                if reversed!=direction {
                    if let Some(counter)=extremes[slot(reversed.target())].query(peak+1,cursor+1,source,pool,bars) {
                        let old_key=preceding[peak];
                        let ordinary=old_key.is_some_and(|key| at!(key).known_index<at!(counter).index
                            && reversed.sign()*(at!(counter).value-at!(key).value)>0.0);
                        let promotion=promotions.iter().rev().find(|receipt| {
                            if receipt.known_index>known || at!(peak).order()<receipt.attack.order() {return false;}
                            let Some(start)=origin else{return false;};
                            if !(at!(start).order()==receipt.origin.order() && at!(peak).order()==receipt.attack.order()
                                || at!(start).order()>=receipt.attack.order()) {return false;}
                            let first=source.partition_point(|id|pool.view(*id,bars).order()<receipt.attack.order());
                            let high=extremes[0].query(first,cursor+1,source,pool,bars);
                            let low=extremes[1].query(first,cursor+1,source,pool,bars);
                            let ceiling=receipt.origin.value.max(receipt.attack.value);let floor=receipt.origin.value.min(receipt.attack.value);
                            !high.is_some_and(|p|at!(p).value>ceiling) && !low.is_some_and(|p|at!(p).value<floor)
                        });
                        let local_definition=promotion.is_some() && proof_positions.clone().all(|position|*position>=peak);
                        if (local_definition || ordinary || options.ordinary_trend_completion_enabled) && (level==1 || counter>peak+1) {
                            let key=old_key.map(|p|source[p]);
                            let preceding_start=origin.map(|p|source[p]);
                            let evidence=vec![("origin",Fact::Point(source[peak])),("endpoint",Fact::Point(source[counter])),("wave_origin",Fact::OptionalPoint(preceding_start)),
                                ("confirmed_by",Fact::Point(source[cursor])),("definition",Fact::Points(proof_positions.clone().map(|i|source[*i]).collect())),
                                ("direction_before",Fact::Direction(direction)),("direction_after",Fact::Direction(reversed)),("reversal_key",Fact::OptionalPoint(key)),
                                ("higher_trend_promotion",Fact::Legacy(crate::indexed_json::to_value(promotion).map_err(|_|"invalid promotion proof")?)),
                                ("interior",Fact::OptionalPoint((counter>peak+1).then(||source[peak+1])))];
                            publish(pool,bars,source[peak],level,known,"ordinary_whole_wave_reversal",evidence,&mut closed,&mut frame.events,key,preceding_start);
                            let published=*closed.get(&at!(peak).order()).ok_or("missing published reversal")?;
                            keys[slot(pool.view(published,bars).kind)].insert(peak,published);
                            frame.preceding_start=preceding_start;
                            origin=Some(peak); extreme=Some(counter); owned=Some(reversed);
                        }
                    }
                }
            }
        }
        if !options.secondary_reversal_enabled { continue; }
        let attack_direction=if attack.kind==PointKind::H {Direction::Up} else {Direction::Down};
        let mut key=None;
        if owned.is_some_and(|d|d!=attack_direction) {
            if let Some(start)=origin.filter(|p|at!(*p).kind==attack.kind && *p<cursor) {
                key=keys[kind].get(&start).cloned().map(|p|(start,p));
            }
        }
        if key.is_none() {
            let start=marker[kind].filter(|p|*p<cursor).unwrap_or(0);
            key=keys[kind].range(start..cursor).next_back().map(|(p,key)|(*p,key.clone()));
        }
        let Some((key_position,key))=key else { continue; };
        if pool.view(key,bars).known_index<attack.index { marker[kind]=Some(key_position); }
        let sign=attack_direction.sign();
        if sign*(attack.value-pool.view(key,bars).value)<=0.0 || pool.view(key,bars).known_index>=attack.index { continue; }
        let continuing=owned==Some(attack_direction) && origin.is_some();
        let start=if continuing {origin} else if owned.is_some_and(|d|d!=attack_direction) && extreme.is_some() { extreme }
            else { extremes[opposite_kind].query(key_position+1,cursor,source,pool,bars) };
        let Some(start)=start else { continue; };
        if continuing && old_extreme.is_some_and(|p|at!(p).kind==attack.kind && sign*(attack.value-at!(p).value)<=0.0) {continue;}
        let closes_origin=key_position<start && start<cursor && sign*(pool.view(key,bars).value-at!(start).value)>0.0;
        if !continuing && !closes_origin {continue;}
        let previous_start=origin.map(|p|source[p]);
        let evidence=vec![("origin",Fact::Point(source[start])),("broken_key",Fact::Point(key)),("confirmed_by",Fact::Point(source[cursor])),
            ("direction",Fact::Direction(attack_direction)),("available_index",Fact::Number(known)),("role",Fact::Static("origin"))];
        if !continuing{
            publish(pool,bars,source[start],level,known,"strict_known_trend_key_break",evidence,&mut closed,&mut frame.events,Some(key),previous_start);
            let published=*closed.get(&at!(start).order()).ok_or("missing promoted origin")?;
            keys[slot(pool.view(published,bars).kind)].insert(start,published);frame.preceding_start=previous_start;origin=Some(start);
            extreme=extremes[kind].query(start+1,cursor+1,source,pool,bars);
        }else{extreme=Some(cursor);}
        latest_upgrade=Some(pool.proof(vec![("direction",Fact::Direction(attack_direction)),("role",Fact::Static("endpoint")),("available_index",Fact::Number(known)),
            ("confirmation_rule",Fact::Text(format!("level{}_confirmed_{}_breaks_known_level{}_{}",level-1,
                if attack.kind==PointKind::H{"high"}else{"low"},level,if attack.kind==PointKind::H{"last_fall_high"}else{"last_rise_low"}))),
            ("origin",Fact::Point(source[start])),("broken_key",Fact::Point(key)),("confirmed_by",Fact::Point(source[cursor]))]));
        owned=Some(attack_direction);
    }
    let state=PathState{frame:frame.clone(),extremes,closed,keys,recent,latest,preceding,owned,origin,extreme,
        pending_origin,pending_extreme,marker,known,body_rejection,latest_upgrade:latest_upgrade.clone()};
    if checkpoint_at==Some(source.len()){checkpoint=Some(Checkpoint{position:source.len(),state:state.clone()});}
    frame.points=state.closed.values().cloned().collect();
    // 动态尾端保留整波极值，避免显示层另算或重锚；它不授予下一级输入资格。
    frame.candidate=extreme.map(|index|{
        let id=source[index];let mut node=pool.nodes[id.0].clone();node.level=level;node.state=PointState::Developing;node.source=Some(id);node.strict_breaks=None;
        let upgrade=latest_upgrade.filter(|proof|pool.proofs[*proof].iter().any(|(name,fact)|*name=="confirmed_by"&&matches!(fact,Fact::Point(p) if pool.same(*p,id,bars))));
        node.evidence=Evidence::Candidate{origin:origin.map(|p|source[p]),upgrade};pool.push(node)
    });
    frame.direction=owned;
    let annotated=super::structure::six_state_indices(pool,bars,&frame.points,level)?;
    frame.state=annotated.state.or(owned.map(|d|if d==Direction::Up {TrendState::Bull} else {TrendState::Bear}));
    frame.structure_direction=annotated.direction;
    frame.anchor=annotated.anchor;
    frame.key=annotated.key.or_else(||origin.and_then(|p|frame.points.iter().copied().find(|id|pool.view(*id,bars).order()==at!(p).order())));
    // 整波前起点由本函数维护，六态的破位key不能覆盖续算边界。
    frame.contexts=annotated.contexts;frame.events.extend(annotated.events);
    Ok((frame,state,checkpoint))
}

fn same_reference(value:&Value,point:&KernelPoint)->bool {
    value.get("index").and_then(Value::as_u64)==Some(point.index as u64)
        && value.get("ordinal").and_then(Value::as_u64)==Some(point.ordinal as u64)
        && value.get("value").and_then(Value::as_f64)==Some(point.value)
        && value.get("time").and_then(Value::as_str)==Some(point.time.as_str())
        && value.get("kind").and_then(Value::as_str)==Some(if point.kind==PointKind::H {"H"} else {"L"})
}
pub(super) fn rejects_wick(anchor:&KernelPoint,counter:&KernelPoint,rebound:&KernelPoint)->bool{
    let mut pool=PointArena::default();let a=pool.import(anchor);let c=pool.import(counter);let r=pool.import(rebound);
    rejects_wick_indices(&pool,&[],a,c,r)
}

fn rejects_wick_indices(pool:&PointArena,bars:&[KernelBar],anchor_id:PointId,counter_id:PointId,rebound_id:PointId)->bool{
    let a=pool.view(anchor_id,bars);let c=pool.view(counter_id,bars);let r=pool.view(rebound_id,bars);
    if a.kind!=PointKind::H||c.kind!=PointKind::L||r.kind!=PointKind::H||r.value<=a.value||a.level==0
        ||c.level!=a.level||r.level!=a.level||a.path!=c.path||a.path!=r.path{return false;}
    let(Some(close),Some(anchor_close))=(pool.body_close(rebound_id,bars),pool.body_close(anchor_id,bars))else{return false;};
    if close>anchor_close{return false;}
    if let(Some(anchor),Some(counter),Some(rebound))=(pool.imported(anchor_id),pool.imported(counter_id),pool.imported(rebound_id)){
        return anchor.evidence.get("strict_breaks").and_then(Value::as_array).is_some_and(|proofs|proofs.iter().any(|proof|{
            proof.get("direction").and_then(Value::as_str)==Some("down")&&proof.get("role").and_then(Value::as_str)==Some("origin")
                &&proof.get("origin").is_some_and(|p|same_reference(p,anchor))&&proof.get("confirmed_by").is_some_and(|p|same_reference(p,counter))
                &&proof.get("available_index").and_then(Value::as_u64).is_some_and(|known|known<rebound.index as u64)
        }));
    }
    pool.strict_proofs(anchor_id).into_iter().any(|id|{
        let proof=&pool.proofs[id];let has=|name:&str,predicate:&dyn Fn(&Fact)->bool|proof.iter().any(|(key,value)|*key==name&&predicate(value));
        has("direction",&|v|matches!(v,Fact::Direction(Direction::Down)))&&has("role",&|v|matches!(v,Fact::Static("origin")))
            &&has("origin",&|v|matches!(v,Fact::Point(p) if pool.same(*p,anchor_id,bars)))
            &&has("confirmed_by",&|v|matches!(v,Fact::Point(p) if pool.same(*p,counter_id,bars)))
            &&has("available_index",&|v|matches!(v,Fact::Number(i) if *i<r.index))
    })
}

pub fn trend(points:&[KernelPoint],level:u8,asof:usize,options:&KernelOptions)->Result<KernelFrame,String>{
    TrendCursor::default().advance(points,level,asof,options)
}
/// 各级只持有来源点号及本路径状态，算法与证明类型完全共用。
#[derive(Default)]
pub struct TrendCursor {
    paths:BTreeMap<usize,PathCursor>,identity:Option<(u8,KernelOptions)>,
    legacy_arena:PointArena,legacy_source:Vec<PointId>,external:bool,statistics:TrendScanStatistics,
}
impl TrendCursor {
    pub fn advance_from(&mut self,points:&[KernelPoint],offset:usize,level:u8,asof:usize,options:&KernelOptions)->Result<KernelFrame,String>{
        if offset>points.len(){return Err("trend array offset exceeds the parent data array".into());}
        self.advance_with_offset(points,offset,level,asof,options)
    }
    pub fn statistics(&self)->&TrendScanStatistics{&self.statistics}
    pub fn advance(&mut self,points:&[KernelPoint],level:u8,asof:usize,options:&KernelOptions)->Result<KernelFrame,String>{self.advance_with_offset(points,0,level,asof,options)}
    fn advance_with_offset(&mut self,points:&[KernelPoint],offset:usize,level:u8,asof:usize,options:&KernelOptions)->Result<KernelFrame,String>{
        if points.len()>250_000{return Err("trend source exceeds 250000 points".into());}
        if self.external{self.paths.clear();self.identity=None;self.external=false;}
        // 兼容输入在边界导入一次；修订时重建，追加只导入新尾部。
        let common=self.legacy_source.iter().zip(points).take_while(|(id,p)|self.legacy_arena.imported(**id)==Some(*p)).count();
        self.legacy_source.truncate(common);
        for p in &points[self.legacy_source.len()..]{self.legacy_source.push(self.legacy_arena.import(p));}
        let mut pool=std::mem::take(&mut self.legacy_arena);let ids=std::mem::take(&mut self.legacy_source);
        let result=self.compute_indices(&mut pool,&[],&ids,offset,level,asof,options);
        let result=result.map(|frame|pool.materialize(&frame,&[]));self.legacy_arena=pool;self.legacy_source=ids;result
    }
    pub(crate) fn advance_indices(&mut self,pool:&mut PointArena,bars:&[KernelBar],points:&[PointId],offset:usize,level:u8,asof:usize,options:&KernelOptions)->Result<RefFrame,String>{
        if offset>points.len(){return Err("trend array offset exceeds the parent data array".into());}
        if !self.external{self.paths.clear();self.identity=None;self.external=true;}
        self.compute_indices(pool,bars,points,offset,level,asof,options)
    }
    fn compute_indices(&mut self,pool:&mut PointArena,bars:&[KernelBar],points:&[PointId],offset:usize,level:u8,asof:usize,options:&KernelOptions)->Result<RefFrame,String>{
        if !(1..=3).contains(&level){return Err("trend level must be 1, 2 or 3".into());}
        if points.len()>250_000{return Err("trend source exceeds 250000 points".into());}
        let mut groups:BTreeMap<usize,Vec<PointId>>=BTreeMap::new();
        let mut offsets:BTreeMap<usize,usize>=BTreeMap::new();
        for (position,id) in points.iter().copied().enumerate(){
            let point=pool.view(id,bars);
            if point.level!=level-1{return Err("trend source must belong to immediately lower level".into());}
            if !point.value.is_finite()||point.value<=0.0||point.known_index<point.index
                ||pool.imported(id).is_some_and(|point|point.time.is_empty()) {return Err("finite positive causal source points required".into());}
            if !point.is_closed()||point.known_index>asof{continue;}
            let group=groups.entry(point.path).or_default();
            if group.last().is_some_and(|id|{let previous=pool.view(*id,bars);previous.order()>=point.order()||previous.known_index>point.known_index}){
                return Err("ordered source points and knowledge clocks required".into());
            }
            if position<offset{*offsets.entry(point.path).or_default()+=1;}
            group.push(id);
        }
        if self.identity.as_ref()!=Some(&(level,options.clone())){self.paths.clear();self.identity=Some((level,options.clone()));}
        self.paths.retain(|path,_|groups.contains_key(path));let mut result=RefFrame::empty(level);
        for(path,group)in groups{
            let mut frame=self.paths.entry(path).or_default().advance(pool,bars,&group,offsets.get(&path).copied().unwrap_or(0),level,options,&mut self.statistics)?;
            if let(Some(left),Some(right))=(result.points.last().copied(),frame.points.first().copied()){
                if let Some(bridges)=continuity_bridge(pool,bars,left,right,points,level){
                    let path=pool.view(left,bars).path;result.points.extend(bridges);
                    for context in &mut frame.contexts{context.path=path;}
                    for id in &mut frame.points{let mut node=pool.nodes[id.0].clone();node.path=path;*id=pool.push(node);}
                    if let Some(id)=frame.candidate.as_mut(){let mut node=pool.nodes[id.0].clone();node.path=path;*id=pool.push(node);}
                }
            }
            result.contexts.extend(frame.contexts);result.points.extend(frame.points);result.events.extend(frame.events);result.candidate=frame.candidate;
            result.direction=frame.direction;result.structure_direction=frame.structure_direction;result.state=frame.state;
            result.key=frame.key;result.anchor=frame.anchor;result.preceding_start=frame.preceding_start;
        }
        Ok(result)
    }
}
fn alternating(left:PointView,right:PointView)->bool{left.kind!=right.kind&&right.kind.sign()*(right.value-left.value)>0.0}
fn continuity_bridge(pool:&mut PointArena,bars:&[KernelBar],left_id:PointId,right_id:PointId,source:&[PointId],level:u8)->Option<Vec<PointId>>{
    let left=pool.view(left_id,bars);let right=pool.view(right_id,bars);if left.order()>=right.order(){return None;}
    let known=left.known_index.max(right.known_index);
    let candidates=source.iter().copied().filter(|id|{let p=pool.view(*id,bars);p.is_closed()&&left.order()<p.order()&&p.order()<right.order()&&p.known_index<=known});
    let mut chosen=Vec::new();
    if left.kind==right.kind{
        let kind=left.kind.opposite();let mut best=None;
        for id in candidates{let candidate=pool.view(id,bars);
            if candidate.kind==kind&&kind.sign()*(candidate.value-left.value)>0.0&&kind.sign()*(candidate.value-right.value)>0.0
                &&best.is_none_or(|old|kind.sign()*(candidate.value-pool.view(old,bars).value)>0.0){best=Some(id);}}
        chosen.push(best?);
    }else if !alternating(left,right){
        let(mut first,mut pair)=(None,None);let mut score=f64::NEG_INFINITY;
        for id in candidates{let candidate=pool.view(id,bars);
            if candidate.kind==right.kind&&alternating(left,candidate){
                if first.is_none_or(|old|right.kind.sign()*(candidate.value-pool.view(old,bars).value)>0.0){first=Some(id);}continue;}
            let Some(initial_id)=first else{continue;};let initial=pool.view(initial_id,bars);
            if candidate.kind!=left.kind||!alternating(initial,candidate)||!alternating(candidate,right){continue;}
            let distance=(initial.value-left.value).abs()+(candidate.value-initial.value).abs()+(right.value-candidate.value).abs();
            if distance>score{score=distance;pair=Some((initial_id,id));}
        }
        let(first,second)=pair?;chosen.extend([first,second]);
    }
    Some(chosen.into_iter().map(|id|{let mut node=pool.nodes[id.0].clone();node.level=level;node.path=left.path;node.state=PointState::Reversal;
        node.known_index=known;node.source=Some(id);node.evidence=Evidence::Bridge{left:left_id,right:right_id};node.strict_breaks=None;pool.push(node)}).collect())
}
