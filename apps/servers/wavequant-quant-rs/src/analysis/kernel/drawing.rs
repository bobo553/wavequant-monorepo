//! lecture_drawing.py 的包含关系、十字星来向与可知时间规则。
use super::{Direction, KernelBar, KernelFrame, PointKind, PointState, validate_bars};
use super::arena::{PointArena,PointId,PointNode,PriceRef,Evidence,RefFrame};

fn vertex(pool:&mut PointArena,bars:&[KernelBar],position:usize,kind:PointKind,ordinal:usize,state:PointState,known:usize,path:usize)->PointId {
    let _=&bars[position];
    pool.push(PointNode{price:PriceRef::Bar(position),body:PriceRef::Bar(position),ordinal,kind,known_index:known,level:0,path,state,source:None,
        evidence:Evidence::Bar{path_source:None,teaching_path:None},strict_breaks:None})
}
fn outside_doji(pool:&PointArena,bars:&[KernelBar],points:&[PointId],bar:&KernelBar,direction:Option<Direction>)->bool {
    let Some(direction)=direction else{return false;};
    if points.len()<2||bar.open!=bar.close{return false;}
    let anchor=pool.view(points[points.len()-2],bars);let endpoint=pool.view(points[points.len()-1],bars);
    let target=direction.target();
    anchor.kind==target.opposite()&&matches!(anchor.state,PointState::Confirmed|PointState::Teaching)
        &&endpoint.kind==target&&endpoint.state==PointState::Developing
        &&direction.sign()*((if target==PointKind::H{bar.high}else{bar.low})-endpoint.value)>=0.0
}
fn coloured(pool:&mut PointArena,bars:&[KernelBar],position:usize,known:usize,path:usize)->[PointId;2] {
    let bar=&bars[position];let first=if bar.close>bar.open{PointKind::L}else{PointKind::H};
    [vertex(pool,bars,position,first,0,PointState::Developing,known,path),
        vertex(pool,bars,position,first.opposite(),1,PointState::Developing,known,path)]
}
fn archive(pool:&PointArena,bars:&[KernelBar],points:&[PointId],output:&mut Vec<PointId>){
    output.extend(points.iter().copied().filter(|id|pool.view(*id,bars).is_closed()));
}
fn revise(pool:&mut PointArena,id:PointId,change:impl FnOnce(&mut PointNode))->PointId{
    let mut node=pool.nodes[id.0].clone();change(&mut node);pool.push(node)
}
fn path_evidence(pool:&mut PointArena,id:PointId,source:&'static str,teaching_path:Option<usize>)->PointId{
    revise(pool,id,|n|n.evidence=Evidence::Bar{path_source:Some(source),teaching_path})
}

pub fn polyline(bars: &[KernelBar]) -> Result<KernelFrame, String> {
    PolylineCursor::default().advance(bars)
}

/// 保存包含关系的来向、路径起点和活动尾点；追加行情不重放已消费前缀。
pub struct PolylineCursor {
    archived: RefFrame,
    pub(crate) arena:PointArena,
    points: Vec<PointId>,
    direction: Option<Direction>,
    epoch: usize,
    start_position: usize,
    processed: usize,
    last_bar: Option<KernelBar>,
    failed: bool,
}
impl Default for PolylineCursor {
    fn default() -> Self { Self { archived:RefFrame::empty(0),arena:PointArena::default(),points:Vec::new(),direction:None,
        epoch:0,start_position:0,processed:0,last_bar:None,failed:false } }
}
impl PolylineCursor {
    pub fn advance(&mut self,bars:&[KernelBar])->Result<KernelFrame,String> {
        self.advance_index(bars)?;
        self.snapshot(bars)
    }
    pub fn advance_index(&mut self,bars:&[KernelBar])->Result<(),String> {
        if self.failed {return Err("polyline cursor must be rebuilt after a failed transition".into());}
        if bars.len()<self.processed {return Err("polyline cursor cannot consume a truncated prefix".into());}
        if self.processed>0&&bars.get(self.processed-1)!=self.last_bar.as_ref(){return Err("polyline cursor boundary was revised; rebuild from the owned prefix".into());}
        validate_bars(&bars[self.processed.saturating_sub(1)..])?;
        // 失败后禁止从半完成的内部状态继续消费。
        self.failed=true;
        let result=self.consume(bars);
        if result.is_ok(){self.failed=false;}
        result
    }
    fn consume(&mut self,bars:&[KernelBar])->Result<(),String> {
    let Some(first) = bars.first() else { return Ok(()); };
    let mut frame=std::mem::replace(&mut self.archived,RefFrame::empty(0));
    let mut points=std::mem::take(&mut self.points);
    let mut direction=self.direction;
    let mut epoch=if self.processed==0 {first.index}else{self.epoch};
    let mut start_position=self.start_position;
    let pool=&mut self.arena;
    let mut next_position=self.processed.max(1);
    while next_position<bars.len() {
        let position=next_position;next_position+=1;
        let previous = &bars[position-1];
        let bar = &bars[position];
        let inside = bar.high <= previous.high && bar.low >= previous.low
            && (bar.high < previous.high || bar.low > previous.low);
        let outside = bar.high >= previous.high && bar.low <= previous.low
            && (bar.high > previous.high || bar.low < previous.low);
        let mut inside_fallback = false;
        let mut outside_extension = false;
        if inside || outside {
            let mut ordered = Vec::new();
            let mut source = "lecture_containment_colour_convention_not_observed_intrabar_path";
            if previous.open != previous.close && bar.open != bar.close {
                ordered.extend(coloured(pool,bars,position-1,bar.index,epoch));
                ordered.extend(coloured(pool,bars,position,bar.index,epoch));
            } else if outside && previous.open == previous.close && bar.open != bar.close && direction.is_some() && !points.is_empty() {
                let endpoint = pool.view(points[points.len()-1],bars);
                let touched = outside_doji(pool,bars,&points,previous,direction)
                    && pool.view(points[points.len()-2],bars).index >= epoch && endpoint.index < previous.index
                    && endpoint.value == if direction == Some(Direction::Up) { previous.high } else { previous.low };
                if endpoint.index == previous.index || touched {
                    let child=revise(pool,points[points.len()-1],|node|node.price=PriceRef::Bar(position-1));
                    ordered.push(child);
                    ordered.extend(coloured(pool,bars,position,bar.index,epoch));
                    source = "known_child_endpoint_to_coloured_mother";
                }
            }
            if !ordered.is_empty() {
                let mut offset = 0;
                if let Some(last_id)=points.last().copied().filter(|id|pool.view(*id,bars).index==previous.index){
                    let last=pool.view(last_id,bars);
                    if ordered.len()>1&&last.kind==pool.view(ordered[1],bars).kind&&last.value==pool.view(ordered[1],bars).value{offset=2;}
                    else if last.kind==pool.view(ordered[0],bars).kind&&last.value==pool.view(ordered[0],bars).value{offset=1;}
                    else{return Err("current endpoint must match a first-bar extreme".into());}
                    let i=points.len()-1;points[i]=path_evidence(pool,last_id,source,Some(bar.index));
                }
                for next_id in ordered.into_iter().skip(offset){
                    let next_id=revise(pool,next_id,|n|{n.state=PointState::Developing;n.known_index=bar.index;});
                    let next_id=path_evidence(pool,next_id,source,Some(bar.index));let next=pool.view(next_id,bars);
                    if points.is_empty(){points.push(revise(pool,next_id,|n|n.state=PointState::Teaching));continue;}
                    let length=points.len();let last=pool.view(points[length-1],bars);
                    if last.value==next.value{
                        if outside&&next.index==bar.index&&last.index==previous.index{points[length-1]=next_id;}
                        continue;
                    }
                    if length>1&&last.state==PointState::Developing
                        &&(last.value-pool.view(points[length-2],bars).value)*(next.value-last.value)>0.0{
                        points[length-1]=next_id;
                    }else{
                        if last.state==PointState::Developing{points[length-1]=revise(pool,points[length-1],|n|{n.state=PointState::Confirmed;n.known_index=bar.index;});}
                        points.push(next_id);
                    }
                }
                direction = points.last().map(|id| if pool.view(*id,bars).kind == PointKind::H {Direction::Up} else {Direction::Down});
                continue;
            }
            inside_fallback = inside && direction.is_some() && !points.is_empty();
            outside_extension = outside && outside_doji(pool,bars,&points,bar,direction);
            if !inside_fallback && !outside_extension {
                archive(pool,bars,&points,&mut frame.points);
                frame.issues.push(format!("{}: 十字星包含关系的阴阳顺序未定义", bar.index));
                frame.blocked_at = Some(bar.index);
                points.clear();
                direction = None;
                epoch = bar.index;
                start_position = position;
                continue;
            }
        }
        let Some(incoming) = direction else {
            direction = if bar.high > previous.high || bar.low > previous.low {Some(Direction::Up)}
                else if bar.low < previous.low || bar.high < previous.high {Some(Direction::Down)} else {None};
            if let Some(new_direction) = direction {
                points = vec![vertex(pool,bars,start_position, new_direction.target().opposite(), 0, PointState::Seed, bar.index, epoch),
                    vertex(pool,bars,position, new_direction.target(), 0, PointState::Developing, bar.index, epoch)];
                frame.blocked_at = None;
            }
            continue;
        };
        let reverse = !outside_extension && (inside_fallback || if incoming == Direction::Up {
            bar.low < previous.low || bar.high < previous.high
        } else { bar.high > previous.high || bar.low > previous.low });
        let Some(candidate_id) = points.last().copied() else { return Err("direction requires a live endpoint".into()); };
        let candidate=pool.view(candidate_id,bars);
        let mut next = vertex(pool,bars,position, if reverse { incoming.target().opposite() } else { candidate.kind },
            0, PointState::Developing, bar.index, epoch);
        if inside_fallback || outside_extension {
            next=path_evidence(pool,next,if inside_fallback{"known_direction_single_extreme_for_inside_doji"}
                else{"lecture_known_direction_single_extreme_for_outside_doji"},None);
        }
        let delta = incoming.sign() * (pool.view(next,bars).value - candidate.value);
        if !reverse && delta > 0.0 {
            let last = points.len()-1;
            points[last] = next;
        } else if reverse && delta < 0.0 {
            let last = points.len()-1;
            points[last]=revise(pool,points[last],|n|{n.state=PointState::Confirmed;n.known_index=bar.index;});
            points.push(next);
            direction = Some(incoming.opposite());
        }
    }
    self.processed=bars.len();
    self.last_bar=bars.last().cloned();
    self.direction=direction;
    self.epoch=epoch;
    self.start_position=start_position;
    self.archived=frame;
    self.points=points;
    Ok(())
    }
    pub fn snapshot(&mut self,bars:&[KernelBar])->Result<KernelFrame,String>{
        let frame=self.snapshot_indices(bars)?;Ok(self.arena.materialize(&frame,bars))
    }
    pub(crate) fn snapshot_indices(&mut self,bars:&[KernelBar])->Result<RefFrame,String>{
        let pool=&mut self.arena;let mut frame=self.archived.clone();
        archive(pool,bars,&self.points,&mut frame.points);
        frame.candidate=self.points.last().copied().filter(|id|pool.view(*id,bars).state==PointState::Developing);
        frame.direction=self.direction;
        let current:Vec<_>=frame.points.iter().copied().filter(|id|pool.view(*id,bars).path==self.epoch).collect();
        // 六态证明也使用点号，不展开历史点。
        let mut six_state=super::structure::SixStateCursor::default();
        six_state.sync_from(pool,bars,0,current.iter().copied(),0)?;
        let state=six_state.frame(0);
        frame.state=state.state;frame.structure_direction=state.direction;frame.key=state.key;frame.anchor=state.anchor;
        frame.preceding_start=state.preceding_start;
        frame.segment_states=state.segment_states;
        frame.contexts=state.contexts;frame.events=state.events;
        // 已结束路径的资格仍需自己的历史六态时钟；只补只读上下文，不重连几何。
        let mut historical=std::collections::BTreeMap::<usize,Vec<_>>::new();
        for id in &frame.points{let path=pool.view(*id,bars).path;if path!=self.epoch{historical.entry(path).or_default().push(*id);}}
        let mut segment_states=std::collections::BTreeMap::new();
        for (id,state) in current.iter().zip(&frame.segment_states){segment_states.insert(*id,*state);}
        for points in historical.values(){
            let history=super::structure::six_state_indices(pool,bars,points,0)?;
            for (id,state) in points.iter().zip(&history.segment_states){segment_states.insert(*id,*state);}
            frame.contexts.extend(history.contexts);frame.events.extend(history.events);
        }
        frame.segment_states=frame.points.iter().map(|id|segment_states.get(id).copied().flatten()).collect();
        if let Some(id)=frame.candidate{
            let tail=pool.view(id,bars);
            // 发展端尚未构成严格交替入段时保留未知，附加标签不能令合法几何失败。
            let observable=current.last().is_none_or(|last|{let last=pool.view(*last,bars);
                tail.order()>last.order()&&tail.kind!=last.kind&&tail.kind.sign()*(tail.value-last.value)>0.0});
            if observable{
                let mut node=pool.nodes[id.0];node.state=PointState::Reversal;node.known_index=bars.last().map_or(node.known_index,|bar|bar.index);
                let known=node.known_index;let id=pool.push(node);let mut tail=six_state.before(current.len());
                let observed=tail.advance(pool,bars,id,0)?;frame.candidate_trend_state=observed.state;frame.candidate_state_known=Some(known);
            }
        }
        frame.contexts.sort_by_key(|c|c.known_index);frame.events.sort_by_key(|e|e.known_index);
        Ok(frame)
    }
}
