//! 结构结果只保存位置与有限状态；行情由同一股票 bars 数组提供。
use serde::{Serialize,Deserialize};
use super::{analyzer::{AnalysisResponse,TrendAnalyzerState},kernel::{KernelFrame,KernelPoint,PointKind,PointState,Direction,TrendState},trading::RuleCandle};
use rust_decimal::prelude::ToPrimitive;
#[derive(Debug,Clone,Copy,Serialize,Deserialize)]
#[serde(transparent)]
pub struct BarIndex(pub usize);
#[derive(Debug,Clone,Copy,Serialize,Deserialize)]
#[serde(transparent)]
pub struct PointIndex(pub usize);
/// 行情索引与本层点索引使用不同类型；平行列避免重复嵌套坐标。
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct IndexedLevel{
    pub bar_indices:Vec<BarIndex>,pub source_indices:Vec<Option<PointIndex>>,
    pub kinds:Vec<PointKind>,pub known_indices:Vec<BarIndex>,pub ordinals:Vec<usize>,pub paths:Vec<usize>,pub states:Vec<PointState>,
    pub points:Vec<PointIndex>,pub candidate:Option<PointIndex>,pub publication:Vec<PointIndex>,
    pub key:Option<PointIndex>,pub anchor:Option<PointIndex>,pub preceding_start:Option<PointIndex>,
    pub direction:Option<Direction>,pub structure_direction:Option<Direction>,pub state:Option<TrendState>,pub blocked_at:Option<BarIndex>,
}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase")]
pub struct IndexedAnalysis{
    pub schema_version:u32,pub state:TrendAnalyzerState,pub levels:[IndexedLevel;4],pub complete:bool,pub unavailable_rules:Vec<String>,
}
fn same(a:&KernelPoint,b:&KernelPoint)->bool{a.index==b.index&&a.ordinal==b.ordinal&&a.kind==b.kind&&a.path==b.path&&a.known_index==b.known_index&&a.state==b.state}
fn intern<'a>(table:&mut Vec<&'a KernelPoint>,point:&'a KernelPoint)->PointIndex{
    if let Some(i)=table.iter().position(|old|same(old,point)){PointIndex(i)}else{table.push(point);PointIndex(table.len()-1)}
}
impl IndexedAnalysis{
    pub fn from_response(response:&AnalysisResponse)->Self{
        Self::from_frames(response.state.clone(),&response.levels,response.complete,response.unavailable_rules.clone())
    }
    pub fn from_frames(state:TrendAnalyzerState,frames:&[KernelFrame;4],complete:bool,unavailable_rules:Vec<String>)->Self{
        let mut tables:Vec<Vec<&KernelPoint>>=Vec::new();
        let levels=std::array::from_fn(|level|{
            let frame=&frames[level];let mut table:Vec<&KernelPoint>=Vec::new();
            let mut intern=|p|intern(&mut table,p);
            let points=frame.points.iter().map(&mut intern).collect();let candidate=frame.candidate.as_ref().map(&mut intern);
            let publication=frame.publication.iter().map(&mut intern).collect();let key=frame.key.as_ref().map(&mut intern);
            let anchor=frame.anchor.as_ref().map(&mut intern);let preceding_start=frame.preceding_start.as_ref().map(&mut intern);
            let source_indices=table.iter().map(|p|level.checked_sub(1).and_then(|parent|tables[parent].iter().position(|source|source.index==p.index&&source.ordinal==p.ordinal&&source.kind==p.kind&&source.path==p.path)).map(PointIndex)).collect();
            let output=IndexedLevel{bar_indices:table.iter().map(|p|BarIndex(p.index)).collect(),source_indices,
                kinds:table.iter().map(|p|p.kind).collect(),known_indices:table.iter().map(|p|BarIndex(p.known_index)).collect(),
                ordinals:table.iter().map(|p|p.ordinal).collect(),paths:table.iter().map(|p|p.path).collect(),states:table.iter().map(|p|p.state).collect(),
                points,candidate,publication,key,anchor,preceding_start,direction:frame.direction,structure_direction:frame.structure_direction,state:frame.state,blocked_at:frame.blocked_at.map(BarIndex)};
            tables.push(table);output
        });
        Self{schema_version:1,state,levels,complete,unavailable_rules}
    }
    /// 内核直接导出平行列；不先展开 KernelPoint 或递归证明。
    pub(crate) fn from_arena(state:TrendAnalyzerState,frames:&[super::kernel::arena::RefFrame;4],pool:&super::kernel::arena::PointArena,
        bars:&[super::kernel::KernelBar],complete:bool,unavailable_rules:Vec<String>)->Self{
        use std::collections::BTreeMap;
        let mut parent_tables:Vec<BTreeMap<(usize,usize,u8,usize),usize>>=Vec::with_capacity(4);
        let levels=std::array::from_fn(|level|{
            let frame=&frames[level];let mut table=Vec::new();let mut positions=BTreeMap::new();
            let mut intern=|id|{let p=pool.view(id,bars);let key=(p.index,p.ordinal,p.kind as u8,p.path,p.known_index,p.state as u8);
                let next=table.len();let i=*positions.entry(key).or_insert_with(||{table.push(id);next});PointIndex(i)};
            let points=frame.points.iter().copied().map(&mut intern).collect();let candidate=frame.candidate.map(&mut intern);
            let key=frame.key.map(&mut intern);let anchor=frame.anchor.map(&mut intern);let preceding_start=frame.preceding_start.map(&mut intern);
            let mut coordinates=BTreeMap::new();let mut output=IndexedLevel{bar_indices:Vec::with_capacity(table.len()),source_indices:Vec::with_capacity(table.len()),
                kinds:Vec::with_capacity(table.len()),known_indices:Vec::with_capacity(table.len()),ordinals:Vec::with_capacity(table.len()),paths:Vec::with_capacity(table.len()),states:Vec::with_capacity(table.len()),
                points,candidate,publication:Vec::new(),key,anchor,preceding_start,direction:frame.direction,structure_direction:frame.structure_direction,state:frame.state,blocked_at:frame.blocked_at.map(BarIndex)};
            for(i,id)in table.into_iter().enumerate(){let p=pool.view(id,bars);let coordinate=(p.index,p.ordinal,p.kind as u8,p.path);
                coordinates.entry(coordinate).or_insert(i);output.bar_indices.push(BarIndex(p.index));output.kinds.push(p.kind);output.known_indices.push(BarIndex(p.known_index));
                output.ordinals.push(p.ordinal);output.paths.push(p.path);output.states.push(p.state);
                output.source_indices.push(level.checked_sub(1).and_then(|parent|parent_tables[parent].get(&coordinate)).copied().map(PointIndex));
            }
            parent_tables.push(coordinates);output
        });
        Self{schema_version:1,state,levels,complete,unavailable_rules}
    }
    /// 仅为旧图表适配器读取坐标，不识别趋势或重建判定证据。
    pub fn chart_response(&self,bars:&[RuleCandle])->Result<AnalysisResponse,String>{
        if self.schema_version!=1||self.state.current_index!=bars.len(){return Err("indexed structure prefix mismatch".into());}
        let mut frames:Vec<KernelFrame>=Vec::new();
        for(level,data)in self.levels.iter().enumerate(){
            let n=data.bar_indices.len();if [data.kinds.len(),data.known_indices.len(),data.ordinals.len(),data.paths.len(),data.states.len(),data.source_indices.len()].iter().any(|len|*len!=n){return Err("indexed structure columns differ".into());}
            let table=data.bar_indices.iter().enumerate().map(|(i,index)|{
                let bar=bars.get(index.0).ok_or("indexed structure bar out of range")?;
                if data.known_indices[i].0>=bars.len(){return Err("indexed structure confirmation out of range".into());}
                if let Some(source)=data.source_indices[i]{let parent=level.checked_sub(1).ok_or("polyline cannot reference parent")?;if source.0>=self.levels[parent].bar_indices.len(){return Err("indexed parent out of range".into());}}
                let value=if data.kinds[i]==PointKind::H{bar.high}else{bar.low}.to_f64().ok_or("invalid indexed structure price")?;
                Ok(KernelPoint::new(crate::analysis::kernel::KernelPointData{index:index.0,time:bar.timestamp.clone(),value,kind:data.kinds[i],known_index:data.known_indices[i].0,ordinal:data.ordinals[i],path:data.paths[i],state:data.states[i],level:level as u8,evidence:crate::indexed_json::Value::Null}))
            }).collect::<Result<Vec<_>,String>>()?;
            let get=|index:PointIndex|table.get(index.0).cloned().ok_or_else(||"indexed point out of range".to_string());
            let mut frame=KernelFrame::empty(level as u8);frame.points=data.points.iter().map(|i|get(*i)).collect::<Result<_,_>>()?;
            frame.candidate=data.candidate.map(get).transpose()?;frame.publication=data.publication.iter().map(|i|get(*i)).collect::<Result<_,_>>()?;
            frame.key=data.key.map(get).transpose()?;frame.anchor=data.anchor.map(get).transpose()?;frame.preceding_start=data.preceding_start.map(get).transpose()?;
            frame.direction=data.direction;frame.structure_direction=data.structure_direction;frame.state=data.state;frame.blocked_at=data.blocked_at.map(|i|i.0);frames.push(frame);
        }
        Ok(AnalysisResponse{state:self.state.clone(),levels:frames.try_into().map_err(|_|"indexed levels differ")?,last_execution:Vec::new(),cache_status:"indexed".into(),complete:self.complete,unavailable_rules:self.unavailable_rules.clone(),daily_trading:None,structure_cache:Default::default()})
    }
}
