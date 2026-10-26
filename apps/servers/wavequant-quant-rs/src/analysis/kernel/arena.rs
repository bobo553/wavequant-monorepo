//! 计算期只传递点号；行情、证明和来源各保存一次，兼容对象在边界展开。
use super::{Direction, KernelBar, KernelEvent, KernelFrame, KernelPoint, PointKind, PointState, TrendState};
use crate::indexed_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct PointId(pub usize);
#[derive(Debug, Clone, Copy)]
pub enum PriceRef { Bar(usize), Imported(usize) }
#[derive(Debug, Clone, Copy)]
pub enum Evidence {
    Bar { path_source: Option<&'static str>, teaching_path: Option<usize> },
    Imported(usize),
    Published { rule: &'static str, proof: usize },
    Candidate { origin: Option<PointId>, upgrade: Option<usize> },
    Bridge { left: PointId, right: PointId },
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::{KernelOptions,TrendCursor};
    fn bar_node(pool:&mut PointArena,position:usize,kind:PointKind)->PointId{
        pool.push(PointNode{price:PriceRef::Bar(position),body:PriceRef::Bar(position),ordinal:0,kind,
            known_index:position,level:0,path:0,state:PointState::Confirmed,source:None,
            evidence:Evidence::Bar{path_source:None,teaching_path:None},strict_breaks:None})
    }
    #[test]
    fn prices_are_read_from_the_single_bar_array_and_versions_keep_their_clocks(){
        let mut bars=vec![KernelBar{index:0,time:"2020-01-01".into(),open:10.0,high:12.0,low:8.0,close:11.0}];
        let mut pool=PointArena::default();let low=bar_node(&mut pool,0,PointKind::L);let high=bar_node(&mut pool,0,PointKind::H);
        assert_eq!(pool.view(low,&bars).value,8.0);assert_eq!(pool.view(high,&bars).value,12.0);
        bars[0].high=13.0;assert_eq!(pool.view(high,&bars).value,13.0);
        let mut promoted=pool.nodes[high.0];promoted.level=1;promoted.known_index=3;promoted.source=Some(high);
        let promoted=pool.push(promoted);assert_eq!(pool.view(high,&bars).known_index,0);
        assert_eq!(pool.view(promoted,&bars).known_index,3);assert_eq!(pool.nodes[promoted.0].source,Some(high));
        assert_eq!(pool.view(high,&bars).level,0);
    }
    #[test]
    fn all_three_levels_share_index_core_and_preserve_compatibility_proofs(){
        let values=[8.0,12.0,10.0,15.0,11.0,17.0,9.0,13.0,7.0,11.0,6.0,14.0,10.0,18.0];
        let bars:Vec<_>=values.iter().enumerate().map(|(i,v)|KernelBar{index:i,time:format!("2020-01-{:02}",i+1),
            open:*v,close:*v,high:if i%2==0{v+1.0}else{*v},low:if i%2==0{*v}else{v-1.0}}).collect();
        let mut pool=PointArena::default();let mut source=RefFrame::empty(0);
        source.points=values.iter().enumerate().map(|(i,_)|bar_node(&mut pool,i,if i%2==0{PointKind::L}else{PointKind::H})).collect();
        for level in 1..=3{
            let old=pool.materialize(&source,&bars);let options=KernelOptions::default();
            let expected=super::super::trend(&old.points,level,values.len()-1,&options).unwrap();
            let mut cursor=TrendCursor::default();let offset=source.offset(&pool,&bars).unwrap();
            let result=cursor.advance_indices(&mut pool,&bars,&source.points,offset,level,values.len()-1,&options).unwrap();
            assert_eq!(pool.materialize(&result,&bars),expected);
            for id in &result.points{
                assert_eq!(pool.view(*id,&bars).level,level);
                let lower=pool.nodes[id.0].source.expect("promoted point has lower reference");
                assert_eq!(pool.view(lower,&bars).level,level-1);
            }
            let repeated=cursor.advance_indices(&mut pool,&bars,&source.points,offset,level,values.len()-1,&options).unwrap();
            assert_eq!(pool.materialize(&repeated,&bars),expected);
            source=result;
        }
    }
}
#[derive(Debug, Clone, Copy)]
pub struct PointNode {
    pub price: PriceRef, pub body:PriceRef, pub ordinal: usize, pub kind: PointKind, pub known_index: usize,
    pub level: u8, pub path: usize, pub state: PointState, pub source: Option<PointId>,
    pub evidence: Evidence, pub strict_breaks: Option<usize>,
}
/// 临时标量视图不拥有时间、价格体或证明树。
#[derive(Debug, Clone, Copy)]
pub struct PointView {
    pub index: usize, pub ordinal: usize, pub kind: PointKind, pub value: f64,
    pub known_index: usize, pub level: u8, pub path: usize, pub state: PointState,
}
impl PointView {
    pub fn order(self)->(usize,usize){(self.index,self.ordinal)}
    pub fn is_closed(self)->bool{matches!(self.state,PointState::Confirmed|PointState::Teaching|PointState::Reversal)}
}
#[derive(Debug, Clone)]
pub enum Fact {
    Point(PointId), OptionalPoint(Option<PointId>), Points(Vec<PointId>),
    Direction(Direction), Number(usize), Float(f64), Text(String), Static(&'static str), Bool(bool),
    Legacy(Value),
}
pub type Proof = Vec<(&'static str, Fact)>;
#[derive(Debug, Clone)]
pub enum EventProof {Shared(usize),Inline(Proof)}
#[derive(Debug, Clone)]
pub struct RefEvent {
    pub title: &'static str, pub known_index: usize, pub point: PointId,
    pub key: Option<PointId>, pub origin: Option<PointId>, pub proof: EventProof,
}
#[derive(Debug, Clone, Copy)]
pub struct RefContext { pub known_index:usize,pub path:usize,pub state:Option<TrendState>,pub direction:Option<Direction>,pub key:Option<PointId>,pub anchor:Option<PointId> }
#[derive(Debug, Clone)]
pub struct RefFrame {
    pub level:u8, pub points:Vec<PointId>, pub candidate:Option<PointId>,
    pub segment_states:Vec<Option<TrendState>>,pub candidate_trend_state:Option<TrendState>,pub candidate_state_known:Option<usize>,
    pub direction:Option<Direction>, pub structure_direction:Option<Direction>, pub state:Option<TrendState>,
    pub key:Option<PointId>, pub anchor:Option<PointId>, pub preceding_start:Option<PointId>,
    pub observations:Vec<super::observations::TrendObservation>,pub observed_events:Vec<RefEvent>,pub contexts:Vec<RefContext>, pub events:Vec<RefEvent>, pub blocked_at:Option<usize>, pub issues:Vec<String>,
}
impl RefFrame {
    pub fn empty(level:u8)->Self{Self{level,points:Vec::new(),candidate:None,segment_states:Vec::new(),candidate_trend_state:None,candidate_state_known:None,direction:None,structure_direction:None,
        state:None,key:None,anchor:None,preceding_start:None,observations:Vec::new(),observed_events:Vec::new(),contexts:Vec::new(),events:Vec::new(),blocked_at:None,issues:Vec::new()}}
    pub fn offset(&self,pool:&PointArena,bars:&[KernelBar])->Result<usize,String>{
        let Some(origin)=self.preceding_start else{return Ok(0);};let origin=pool.view(origin,bars);
        self.points.iter().position(|id|{let p=pool.view(*id,bars);p.order()==origin.order()&&p.kind==origin.kind&&p.value==origin.value})
            .ok_or_else(||"preceding trend origin is absent from this level's data array".into())
    }
}
struct StrictBreak {proof:usize,previous:Option<usize>}
#[derive(Default)]
pub struct PointArena {
    pub nodes:Vec<PointNode>, pub proofs:Vec<Proof>, imported:Vec<KernelPoint>, strict_breaks:Vec<StrictBreak>, exports:std::sync::Mutex<BTreeMap<PointId,KernelPoint>>,
}
impl PointArena {
    pub fn push(&mut self,node:PointNode)->PointId{let id=PointId(self.nodes.len());self.nodes.push(node);id}
    pub fn proof(&mut self,proof:Proof)->usize{let id=self.proofs.len();self.proofs.push(proof);id}
    pub fn import(&mut self,point:&KernelPoint)->PointId{
        let base=self.imported.len();self.imported.push(point.clone());
        self.push(PointNode{price:PriceRef::Imported(base),body:PriceRef::Imported(base),ordinal:point.ordinal,kind:point.kind,known_index:point.known_index,
            level:point.level,path:point.path,state:point.state,source:None,evidence:Evidence::Imported(base),strict_breaks:None})
    }
    pub fn imported(&self,id:PointId)->Option<&KernelPoint>{match self.nodes[id.0].evidence{Evidence::Imported(i)=>self.imported.get(i),_=>None}}
    pub fn view(&self,id:PointId,bars:&[KernelBar])->PointView{
        let n=&self.nodes[id.0];let (index,value)=match n.price{
            PriceRef::Bar(i)=>{let b=&bars[i];(b.index,if n.kind==PointKind::H{b.high}else{b.low})},
            PriceRef::Imported(i)=>{let p=&self.imported[i];(p.index,p.value)},
        };
        PointView{index,value,ordinal:n.ordinal,kind:n.kind,known_index:n.known_index,level:n.level,path:n.path,state:n.state}
    }
    pub fn same(&self,a:PointId,b:PointId,bars:&[KernelBar])->bool{
        let same_time=self.time(a,bars)==self.time(b,bars);let a=self.view(a,bars);let b=self.view(b,bars);
        same_time&&a.order()==b.order()&&a.value==b.value&&a.kind==b.kind
    }
    pub fn body_close(&self,id:PointId,bars:&[KernelBar])->Option<f64>{
        let p=self.view(id,bars);if p.kind!=PointKind::H{return None;}
        match self.nodes[id.0].body{
            PriceRef::Bar(i)=>{let b=&bars[i];(b.index==p.index&&b.time==self.time(id,bars)&&p.value==b.high).then_some(b.close)},
            PriceRef::Imported(i)=>{
                let original=&self.imported[i];let body=original.evidence.get("price_body")?;
                if body.get("index")?.as_u64()?!=p.index as u64||body.get("time")?.as_str()?!=original.time{return None;}
                let open=body.get("open")?.as_f64()?;let close=body.get("close")?.as_f64()?;
                let high=body.get("high")?.as_f64()?;let low=body.get("low")?.as_f64()?;
                if [open,close,high,low].iter().any(|p|!p.is_finite()||*p<=0.0)||low>open.min(close)||high<open.max(close)||p.value!=high{return None;}
                Some(close)
            }
        }
    }
    pub fn append_strict(&mut self,previous:Option<usize>,proof:usize)->usize{
        let id=self.strict_breaks.len();self.strict_breaks.push(StrictBreak{proof,previous});id
    }
    pub fn strict_proofs(&self,id:PointId)->Vec<usize>{
        let mut proofs=Vec::new();let mut next=self.nodes[id.0].strict_breaks;
        while let Some(id)=next{let item=&self.strict_breaks[id];proofs.push(item.proof);next=item.previous;}
        proofs.reverse();proofs
    }
    fn body(&self,id:PointId,bars:&[KernelBar])->Value{
        match self.nodes[id.0].body{
            PriceRef::Bar(i)=>{let b=&bars[i];json!({"index":b.index,"time":b.time,"open":b.open,"close":b.close,"high":b.high,"low":b.low})},
            PriceRef::Imported(i)=>self.imported[i].evidence.get("price_body").cloned().unwrap_or(Value::Null),
        }
    }
    pub(super) fn time<'a>(&'a self,id:PointId,bars:&'a[KernelBar])->&'a str{match self.nodes[id.0].price{PriceRef::Bar(i)=>&bars[i].time,PriceRef::Imported(i)=>&self.imported[i].time}}
    pub fn materialize(&self,frame:&RefFrame,bars:&[KernelBar])->KernelFrame{
        let mut cache=BTreeMap::new();let mut get=|id|self.expand(id,bars,&mut cache);
        let mut result=KernelFrame::empty(frame.level);result.points=frame.points.iter().map(|id|get(*id)).collect();
        result.candidate=frame.candidate.map(&mut get);result.key=frame.key.map(&mut get);result.anchor=frame.anchor.map(&mut get);
        result.preceding_start=frame.preceding_start.map(&mut get);result.direction=frame.direction;result.structure_direction=frame.structure_direction;
        result.segment_states=frame.segment_states.clone();result.candidate_trend_state=frame.candidate_trend_state;result.candidate_state_known=frame.candidate_state_known;
        result.state=frame.state;result.blocked_at=frame.blocked_at;result.issues=frame.issues.clone();
        result.events=frame.events.iter().map(|e|KernelEvent{title:e.title.into(),known_index:e.known_index,
            point:self.expand(e.point,bars,&mut cache),key:e.key.map(|id|self.expand(id,bars,&mut cache)),
            origin:e.origin.map(|id|self.expand(id,bars,&mut cache)),evidence:match &e.proof{EventProof::Shared(id)=>self.expand_proof(*id,bars,&mut cache),EventProof::Inline(facts)=>self.expand_facts(facts,bars,&mut cache)}}).collect();result
    }
    fn expand_proof(&self,id:usize,bars:&[KernelBar],cache:&mut BTreeMap<PointId,KernelPoint>)->Value{
        self.expand_facts(&self.proofs[id],bars,cache)
    }
    fn expand_facts(&self,facts:&Proof,bars:&[KernelBar],cache:&mut BTreeMap<PointId,KernelPoint>)->Value{
        facts.iter().map(|(key,fact)|((*key).to_string(),match fact{
            Fact::Point(id)=>json!(self.expand(*id,bars,cache)),Fact::OptionalPoint(id)=>json!(id.map(|id|self.expand(id,bars,cache))),
            Fact::Points(ids)=>json!(ids.iter().map(|id|self.expand(*id,bars,cache)).collect::<Vec<_>>()),
            Fact::Direction(d)=>json!(d),Fact::Number(n)=>json!(n),Fact::Float(v)=>json!(v),Fact::Text(s)=>json!(s),
            Fact::Static(s)=>json!(s),Fact::Bool(b)=>json!(b),Fact::Legacy(v)=>v.clone(),
        })).collect::<crate::indexed_json::Map<_,_>>().into()
    }
    fn expand(&self,id:PointId,bars:&[KernelBar],cache:&mut BTreeMap<PointId,KernelPoint>)->KernelPoint{
        if let Some(point)=cache.get(&id){return point.clone();}
        if let Some(point)=self.exports.lock().ok().and_then(|exports|exports.get(&id).cloned()){cache.insert(id,point.clone());return point;}
        let node=&self.nodes[id.0];let p=self.view(id,bars);
        let source=|cache:&mut BTreeMap<PointId,KernelPoint>|node.source.map(|id|self.expand(id,bars,cache));
        let mut evidence=match &node.evidence{
            Evidence::Imported(i)=>self.imported[*i].evidence.clone(),
            Evidence::Bar{path_source,teaching_path}=>{
                let mut v=json!({"source":"lecture_bar_relation_v1","price_body":self.body(id,bars)});
                if let Some(s)=path_source{v["path_evidence"]=json!({"source":s,"intrabar_order_resolved":false});
                    if let Some(path)=teaching_path{v["path_evidence"]["teaching_path"]=json!(path);}}
                v
            },
            Evidence::Published{rule,proof}=>{
                let proof=self.expand_proof(*proof,bars,cache);let direction=proof.get("direction_after").or_else(||proof.get("direction")).cloned();
                json!({"confirmation_rule":rule,"wave_identity_state":"owned","wave_completion_state":"closed",
                    "wave_direction_after":direction,"source":source(cache),"proof":proof,"price_body":self.body(id,bars)})
            },
            Evidence::Candidate{origin,upgrade}=>{
                let mut v=json!({"wave_identity_state":"owned","wave_completion_state":"open",
                    "whole_origin":origin.map(|id|self.expand(id,bars,cache)),"source":source(cache)});
                if let Some(proof)=upgrade{let proof=self.expand_proof(*proof,bars,cache);
                    v["confirmation_rule"]=proof["confirmation_rule"].clone();v["broken_key"]=proof["broken_key"].clone();v["upgrade_evidence"]=proof;}
                v
            },
            Evidence::Bridge{left,right}=>json!({"confirmation_rule":"cross_path_confirmed_base_extreme","source":source(cache),
                "confirmed_by":[self.expand(*left,bars,cache),self.expand(*right,bars,cache)],"price_body":self.body(id,bars)}),
        };
        if node.strict_breaks.is_some(){evidence["strict_breaks"]=json!(self.strict_proofs(id).into_iter().map(|proof|self.expand_proof(proof,bars,cache)).collect::<Vec<_>>());}
        let point=KernelPoint::new(crate::analysis::kernel::KernelPointData{index:p.index,ordinal:p.ordinal,time:self.time(id,bars).into(),kind:p.kind,value:p.value,
            known_index:p.known_index,level:p.level,path:p.path,state:p.state,evidence});cache.insert(id,point.clone());if let Ok(mut exports)=self.exports.lock(){exports.insert(id,point.clone());}point
    }
}
