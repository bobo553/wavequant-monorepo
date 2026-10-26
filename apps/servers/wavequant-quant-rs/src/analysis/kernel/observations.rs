//! 在共同趋势扫描中发布当时可知的观察；发展尾点只用于本级事实，不进入上级来源。
use super::{Direction,KernelBar,PointState,TrendState};
use super::arena::{PointArena,PointId,RefFrame,RefEvent};
use std::collections::BTreeMap;

#[derive(Debug,Clone,Copy)]
pub(crate) struct TrendObservation {
    pub known_index:usize,pub path:usize,pub origin:PointId,pub extreme:PointId,
    pub trend_direction:Direction,pub structure_direction:Option<Direction>,pub state:Option<TrendState>,
    pub key:Option<PointId>,pub anchor:Option<PointId>,
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
}
impl ObservationCursor {
    pub(super) fn changed(&mut self,order:(usize,usize)){self.dirty=Some(self.dirty.map_or(order,|old|old.min(order)));}
    fn sync(&mut self,pool:&mut PointArena,bars:&[KernelBar],closed:&BTreeMap<(usize,usize),PointId>,level:u8)->Result<usize,String>{
        let Some(dirty)=self.dirty.take()else{return Ok(self.closed.points.len());};
        let start=self.closed.points.partition_point(|id|pool.view(*id,bars).order()<dirty);
        self.closed.sync_from(pool,bars,start,closed.range(dirty..).map(|(_,id)|*id),level)?;Ok(start)
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
    origin:Option<usize>,extreme:Option<usize>,direction:Option<Direction>,known:usize,level:u8,cursor:&mut ObservationCursor,frame:&mut RefFrame)->Result<(),String>{
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
        let mut node=pool.nodes[extreme.0];node.level=level;node.state=PointState::Reversal;node.source=Some(extreme);node.known_index=known;
        let id=pool.push(node);state.advance(pool,bars,id,level)?
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
