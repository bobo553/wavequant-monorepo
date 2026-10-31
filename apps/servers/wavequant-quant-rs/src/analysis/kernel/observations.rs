//! 在共同趋势扫描中发布当时可知的观察；发展尾点只用于本级事实，不进入上级来源。
use super::{Direction,KernelBar,PointState,TrendState};
use super::arena::{PointArena,PointId,RefFrame,RefEvent,Fact};
use std::collections::BTreeMap;

#[derive(Debug,Clone,Copy)]
pub(crate) struct TrendObservation {
    pub known_index:usize,pub path:usize,pub origin:PointId,pub extreme:PointId,
    pub trend_direction:Direction,pub structure_direction:Option<Direction>,pub state:Option<TrendState>,
    pub key:Option<PointId>,pub anchor:Option<PointId>,
}
#[derive(Debug,Clone,Copy)]
pub(crate) struct SourceRevision{pub known:usize,pub path:usize,pub point:PointId}
/// 严格通道把发展波重新接回更早的整波起点时，内部回撤点不再是该时点的波段边界。
/// 保留失效时钟，避免最终数组后来恢复该点后，把资格错误延长穿过历史修订。
pub(crate) fn source_revisions(pool:&PointArena,bars:&[KernelBar],frame:&RefFrame,lower:&RefFrame)->Vec<SourceRevision>{
    let mut previous:BTreeMap<usize,&TrendObservation>=BTreeMap::new();let mut result=Vec::new();
    for next in &frame.observations{
        if let Some(old)=previous.insert(next.path,next){
            let (old_origin,new_origin,extreme)=(pool.view(old.origin,bars),pool.view(next.origin,bars),pool.view(next.extreme,bars));
            if next.known_index<=old.known_index||next.trend_direction!=old.trend_direction||new_origin.order()>=old_origin.order()
                ||extreme.order()<=old_origin.order(){continue;}
            let Some(point)=frame.points.iter().copied().find(|id|pool.same(*id,old.origin,bars)&&pool.view(*id,bars).known_index<next.known_index)else{continue;};
            let boundary=frame.points.iter().rev().find(|id|{let p=pool.view(**id,bars);p.path==next.path&&p.order()<old_origin.order()&&p.kind==extreme.kind});
            if let Some(boundary)=boundary.filter(|id|next.trend_direction.sign()*(extreme.value-pool.view(**id,bars).value)>0.0){
                let boundary=pool.view(*boundary,bars);let floor=pool.view(point,bars).known_index;
                // 最终同向极值可能已替换第一次严格突破端点；结束时钟必须读取
                // 直接下级保存的首次正式 endpoint 证明，不能延迟到最后一个新高。
                let known=lower.promotions.iter().filter_map(|proof|{
                    let facts=&pool.proofs[*proof];
                    let attack=facts.iter().find_map(|(key,f)|if *key=="confirmed_by"{if let Fact::Point(id)=f{Some(pool.view(*id,bars))}else{None}}else{None})?;
                    let clock=facts.iter().find_map(|(key,f)|if *key=="available_index"{if let Fact::Number(n)=f{Some(*n)}else{None}}else{None})?;
                    (clock>floor&&clock<=next.known_index&&attack.path==old_origin.path&&attack.kind==extreme.kind
                        &&old_origin.order()<attack.order()&&attack.order()<=extreme.order()&&boundary.known_index<attack.index
                        &&next.trend_direction.sign()*(attack.value-boundary.value)>0.0).then_some(clock)
                }).min().unwrap_or(next.known_index);
                result.push(SourceRevision{known,path:next.path,point});
            }
        }
    }
    result.sort_by_key(|revision|revision.known);result
}
fn same_optional(pool:&PointArena,bars:&[KernelBar],a:Option<PointId>,b:Option<PointId>)->bool {
    match(a,b){(Some(a),Some(b))=>pool.same(a,b,bars),(None,None)=>true,_=>false}
}
type EventPoint=(usize,usize,u8,u64,String);
fn event_point(pool:&PointArena,bars:&[KernelBar],id:PointId)->EventPoint{
    let point=pool.view(id,bars);(point.index,point.ordinal,point.kind as u8,point.value.to_bits(),pool.time(id,bars).to_owned())
}
/// 稳定前缀只推进新增点；尾端试算从该端之前的状态恢复，不发布给上一级。
#[derive(Clone,Default)]
pub(super) struct ObservationCursor {
    closed:super::structure::SixStateCursor,dirty:Option<(usize,usize)>,visible_closed:usize,
    seen:std::collections::BTreeSet<(&'static str,EventPoint,Option<EventPoint>)>,
    upgrades:BTreeMap<(usize,usize),usize>,
}
impl ObservationCursor {
    pub(super) fn changed(&mut self,order:(usize,usize)){self.dirty=Some(self.dirty.map_or(order,|old|old.min(order)));}
    fn remember_upgrade(&mut self,pool:&PointArena,bars:&[KernelBar],upgrade:Option<usize>,known:usize){
        let Some(upgrade)=upgrade else{return;};let Some(proof)=pool.proofs.get(upgrade)else{return;};
        if !proof.iter().any(|(key,fact)|*key=="role"&&matches!(fact,Fact::Static("endpoint")))
            ||!proof.iter().any(|(key,fact)|*key=="available_index"&&matches!(fact,Fact::Number(index) if *index<=known)){return;}
        let Some(endpoint)=proof.iter().find_map(|(key,fact)|if *key=="confirmed_by"{if let Fact::Point(id)=fact{Some(*id)}else{None}}else{None})else{return;};
        let order=pool.view(endpoint,bars).order();
        if let std::collections::btree_map::Entry::Vacant(entry)=self.upgrades.entry(order){entry.insert(upgrade);self.changed(order);}
    }
    fn attach_upgrade(&self,pool:&mut PointArena,bars:&[KernelBar],id:PointId)->PointId{
        let Some(proof)=self.upgrades.get(&pool.view(id,bars).order())else{return id;};
        let mut node=pool.nodes[id.0];node.strict_breaks=Some(pool.append_strict(node.strict_breaks,*proof));pool.push(node)
    }
    fn sync(&mut self,pool:&mut PointArena,bars:&[KernelBar],closed:&BTreeMap<(usize,usize),PointId>,level:u8)->Result<usize,String>{
        let Some(dirty)=self.dirty.take()else{return Ok(self.closed.points.len());};
        let start=self.closed.points.partition_point(|id|pool.view(*id,bars).order()<dirty);
        let points=closed.range(dirty..).map(|(_,id)|self.attach_upgrade(pool,bars,*id)).collect::<Vec<_>>();
        self.closed.sync_from(pool,bars,start,points.into_iter(),level)?;Ok(start)
    }
    pub(super) fn frame(&self,level:u8)->RefFrame{self.closed.frame(level)}
    fn publish_event(&mut self,pool:&PointArena,bars:&[KernelBar],mut event:RefEvent,known:usize,frame:&mut RefFrame){
        if event.known_index>known{return;}
        let identity=(event.title,event_point(pool,bars,event.point),event.key.map(|key|event_point(pool,bars,key)));
        if self.seen.insert(identity){event.known_index=known;frame.observed_events.push(event);}
    }
}
/// 段六态在共同趋势扫描中形成，N 与资格直接读取已保存枚举及首次可知时钟。
pub(super) fn observe_path(pool:&mut PointArena,bars:&[KernelBar],source:&[PointId],closed:&BTreeMap<(usize,usize),PointId>,
    origin:Option<usize>,extreme:Option<usize>,direction:Option<Direction>,upgrade:Option<usize>,known:usize,level:u8,cursor:&mut ObservationCursor,frame:&mut RefFrame)->Result<(),String>{
    cursor.remember_upgrade(pool,bars,upgrade,known);
    let changed=cursor.sync(pool,bars,closed,level)?;
    let(Some(origin),Some(extreme),Some(direction))=(origin,extreme,direction)else{return Ok(());};
    let(origin,extreme)=(source[origin],source[extreme]);
    let mut base=closed.len();let tail=pool.view(extreme,bars);
    let append=if let Some(last)=closed.last_key_value().map(|(_,id)|*id){let previous=pool.view(last,bars);
        if tail.order()<=previous.order(){false}
        else if tail.kind==previous.kind{
            if tail.kind.sign()*(tail.value-previous.value)>0.0{base-=1;true}else{false}
        }else{tail.kind.sign()*(tail.value-previous.value)>0.0}
    }else{true};
    let mut state=cursor.closed.before(base);
    let mut observed=if append{
        let mut node=pool.nodes[extreme.0];node.level=level;node.state=PointState::Reversal;node.source=Some(extreme);node.known_index=known;node.strict_breaks=None;
        let id=pool.push(node);let id=cursor.attach_upgrade(pool,bars,id);
        let strict=super::structure::strict_context(pool,bars,id,&cursor.closed.points[..base],level);
        state.advance(pool,bars,id,level,strict)?
    }else{cursor.closed.summary(level)};
    // 只有变化后缀及上次被尾点遮住、此次恢复的端点可能带来未发布事件。
    let start=changed.min(cursor.visible_closed).min(base);
    let events:Vec<_>=cursor.closed.events_from(start,base).cloned().collect();
    for event in events{cursor.publish_event(pool,bars,event,known,frame);}
    if append{for event in std::mem::take(&mut observed.events){cursor.publish_event(pool,bars,event,known,frame);}}
    cursor.visible_closed=base;
    let next=TrendObservation{known_index:known,path:pool.view(origin,bars).path,origin,extreme,trend_direction:direction,
        structure_direction:observed.direction,state:observed.state,key:observed.key,anchor:observed.anchor};
    let repeated=frame.observations.last().is_some_and(|old|old.path==next.path&&old.trend_direction==next.trend_direction
        &&old.structure_direction==next.structure_direction&&old.state==next.state&&pool.same(old.origin,next.origin,bars)
        &&pool.same(old.extreme,next.extreme,bars)&&same_optional(pool,bars,old.key,next.key)&&same_optional(pool,bars,old.anchor,next.anchor));
    if !repeated{frame.observations.push(next);}
    frame.candidate_trend_state=next.state;frame.candidate_state_known=Some(known);Ok(())
}

#[cfg(test)]
mod strict_flip_tests{
    use super::*;
    use super::super::{KernelPoint,KernelPointData,PointKind};
    use crate::indexed_json::Value;
    fn fixture(level:u8,mirror:bool)->(PointArena,Vec<PointId>,BTreeMap<(usize,usize),PointId>,usize){
        let mut pool=PointArena::default();
        let source=[10.0,5.0,12.0,7.0,28.0,10.0,11.5,9.5,12.0,5.2,12.6,6.3].into_iter().enumerate().map(|(index,value)|{
            pool.import(&KernelPoint::new(KernelPointData{index,ordinal:0,known_index:index,level:level-1,path:0,
                kind:if (index%2==0)^mirror{PointKind::H}else{PointKind::L},value:if mirror{40.0-value}else{value},
                time:format!("2020-01-{:02}",index+1),state:PointState::Confirmed,evidence:Value::Null}))
        }).collect::<Vec<_>>();
        let closed=source[..10].iter().map(|id|{let mut node=pool.nodes[id.0];node.level=level;node.source=Some(*id);
            let owned=pool.push(node);(pool.view(owned,&[]).order(),owned)}).collect();
        let proof=pool.proof(vec![("role",Fact::Static("endpoint")),("direction",Fact::Direction(if mirror{Direction::Down}else{Direction::Up})),
            ("origin",Fact::Point(source[9])),("broken_key",Fact::Point(source[8])),("confirmed_by",Fact::Point(source[10])),("available_index",Fact::Number(10))]);
        (pool,source,closed,proof)
    }
    #[test]
    fn a_local_strict_break_cannot_replace_the_frozen_outer_background(){
        for level in 1..=3{for mirror in [false,true]{
            let(mut pool,source,mut closed,proof)=fixture(level,mirror);let mut cursor=ObservationCursor::default();let mut frame=RefFrame::empty(level);
            cursor.changed((0,0));
            let direction=if mirror{Direction::Down}else{Direction::Up};let title=if mirror{"翻多为空"}else{"翻空为多"};
            let(mut expected_pool,expected_source,expected_closed,_)=fixture(level,mirror);
            let mut expected_cursor=ObservationCursor::default();let mut expected=RefFrame::empty(level);expected_cursor.changed((0,0));
            observe_path(&mut expected_pool,&[],&expected_source,&expected_closed,Some(9),Some(10),Some(direction),None,10,level,&mut expected_cursor,&mut expected).unwrap();
            observe_path(&mut pool,&[],&source,&closed,Some(9),Some(10),Some(direction),Some(proof),10,level,&mut cursor,&mut frame).unwrap();
            assert!(!frame.observed_events.iter().any(|event|event.title==title&&pool.view(event.point,&[]).index==10));
            let context=frame.observations.last().unwrap();let before=expected.observations.last().unwrap();
            assert_eq!(context.state,before.state);
            assert_eq!(context.key.map(|id|pool.view(id,&[]).index),before.key.map(|id|expected_pool.view(id,&[]).index));
            let mut node=pool.nodes[source[10].0];node.level=level;node.source=Some(source[10]);let endpoint=pool.push(node);
            closed.insert((10,0),endpoint);cursor.changed((10,0));
            observe_path(&mut pool,&[],&source,&closed,Some(10),Some(11),Some(direction.opposite()),Some(proof),11,level,&mut cursor,&mut frame).unwrap();
            assert!(!frame.observed_events.iter().any(|event|event.title==title&&pool.view(event.point,&[]).index==10));
            assert_eq!(cursor.frame(level).state,before.state);
            assert_eq!(closed.len(),11);assert_eq!(pool.view(*closed.get(&(10,0)).unwrap(),&[]).value,pool.view(source[10],&[]).value);
        }}
    }
    #[test]
    fn revision_uses_the_first_formal_crossing_not_the_final_extension(){
        for level in 1..=3{
            let mut pool=PointArena::default();
            let mut make=|index,known,kind,value|pool.import(&KernelPoint::new(KernelPointData{index,ordinal:0,known_index:known,kind,value,
                level,path:0,time:format!("2020-01-{:02}",index+1),state:PointState::Reversal,evidence:Value::Null}));
            let start=make(1,1,PointKind::L,5.0);let high=make(2,3,PointKind::H,20.0);let low=make(4,5,PointKind::L,8.0);
            let first=make(6,7,PointKind::H,22.0);let last=make(8,9,PointKind::H,25.0);
            let mut frame=RefFrame::empty(level);frame.points=vec![start,high,low];
            let observation=|known,origin,extreme|TrendObservation{known_index:known,path:0,origin,extreme,trend_direction:Direction::Up,
                structure_direction:Some(Direction::Up),state:Some(TrendState::Bull),key:None,anchor:None};
            frame.observations=vec![observation(7,low,first),observation(9,start,last)];
            let mut lower=RefFrame::empty(level-1);
            lower.promotions.push(pool.proof(vec![("confirmed_by",Fact::Point(first)),("available_index",Fact::Number(7))]));
            let revisions=source_revisions(&pool,&[],&frame,&lower);
            assert_eq!(revisions.len(),1);assert_eq!(revisions[0].point,low);assert_eq!(revisions[0].known,7);
            frame.observations[1].origin=low;
            assert!(source_revisions(&pool,&[],&frame,&lower).is_empty());
        }
    }
    #[test]
    fn future_or_foreign_own_key_cannot_supply_a_strict_flip(){
        for level in 1..=3{for invalid in 0..4{
            let(mut pool,source,mut closed,proof)=fixture(level,false);
            if invalid==0{pool.proofs[proof].iter_mut().find(|(name,_)|*name=="available_index").unwrap().1=Fact::Number(11);}
            else if invalid==3{let mut node=pool.nodes[source[8].0];node.path=1;let foreign=pool.push(node);
                pool.proofs[proof].iter_mut().find(|(name,_)|*name=="broken_key").unwrap().1=Fact::Point(foreign);}
            else{let key=closed.get_mut(&(8,0)).unwrap();let mut node=pool.nodes[key.0];
                if invalid==1{node.path=1;}else{node.known_index=10;}*key=pool.push(node);
                if invalid==2{let origin=closed.get_mut(&(9,0)).unwrap();let mut node=pool.nodes[origin.0];node.known_index=10;*origin=pool.push(node);}}
            let mut cursor=ObservationCursor::default();let mut frame=RefFrame::empty(level);cursor.changed((0,0));
            observe_path(&mut pool,&[],&source,&closed,Some(9),Some(10),Some(Direction::Up),Some(proof),10,level,&mut cursor,&mut frame).unwrap();
            assert!(!frame.observed_events.iter().any(|event|event.title=="翻空为多"&&pool.view(event.point,&[]).index==10));
        }}
    }
}
