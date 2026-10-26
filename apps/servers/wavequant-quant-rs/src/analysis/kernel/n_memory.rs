//! 同一股票/周期/行情版本的共享N表；级别和趋势只持有稳定索引。
use std::{collections::{BTreeMap,BTreeSet},sync::atomic::{AtomicBool,Ordering},time::Instant};
use serde::{Serialize,Deserialize};
use rust_decimal::Decimal;
use super::{KernelBar,KernelPoint,PointKind,Direction as StructureDirection,arena::{PointArena,PointId,RefFrame}};
use crate::analysis::trading::{Direction,RuleCandle,config::TradingDayConfig,single_n::{self,SingleNData,SingleNState,SqueezeState}};
#[derive(Debug,Clone,Copy,PartialEq,Eq,PartialOrd,Ord,Serialize,Deserialize)]
#[serde(transparent)]
pub struct NIndex(pub usize);
#[derive(Debug,Clone,Copy,PartialEq,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct MemoryPoint{pub bar_index:usize,pub ordinal:usize,pub kind:PointKind,pub known_index:usize,pub value:f64}
impl MemoryPoint{
    fn from_point(p:&KernelPoint)->Self{Self{bar_index:p.index,ordinal:p.ordinal,kind:p.kind,known_index:p.known_index,value:p.value}}
    fn read(pool:&PointArena,bars:&[KernelBar],id:PointId)->Self{let p=pool.view(id,bars);Self{bar_index:p.index,ordinal:p.ordinal,kind:p.kind,known_index:p.known_index,value:p.value}}
    fn same(&self,other:&Self)->bool{self.bar_index==other.bar_index&&self.ordinal==other.ordinal&&self.kind==other.kind&&self.value==other.value}
}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct SharedNRecord{
    pub data:SingleNData,pub observed_through:Option<usize>,pub invalidated_index:Option<usize>,pub invalidation_reason:Option<String>,
    /// 仅变化时保留目标快照；历史悬停不能读取最终目标回填过去。
    #[serde(default)] pub target_history:Vec<NTargetSnapshot>,
    /// 历史仅记录使用区间，不增加维护订阅。
    #[serde(default)] pub usages:Vec<NUsage>,
}
#[derive(Debug,Clone,Copy,PartialEq,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct NTargetSnapshot{pub known_index:usize,pub prices:[Option<Decimal>;4],pub reached_at:[Option<usize>;2]}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct NUsage{pub level:u8,pub path:usize,pub origin_index:usize,pub start_index:usize,pub end_index:Option<usize>}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct FirstNRecord{pub trend_origin:MemoryPoint,pub direction:Direction,pub confirmed_index:usize,pub n_index:NIndex}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct PathNMemory{
    pub path:usize,pub generation:usize,pub boundary:Option<MemoryPoint>,pub boundary_known_index:Option<usize>,
    /// 当前上涨/下跌趋势各最多一个候选，失效后由新候选替换；旧记录在共享表中留存。
    pub current_n:[Option<NIndex>;2],pub first_ns:Vec<FirstNRecord>,
}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct LevelNMemory{pub level:u8,pub paths:Vec<PathNMemory>}
/// 资格只读取这些可变事实；ABC等不变数据始终从共享表按索引取。
#[derive(Debug,Clone,Copy,PartialEq,Eq)]
pub(crate) struct NConfirmationSnapshot{pub attack:Option<usize>,pub defense:Option<Decimal>,pub squeeze:Option<usize>,pub regime:Option<crate::analysis::market_state::regime::MarketRegime>}
impl NConfirmationSnapshot{pub(crate) fn of(n:&SingleNData)->Self{Self{attack:n.attack_index,defense:n.defense,squeeze:n.squeeze_index,regime:n.regime}}}
#[derive(Debug,Clone,Copy)]
pub(crate) struct NFact{pub known:usize,pub path:usize,pub change:NChange}
#[derive(Debug,Clone,Copy)]
pub(crate) enum NChange{Put(NIndex,NConfirmationSnapshot),Remove(NIndex)}
type Owner=(usize,usize);
type Identity=(u8,usize,[(usize,usize,usize);3]);
fn identity(n:&SingleNData)->Identity{(u8::from(n.direction==Direction::Down),n.a.path,[&n.a,&n.b,&n.c].map(|p|(p.bar_index,p.ordinal,p.known_index)))}
struct Lease{count:usize,since:usize}
struct Entry{state:SingleNState,squeeze:SqueezeState,processed:Option<usize>,invalidated:Option<usize>,reason:Option<String>,owners:BTreeMap<Owner,Lease>,target_history:Vec<NTargetSnapshot>,usages:Vec<NUsage>}
impl Entry{fn data(&self)->&SingleNData{match self.state.candidate.as_ref().or(self.state.retired.as_ref()){Some(data)=>data,None=>unreachable!("registered N lost both live and retired data")}}}
#[derive(Default)]
struct NStore{entries:Vec<Entry>,indices:BTreeMap<Identity,NIndex>,watched:BTreeSet<NIndex>,pending:Vec<(usize,NFact)>}
impl NStore{
    fn valid(&self,id:NIndex)->bool{self.entries[id.0].invalidated.is_none()}
    fn data(&self,id:NIndex)->&SingleNData{self.entries[id.0].data()}
    fn send(&mut self,id:NIndex,now:usize,change:NChange){if self.data(id).direction!=Direction::Up{return;}for (&(level,path),lease) in &self.entries[id.0].owners{self.pending.push((level,NFact{known:now.max(lease.since),path,change}));}}
    fn link(&mut self,id:NIndex,owner:Owner,now:usize){
        let valid=self.valid(id);let positive=self.data(id).direction==Direction::Up;let snapshot=NConfirmationSnapshot::of(self.data(id));let lease=self.entries[id.0].owners.entry(owner).or_insert(Lease{count:0,since:now});lease.count+=1;
        if lease.count==1&&valid&&positive{self.pending.push((owner.0,NFact{known:now,path:owner.1,change:NChange::Put(id,snapshot)}));}
        if valid{self.watched.insert(id);}
    }
    fn note_usage(&mut self,id:NIndex,owner:Owner,origin:usize,now:usize){
        self.entries[id.0].usages.push(NUsage{level:owner.0 as u8,path:owner.1,origin_index:origin,start_index:now,end_index:None});
    }
    fn unlink(&mut self,id:NIndex,owner:Owner,now:usize){
        let positive=self.data(id).direction==Direction::Up;let entry=&mut self.entries[id.0];if let Some(lease)=entry.owners.get_mut(&owner){lease.count-=1;if lease.count==0{entry.owners.remove(&owner);
            if let Some(usage)=entry.usages.iter_mut().rev().find(|u|usize::from(u.level)==owner.0&&u.path==owner.1&&u.end_index.is_none()){usage.end_index=Some(now);}
            if entry.invalidated.is_none()&&positive{self.pending.push((owner.0,NFact{known:now,path:owner.1,change:NChange::Remove(id)}));}}}
        if entry.owners.is_empty(){self.watched.remove(&id);}
    }
    fn invalidate(&mut self,id:NIndex,now:usize,reason:&str){
        if !self.valid(id){return;}let entry=&mut self.entries[id.0];entry.invalidated=Some(now);entry.processed=Some(now);entry.reason=Some(reason.into());
        self.watched.remove(&id);self.send(id,now,NChange::Remove(id));
    }
    fn begin_day(&mut self,sources:&BTreeMap<usize,Source>,bars:&[RuleCandle],now:usize,config:&TradingDayConfig)->Result<(),String>{
        let active:Vec<_>=self.watched.iter().copied().collect();
        for id in active{let path=self.data(id).a.path;let source=sources.get(&path).ok_or("shared N source path missing")?;self.advance(id,source,bars,now,config)?;}
        Ok(())
    }
    fn intern(&mut self,state:SingleNState)->Result<NIndex,String>{
        let n=state.candidate.as_ref().ok_or("shared N requires a formed ABC")?;let key=identity(n);
        if let Some(id)=self.indices.get(&key){return Ok(*id);}
        if self.entries.len()>=250_000{return Err("shared N array exceeds 250000 entries".into());}
        let id=NIndex(self.entries.len());self.entries.push(Entry{state,squeeze:Default::default(),processed:None,invalidated:None,reason:None,owners:BTreeMap::new(),target_history:Vec::new(),usages:Vec::new()});self.indices.insert(key,id);Ok(id)
    }
    fn advance(&mut self,id:NIndex,source:&Source,bars:&[RuleCandle],now:usize,config:&TradingDayConfig)->Result<(),String>{
        if !self.valid(id)||self.entries[id.0].processed==Some(now){return Ok(());}
        let start=self.entries[id.0].processed.map_or(now,|day|day+1);
        for day in start..=now{
            let before=NConfirmationSnapshot::of(self.data(id));let entry=&mut self.entries[id.0];
            let formed=if entry.state.last_day()==Some(day){entry.state.candidate.as_ref().is_some_and(|n|n.attack_index==Some(day))}
                else{let step=single_n::advance_n_indexed(&mut entry.state,&source.points,&source.prices,bars,day)?;
                    if step.invalidated{self.invalidate(id,day,"candidate_defense_or_source_invalidated");return Ok(());}step.formed};
            if formed&&config.strict_n_attack_quality&&self.data(id).direction==Direction::Up&&day>0
                &&crate::analysis::trading::entries::v3_positive_n_attack_rejection(&bars[day],Some(bars[day].volume>bars[day-1].volume)).is_some(){self.invalidate(id,day,"attack_quality_rejected");return Ok(());}
            let entry=&mut self.entries[id.0];let n=entry.state.candidate.as_mut().ok_or("active N lost its data")?;
            if day>0{single_n::advance_squeeze_indexed(&mut entry.squeeze,n,bars,day,config.shadow_fraction)?;}
            entry.processed=Some(day);let after=NConfirmationSnapshot::of(n);
            if let Some(targets)=&n.targets{
                let snapshot=NTargetSnapshot{known_index:day,prices:[Some(targets.one_p),Some(targets.two_t),targets.five_top,targets.ten_full],reached_at:[targets.one_p_reached_at,targets.two_t_reached_at]};
                if entry.target_history.last().is_none_or(|previous|previous.prices!=snapshot.prices||previous.reached_at!=snapshot.reached_at){entry.target_history.push(snapshot);}
            }
            if before!=after{self.send(id,day,NChange::Put(id,after));}
        }Ok(())
    }
    fn finish(self)->Result<Vec<SharedNRecord>,String>{self.entries.into_iter().map(|entry|Ok(SharedNRecord{data:entry.state.candidate.or(entry.state.retired).ok_or("shared N lost historical data")?,observed_through:entry.processed,invalidated_index:entry.invalidated,invalidation_reason:entry.reason,target_history:entry.target_history,usages:entry.usages})).collect()}
}
struct Source{points:Vec<KernelPoint>,prices:Vec<Decimal>}
struct Tracker{origin:MemoryPoint,direction:Direction,search:Option<SingleNState>,current:Option<NIndex>,retained:Option<NIndex>}
#[derive(Default)]
struct PathState{memory:PathNMemory,tracker:Option<Tracker>,six_state:Option<super::TrendState>}
impl PathState{
    fn end_trend(&mut self,now:usize,level:usize,store:&mut NStore){
        if let Some(id)=self.tracker.as_ref().and_then(|tracker|tracker.current){store.unlink(id,(level,self.memory.path),now);}
        self.tracker=None;self.memory.current_n=[None,None];
    }
    /// 同一翻向周期只启动一次；同向新极值不制造新的N搜索。
    fn observe_flip(&mut self,origin:MemoryPoint,boundary:MemoryPoint,attack:MemoryPoint,direction:Direction,now:usize,level:usize,store:&mut NStore){
        let target=if direction==Direction::Up{PointKind::H}else{PointKind::L};
        if boundary.kind!=target||origin.kind==target||attack.kind!=target
            ||!((boundary.bar_index,boundary.ordinal)<(origin.bar_index,origin.ordinal)&&(origin.bar_index,origin.ordinal)<(attack.bar_index,attack.ordinal))
            ||boundary.known_index>=attack.bar_index
            ||(attack.value-boundary.value)*(if direction==Direction::Up{1.0}else{-1.0})<=0.0
            ||(boundary.value-origin.value)*(if direction==Direction::Up{1.0}else{-1.0})<=0.0{return;}
        if self.tracker.as_ref().is_some_and(|tracker|tracker.direction==direction&&tracker.origin.same(&origin)&&self.memory.boundary.as_ref().is_some_and(|key|key.same(&boundary))){return;}
        let owner=(level,self.memory.path);
        if let Some(id)=self.tracker.as_ref().and_then(|tracker|tracker.current){store.unlink(id,owner,now);}
        // 新关键位清掉本级周期引用；共享表里其他级别的引用不受影响。
        if self.memory.boundary.as_ref().is_none_or(|key|!key.same(&boundary)){self.memory.first_ns.clear();}
        self.memory.generation+=1;self.memory.boundary=Some(boundary);self.memory.boundary_known_index=Some(now);self.memory.current_n=[None,None];
        self.tracker=Some(Tracker{origin,search:Some(SingleNState::from_origin(attack.bar_index,attack.ordinal,direction)),direction,current:None,retained:None});
    }
    fn advance(&mut self,source:&Source,bars:&[RuleCandle],now:usize,level:usize,store:&mut NStore,config:&TradingDayConfig)->Result<(),String>{
        let owner=(level,self.memory.path);let Some(tracker)=&mut self.tracker else{return Ok(());};let direction=tracker.direction;let slot=usize::from(direction==Direction::Down);
        if let Some(id)=tracker.current{
            if !store.valid(id){
                store.unlink(id,owner,now);tracker.current=None;self.memory.current_n[slot]=None;
                // 本趋势只保留第一条仍有效的N；破位后归档，后续新结构再提供替代。
                tracker.retained=None;tracker.search=Some(SingleNState::from_origin(now,0,direction));return Ok(());
            }
        }else{
            let Some(search)=tracker.search.as_mut()else{return Ok(());};
            single_n::advance_n_indexed(search,&source.points,&source.prices,bars,now)?;
            let Some(n)=search.candidate.as_ref()else{return Ok(());};
            if bars[n.c.bar_index.saturating_add(1).min(now)..now].iter().any(|bar|n.direction.sign()*(n.direction.adverse(bar)-n.pullback)<Decimal::ZERO){search.retire(now);return Ok(());}
            let id=store.intern(tracker.search.take().ok_or("N search cursor missing")?)?;
            if !store.valid(id){tracker.search=Some(SingleNState::from_origin(now,0,direction));return Ok(());}
            tracker.current=Some(id);self.memory.current_n[slot]=Some(id);store.link(id,owner,now);store.note_usage(id,owner,tracker.origin.bar_index,now);
        }
        let id=tracker.current.ok_or("N candidate index missing")?;store.advance(id,source,bars,now,config)?;
        if !store.valid(id){
            store.unlink(id,owner,now);tracker.current=None;self.memory.current_n[slot]=None;
            tracker.retained=None;tracker.search=Some(SingleNState::from_origin(now,0,direction));return Ok(());
        }
        if store.data(id).squeeze_index.is_some()&&tracker.retained.is_none(){
            self.memory.first_ns.push(FirstNRecord{trend_origin:tracker.origin.clone(),direction,confirmed_index:now,n_index:id});
            // 历史仅保留索引；不增加日常维护订阅。
            tracker.retained=Some(id);
        }
        Ok(())
    }
}
#[derive(Clone,Copy)]
struct FlipSeed{known:usize,path:usize,origin:MemoryPoint,key:MemoryPoint,attack:MemoryPoint,direction:Direction}
struct LevelState{flips:Vec<FlipSeed>,flip_cursor:usize,observations:Vec<super::observations::TrendObservation>,cursor:usize,paths:BTreeMap<usize,PathState>,active_path:Option<usize>}
pub(crate) struct NMemoryAllResult{pub memory:[LevelNMemory;4],pub records:Vec<SharedNRecord>,pub facts:[Vec<NFact>;4],pub seconds:[f64;4],pub shared_seconds:f64}
pub(crate) fn calculate_all(pool:&PointArena,market:&[KernelBar],frames:&[RefFrame;4],scalars:&[super::KernelFrame;4],source_paths:&[BTreeMap<(usize,usize),(usize,usize)>;4],bars:&[RuleCandle],config:&TradingDayConfig,cancel:Option<&AtomicBool>)->Result<NMemoryAllResult,String>{
    let shared=Instant::now();let polyline=&scalars[0];let mut sources:BTreeMap<usize,Source>=BTreeMap::new();
    for point in polyline.points.iter().filter(|p|p.is_closed()){
        let source=sources.entry(point.path).or_insert_with(||Source{points:Vec::new(),prices:Vec::new()});
        source.prices.push(point.value.to_string().parse().map_err(|_|"N source price exceeds decimal range")?);source.points.push(point.clone());
    }
    let mut levels:[LevelState;4]=std::array::from_fn(|level|{
        let mut flips:Vec<_>=scalars[level].events.iter().filter_map(|event|{
            let direction=match event.title.as_str(){"翻空为多"=>Direction::Up,"翻多为空"=>Direction::Down,_=>return None};
            Some(FlipSeed{known:event.known_index,path:event.point.path,origin:MemoryPoint::from_point(event.origin.as_ref()?),key:MemoryPoint::from_point(event.key.as_ref()?),attack:MemoryPoint::from_point(&event.point),direction})
        }).collect();flips.sort_by_key(|seed|seed.known);
        LevelState{flips,flip_cursor:0,observations:frames[level].observations.clone(),cursor:0,paths:BTreeMap::new(),active_path:None}
    });
    for pair in frames[0].points.windows(2){let origin=pool.view(pair[0],market);let extreme=pool.view(pair[1],market);if origin.path!=extreme.path{continue;}
        let context=frames[0].contexts.iter().rev().find(|c|c.path==origin.path&&c.known_index<=extreme.known_index);
        levels[0].observations.push(super::observations::TrendObservation{known_index:extreme.known_index,path:origin.path,origin:pair[0],extreme:pair[1],trend_direction:if extreme.kind==PointKind::H{StructureDirection::Up}else{StructureDirection::Down},structure_direction:context.and_then(|c|c.direction),state:context.and_then(|c|c.state),key:context.and_then(|c|c.key),anchor:context.and_then(|c|c.anchor)});
    }
    for level in &mut levels{level.observations.sort_by_key(|o|o.known_index);}
    let mut shared_seconds=shared.elapsed().as_secs_f64();let mut seconds=[0.0;4];let mut store=NStore::default();let mut facts:[Vec<NFact>;4]=std::array::from_fn(|_|Vec::new());
    for now in 0..bars.len(){
        if cancel.is_some_and(|flag|flag.load(Ordering::Relaxed)){return Err("shared N calculation cancelled".into());}
        let clock=Instant::now();store.begin_day(&sources,bars,now,config)?;shared_seconds+=clock.elapsed().as_secs_f64();
        for level in 0..4{let clock=Instant::now();let state=&mut levels[level];
            while state.cursor<state.observations.len()&&state.observations[state.cursor].known_index<=now{
                let observation=state.observations[state.cursor];state.cursor+=1;if state.active_path!=Some(observation.path){
                    for path in state.paths.values_mut(){path.end_trend(now,level,&mut store);}state.active_path=Some(observation.path);
                }
                let path=state.paths.entry(observation.path).or_default();path.memory.path=observation.path;path.six_state=observation.state;
                let ended=path.tracker.as_ref().is_some_and(|tracker|match tracker.direction{
                    Direction::Up=>matches!(observation.state,Some(super::TrendState::Bear|super::TrendState::BullToBear|super::TrendState::BullBearAlternation)),
                    Direction::Down=>matches!(observation.state,Some(super::TrendState::Bull|super::TrendState::BearToBull|super::TrendState::BearBullAlternation))});
                if ended{path.end_trend(now,level,&mut store);}
            }
            while state.flip_cursor<state.flips.len()&&state.flips[state.flip_cursor].known<=now{
                let seed=state.flips[state.flip_cursor];state.flip_cursor+=1;
                if state.active_path.is_some_and(|path|path!=seed.path){continue;}
                state.active_path=Some(seed.path);let path=state.paths.entry(seed.path).or_default();path.memory.path=seed.path;
                path.observe_flip(seed.origin,seed.key,seed.attack,seed.direction,now,level,&mut store);
            }
            if let Some(path)=state.active_path{if let Some(state)=state.paths.get_mut(&path){
                let ended=state.tracker.as_ref().is_some_and(|tracker|match tracker.direction{
                    Direction::Up=>matches!(state.six_state,Some(super::TrendState::Bear|super::TrendState::BullToBear|super::TrendState::BullBearAlternation)),
                    Direction::Down=>matches!(state.six_state,Some(super::TrendState::Bull|super::TrendState::BearToBull|super::TrendState::BearBullAlternation))});
                if ended{state.end_trend(now,level,&mut store);}if state.tracker.is_some(){
                let base=source_paths[level].range((path,0)..=(path,now)).next_back().map_or(path,|(_,p)|p.1);
                if let Some(source)=sources.get(&base){state.advance(source,bars,now,level,&mut store,config)?;}
            }}}
            seconds[level]+=clock.elapsed().as_secs_f64();
        }
        for (level,fact) in store.pending.drain(..){facts[level].push(fact);}
    }
    let memory=levels.into_iter().enumerate().map(|(level,state)|LevelNMemory{level:level as u8,paths:state.paths.into_values().map(|state|state.memory).collect()}).collect::<Vec<_>>().try_into().map_err(|_|"shared N level count differs")?;
    let records=store.finish()?;Ok(NMemoryAllResult{memory,records,facts,seconds,shared_seconds})
}
#[cfg(test)]
mod tests{
    use super::*;
    fn state()->SingleNState{let mut state=SingleNState::from_origin(0,0,Direction::Up);let point=|source_index,bar_index|single_n::NPointRef{path:0,source_index,bar_index,ordinal:0,known_index:bar_index};
        state.candidate=Some(SingleNData{direction:Direction::Up,a:point(0,1),b:point(1,2),c:point(2,3),origin:10.into(),neckline:15.into(),neckline_close:14.into(),pullback:12.into(),known_index:3,attack_index:Some(4),defense:Some(13.into()),box_anchor:Some(16.into()),squeeze_index:Some(6),regime:None,targets:None});state}
    #[test]
    fn identical_n_is_shared_but_one_level_reset_does_not_remove_another_owner(){
        let mut pool=NStore::default();let one=pool.intern(state()).unwrap();let two=pool.intern(state()).unwrap();assert_eq!(one,two);assert_eq!(pool.entries.len(),1);
        pool.link(one,(1,0),6);pool.note_usage(one,(1,0),0,6);pool.link(two,(2,0),6);pool.note_usage(two,(2,0),0,6);pool.unlink(one,(1,0),7);
        assert_eq!(pool.entries[one.0].usages[0].end_index,Some(7));assert_eq!(pool.entries[one.0].usages[1].end_index,None);assert!(pool.watched.contains(&two));assert!(pool.entries[two.0].owners.contains_key(&(2,0)));
        pool.invalidate(two,8,"squeeze_defense_broken");assert!(!pool.valid(two));assert_eq!(pool.data(two).a.bar_index,1);
    }
    #[test]
    fn path_and_direction_are_part_of_n_identity(){let mut pool=NStore::default();let one=pool.intern(state()).unwrap();let mut down=state();down.candidate.as_mut().unwrap().direction=Direction::Down;
        assert_ne!(one,pool.intern(down).unwrap());let mut other=state();for p in [&mut other.candidate.as_mut().unwrap().a]{p.path=2;}
        assert_ne!(one,pool.intern(other).unwrap());}
}

/// 资格回放完成后才压缩输出表；所有本级引用一起重映射，不删除仍需追溯的失效记录。
pub(crate) fn compact(memory:&mut[LevelNMemory;4],records:&mut Vec<SharedNRecord>)->Result<(),String>{
    let mut used:BTreeSet<_>=memory.iter().flat_map(|level|&level.paths).flat_map(|path|path.current_n.into_iter().flatten().chain(path.first_ns.iter().map(|record|record.n_index))).collect();
    // 已结束趋势的使用区间也需要回看；保留共享行，不复制到各级历史。
    used.extend(records.iter().enumerate().filter(|(_,record)|!record.usages.is_empty()).map(|(index,_)|NIndex(index)));
    if used.iter().any(|id|id.0>=records.len()){return Err("N memory index outside shared output table".into());}
    let mut mapping=vec![None;records.len()];let mut output=Vec::with_capacity(used.len());
    for (index,record) in std::mem::take(records).into_iter().enumerate(){if used.contains(&NIndex(index)){mapping[index]=Some(NIndex(output.len()));output.push(record);}}
    for path in memory.iter_mut().flat_map(|level|&mut level.paths){
        for id in path.current_n.iter_mut().flatten(){*id=mapping[id.0].ok_or("current N remapping missing")?;}
        for record in &mut path.first_ns{record.n_index=mapping[record.n_index.0].ok_or("historical N remapping missing")?;}
    }
    *records=output;Ok(())
}

#[cfg(test)]
mod flip_scope_tests{
    use super::*;
    fn point(bar_index:usize,kind:PointKind,value:f64)->MemoryPoint{MemoryPoint{bar_index,ordinal:0,kind,known_index:bar_index,value}}
    #[test]
    fn same_flip_only_updates_one_search_and_an_opposite_flip_starts_a_new_trend(){
        let mut path=PathState::default();let mut store=NStore::default();
        path.observe_flip(point(2,PointKind::L,10.0),point(0,PointKind::H,15.0),point(4,PointKind::H,18.0),Direction::Up,5,1,&mut store);
        assert_eq!(path.memory.generation,1);
        path.observe_flip(point(2,PointKind::L,10.0),point(0,PointKind::H,15.0),point(6,PointKind::H,20.0),Direction::Up,7,1,&mut store);
        assert_eq!(path.memory.generation,1);
        path.observe_flip(point(8,PointKind::H,22.0),point(6,PointKind::L,16.0),point(10,PointKind::L,14.0),Direction::Down,11,1,&mut store);
        assert_eq!(path.memory.generation,2);assert_eq!(path.tracker.as_ref().unwrap().direction,Direction::Down);
    }
    #[test]
    fn confirmed_first_n_is_archived_after_break_and_waits_for_a_replacement(){
        let mut path=PathState::default();let mut store=NStore::default();path.observe_flip(point(2,PointKind::L,10.0),point(0,PointKind::H,15.0),point(4,PointKind::H,18.0),Direction::Up,5,1,&mut store);
        let reference=|source_index,bar_index|single_n::NPointRef{path:0,source_index,bar_index,ordinal:0,known_index:bar_index};
        let mut candidate=SingleNState::from_origin(4,0,Direction::Up);candidate.candidate=Some(SingleNData{direction:Direction::Up,a:reference(0,6),b:reference(1,7),c:reference(2,8),origin:12.into(),neckline:18.into(),neckline_close:17.into(),pullback:14.into(),known_index:8,attack_index:Some(9),defense:Some(15.into()),box_anchor:Some(19.into()),squeeze_index:Some(11),regime:None,targets:None});
        let id=store.intern(candidate).unwrap();store.link(id,(1,0),11);path.tracker.as_mut().unwrap().current=Some(id);path.tracker.as_mut().unwrap().retained=Some(id);path.memory.current_n[0]=Some(id);
        path.memory.first_ns.push(FirstNRecord{trend_origin:point(2,PointKind::L,10.0),direction:Direction::Up,confirmed_index:11,n_index:id});
        store.invalidate(id,12,"squeeze_defense_broken");path.advance(&Source{points:Vec::new(),prices:Vec::new()},&[],12,1,&mut store,&TradingDayConfig::lecture()).unwrap();
        assert!(path.tracker.as_ref().unwrap().search.is_some());assert_eq!(path.memory.current_n[0],None);assert_eq!(path.memory.first_ns[0].n_index,id);assert!(store.watched.is_empty());
    }
}
