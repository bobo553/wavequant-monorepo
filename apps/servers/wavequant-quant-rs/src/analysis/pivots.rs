//! 旧严格折线与已确认分形代理的因果来源；仅产生 level 0，所有上级仍调用统一趋势函数。
use super::kernel::{self,KernelBar,KernelPoint,KernelFrame,PointKind,PointState,Direction};
use crate::indexed_json::json;

fn point(bar:&KernelBar,kind:PointKind,known:usize,path:usize,state:PointState,source:&str)->KernelPoint{
    KernelPoint::new(crate::analysis::kernel::KernelPointData{index:bar.index,ordinal:0,time:bar.time.clone(),kind,value:if kind==PointKind::H{bar.high}else{bar.low},
        known_index:known,level:0,path,state,evidence:json!({"source":source,"available_index":known,"intrabar_order_resolved":false})})
}
fn finish(points:&[KernelPoint],candidate:Option<&KernelPoint>,direction:Option<Direction>,path:usize,blocked:Option<usize>)->Result<KernelFrame,String>{
    let current:Vec<_>=points.iter().filter(|p|p.path==path).cloned().collect();
    let mut frame=kernel::six_state(&current,0)?;frame.points=points.to_vec();frame.candidate=candidate.cloned();frame.direction=direction;frame.blocked_at=blocked;
    if let Some(candidate)=candidate{frame.points.push(candidate.clone());}
    if let Some(index)=blocked{frame.issues.push(format!("{index}: strict containment requires ordered intrabar evidence"));}Ok(frame)
}
/// 只返回当前给定前缀中已经确认的来源点；默认分形宽度为 Python 的两棒。
pub fn pivot_history(bars:&[KernelBar],mode:&str)->Result<Vec<KernelPoint>,String>{
    Ok(pivot_frame(bars,mode,2)?.points.into_iter().filter(KernelPoint::is_closed).collect())
}
pub fn pivot_frame(bars:&[KernelBar],mode:&str,width:usize)->Result<KernelFrame,String>{
    if mode=="lecture_causal"{return kernel::polyline(bars);}
    kernel::validate_bars(bars)?;if width==0{return Err("positive causal pivot width required".into());}
    if bars.is_empty(){return Ok(KernelFrame::empty(0));}
    match mode {
        "strict_polyline"=>strict(bars),
        "confirmed_fractal_proxy"=>fractal(bars,width),
        _=>Err(format!("unknown pivot mode: {mode}")),
    }
}
fn strict(bars:&[KernelBar])->Result<KernelFrame,String>{
    let mut points=vec![];let mut start=0;let mut path=bars[0].index;let mut candidate=None;let mut direction=None;let mut blocked=None;
    for(i,bar)in bars.iter().enumerate(){
        if i==start{
            path=bar.index;let incoming=if bar.close>=bar.open{Direction::Up}else{Direction::Down};
            direction=Some(incoming);candidate=Some(point(bar,incoming.target(),bar.index,path,PointState::Developing,"explicit_seed"));blocked=None;continue;
        }
        let previous=&bars[i-1];let inside=bar.high<previous.high&&bar.low>previous.low;
        let outside=bar.high>previous.high&&bar.low<previous.low;
        if inside||outside{blocked=Some(bar.index);start=i+1;continue;}
        let incoming=direction.ok_or("strict source direction unavailable")?;
        let reverse=if incoming==Direction::Up{bar.low<previous.low||bar.high<previous.high}else{bar.high>previous.high||bar.low>previous.low};
        let old=candidate.as_ref().ok_or("strict source endpoint unavailable")?;
        let kind=if reverse{incoming.target().opposite()}else{old.kind};
        let next=point(bar,kind,bar.index,path,PointState::Developing,"lecture_bar_relation_v1");
        let delta=incoming.sign()*(next.value-old.value);
        if next.kind==old.kind&&delta>0.0{candidate=Some(next);}
        else if next.kind!=old.kind&&delta<0.0{let mut confirmed=old.clone();confirmed.state=PointState::Confirmed;confirmed.known_index=bar.index;
            confirmed.evidence=json!({"source":"lecture_bar_relation_v1","available_index":bar.index,"confirmation_rule":"strict_bar_relation_turn","path_start":path});
            points.push(confirmed);candidate=Some(next);direction=Some(incoming.opposite());}
    }
    finish(&points,candidate.as_ref(),direction,path,blocked)
}
fn fractal(bars:&[KernelBar],width:usize)->Result<KernelFrame,String>{
    let mut points:Vec<KernelPoint>=vec![];
    for i in 0..bars.len(){if i<width.saturating_mul(2){continue;}let j=i-width;
        let low=(j-width..=i).filter(|position|*position!=j).all(|position|bars[j].low<bars[position].low);
        let high=(j-width..=i).filter(|position|*position!=j).all(|position|bars[j].high>bars[position].high);
        if low==high{continue;}let kind=if low{PointKind::L}else{PointKind::H};
        let next=point(&bars[j],kind,bars[i].index,0,PointState::Confirmed,"causal_fractal_proxy_not_lecture_polyline");
        if let Some(last)=points.last(){if last.kind==kind{if kind.sign()*(next.value-last.value)>0.0{points.pop();points.push(next);}}
            else if kind==PointKind::H&&next.value>last.value||kind==PointKind::L&&next.value<last.value{points.push(next);}}
        else{points.push(next);}
    }
    let mut frame=kernel::six_state(&points,0)?;frame.points=points;Ok(frame)
}
/// 历史快照分别持有其当时的已知坐标，未来同型极点替换不修改过去快照。
pub fn pivot_snapshots(bars:&[KernelBar],mode:&str,width:usize)->Result<Vec<KernelFrame>,String>{
    (0..bars.len()).map(|end|pivot_frame(&bars[..=end],mode,width)).collect()
}
